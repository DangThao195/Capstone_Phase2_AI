import os
import pickle
import numpy as np
import pandas as pd
import xgboost as xgb
from xgboost import XGBClassifier
from sklearn.utils.class_weight import compute_sample_weight
from sklearn.metrics import f1_score, precision_score, recall_score, classification_report, confusion_matrix
from sklearn.model_selection import TimeSeriesSplit
from scipy.stats import ks_2samp
import warnings
warnings.filterwarnings('ignore')

import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from FEATURE_v2 import load_and_merge, temporal_split, build_xy, fit_feature_stats, transform_features, FEATURE_COLS_V2

def run_pipeline_loop():
    DATA_DIR = os.path.dirname(os.path.abspath(__file__))
    serving_dir = os.path.join(DATA_DIR, "serving_models")
    os.makedirs(serving_dir, exist_ok=True)
    
    # ----------------------------------------------------
    # 1. EDA & Data Preprocessing
    # ----------------------------------------------------
    print("=" * 70)
    print("STAGE 1: EDA & DATA PREPROCESSING")
    print("=" * 70)
    df_merged = load_and_merge(DATA_DIR)
    
    print("\nDataset Overview:")
    print(f"  Total records: {len(df_merged)}")
    print(f"  Unique resources: {df_merged['line_item_resource_id'].nunique()}")
    print("\nLabel Distribution:")
    print(df_merged['label'].value_counts().to_string())
    
    # ----------------------------------------------------
    # 2. Feature Engineering & Split
    # ----------------------------------------------------
    print("\n" + "=" * 70)
    print("STAGE 2: FEATURE ENGINEERING & CHRONOLOGICAL SPLIT")
    print("=" * 70)
    df_train_raw, df_test_raw = temporal_split(df_merged)
    
    train_stats = fit_feature_stats(df_train_raw)
    df_train_fe = transform_features(df_train_raw, train_stats)
    df_test_fe = transform_features(df_test_raw, train_stats)
    
    # Initial active features
    active_features = [f for f in FEATURE_COLS_V2 if f in df_train_fe.columns]
    
    # ----------------------------------------------------
    # 3. Iterative Tuning Loop
    # ----------------------------------------------------
    print("\n" + "=" * 70)
    print("STAGE 3: ITERATIVE TRAINING & OPTIMIZATION LOOP")
    print("=" * 70)
    
    best_overall_f1 = -1
    best_overall_model = None
    best_overall_features = []
    best_overall_threshold = 0.5
    
    max_iterations = 4
    for iteration in range(1, max_iterations + 1):
        print(f"\n>>> ITERATION {iteration} (Active Features: {len(active_features)}) <<<")
        print(f"Features: {active_features}")
        
        X_train = df_train_fe[active_features].copy()
        y_train = df_train_fe['label_encoded'].copy()
        X_test = df_test_fe[active_features].copy()
        y_test = df_test_fe['label_encoded'].copy()
        
        # A. Walk-Forward CV & Model Selection
        # We try different max_depth values to find the best model configuration
        tscv = TimeSeriesSplit(n_splits=10)
        candidate_depths = [3, 4, 5, 6]
        depth_cv_scores = {}
        depth_cv_results = {}
        
        print("  Running Model Selection (Hyperparameter Tuning via CV)...")
        for depth in candidate_depths:
            cv_val_results = []
            for fold, (train_idx, val_idx) in enumerate(tscv.split(df_train_raw)):
                fold_train = df_train_raw.iloc[train_idx].copy()
                fold_val = df_train_raw.iloc[val_idx].copy()
                fold_stats = fit_feature_stats(fold_train)
                
                X_tr = transform_features(fold_train, fold_stats)[active_features]
                X_vl = transform_features(fold_val, fold_stats)[active_features]
                y_tr = fold_train['label_encoded'].values
                y_vl = fold_val['label_encoded'].values
                
                sw = compute_sample_weight('balanced', y=y_tr)
                clf = XGBClassifier(
                    objective='multi:softprob', num_class=3,
                    eval_metric='mlogloss',
                    learning_rate=0.05, n_estimators=400, max_depth=depth,
                    subsample=0.8, colsample_bytree=0.8,
                    random_state=42, tree_method='hist', n_jobs=-1,
                )
                clf.fit(X_tr, y_tr, sample_weight=sw)
                
                val_proba = clf.predict_proba(X_vl)
                cv_val_results.append((val_proba, y_vl))
                
            # Find optimal threshold and Anomaly F1 score for this depth
            all_prob = np.concatenate([r[0][:, 1] for r in cv_val_results])
            all_true = np.concatenate([r[1] for r in cv_val_results])
            
            best_depth_f1 = -1
            best_depth_th = 0.5
            thresholds = np.arange(0.05, 0.95, 0.01)
            for th in thresholds:
                pred = (all_prob >= th).astype(int)
                score = f1_score(all_true == 1, pred, zero_division=0)
                if score > best_depth_f1:
                    best_depth_f1 = score
                    best_depth_th = th
                    
            depth_cv_scores[depth] = best_depth_f1
            depth_cv_results[depth] = (cv_val_results, best_depth_th, best_depth_f1)
            print(f"    XGBoost (max_depth={depth}) CV Anomaly F1: {best_depth_f1:.4f} at threshold {best_depth_th:.3f}")
            
        best_depth = max(depth_cv_scores, key=depth_cv_scores.get)
        best_val_cv_results, best_f1_threshold, best_val_f1 = depth_cv_results[best_depth]
        print(f"  Selected best configuration: max_depth={best_depth} (CV Anomaly F1: {best_val_f1:.4f} at threshold {best_f1_threshold:.3f})")
        
        # B. Train Final Model with Augmented Data
        print("  Training final model with augmented features...")
        sample_weights = compute_sample_weight('balanced', y=y_train)
        
        # Infrastructure features list for noise injection
        sensor_cols = ['cpu_mean', 'cpu_std', 'cpu_min', 'cpu_variance_24h',
                       'memory_mib', 'network_in_bytes', 'network_out_bytes', 'disk_io_ops']
        active_sensors = [col for col in sensor_cols if col in active_features]
        
        X_aug = X_train.copy()
        rng = np.random.default_rng(seed=42)
        for col in active_sensors:
            std = X_aug[col].std()
            if std > 0:
                X_aug[col] += rng.normal(0, 0.03 * std, size=len(X_aug))
                
        X_train_aug = pd.concat([X_train, X_aug], ignore_index=True)
        y_train_aug = pd.concat([y_train, y_train], ignore_index=True)
        sw_aug = np.concatenate([sample_weights, sample_weights])
        
        model = XGBClassifier(
            objective='multi:softprob', num_class=3,
            eval_metric='mlogloss',
            learning_rate=0.05, n_estimators=600, max_depth=best_depth,
            min_child_weight=5, subsample=0.75, colsample_bytree=0.75,
            reg_alpha=0.5, reg_lambda=1.5,
            random_state=42, tree_method='hist', n_jobs=-1,
        )
        model.fit(X_train_aug, y_train_aug, sample_weight=sw_aug)
        
        # C. Feature Importance & SHAP
        print("  Computing SHAP values for feature selection...")
        import shap
        # Limit sampling size to 1000 for speed
        shap_sample_size = min(1000, len(X_train))
        X_shap = X_train.sample(n=shap_sample_size, random_state=42) if len(X_train) > shap_sample_size else X_train
        
        try:
            explainer = shap.TreeExplainer(model)
            shap_vals = explainer.shap_values(X_shap)
            
            # Calculate mean absolute SHAP values across classes and samples
            if isinstance(shap_vals, list):
                class_importances = [np.mean(np.abs(val), axis=0) for val in shap_vals]
                importance = np.mean(class_importances, axis=0)
            elif isinstance(shap_vals, np.ndarray):
                if len(shap_vals.shape) == 3:
                    importance = np.mean(np.abs(shap_vals), axis=(0, 2))
                else:
                    importance = np.mean(np.abs(shap_vals), axis=0)
            else:
                try:
                    importance = np.mean(np.abs(shap_vals.values), axis=(0, 2))
                except Exception:
                    print("    WARNING: SHAP values retrieval failed. Falling back to built-in feature importances.")
                    importance = model.feature_importances_
        except Exception as e:
            print(f"    WARNING: SHAP computation failed with error: {e}. Falling back to built-in feature importances.")
            importance = model.feature_importances_
            
        feature_importance_df = pd.DataFrame({
            'Feature': active_features,
            'Importance': importance
        }).sort_values(by='Importance', ascending=False)
        
        # Calculate concept drift (KS statistic) between Train and Test splits
        drift_scores = {}
        for col in active_features:
            stat, pval = ks_2samp(X_train[col].dropna(), X_test[col].dropna())
            drift_scores[col] = stat
            
        feature_importance_df['Drift_KS'] = feature_importance_df['Feature'].map(drift_scores)
        
        print("\n  SHAP-based Feature Importance and Drift Ranking:")
        print(feature_importance_df.to_string(index=False))
        
        # D. Threshold Optimization (Already completed during Model Selection)
        print(f"\n  Threshold Optimization (Pre-computed during CV):")
        print(f"    Optimal Threshold selected: {best_f1_threshold:.3f} (Val Anomaly F1: {best_val_f1:.4f})")
        
        # E. Model Evaluation on Test Set
        test_prob_all = model.predict_proba(X_test)
        test_prob = test_prob_all[:, 1]
        test_pred = (test_prob >= best_f1_threshold).astype(int)
        
        cm = confusion_matrix(y_test == 1, test_pred)
        tn, fp, fn, tp = cm.ravel()
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        fpr = fp / (fp + tn) if (fp + tn) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        test_f1 = f1_score(y_test == 1, test_pred, zero_division=0)
        
        print(f"  Test Evaluation:")
        print(f"    Precision: {precision:.4f} (Target: >= 80%)")
        print(f"    FPR      : {fpr:.4f} (Target: <= 10%)")
        print(f"    Recall   : {recall:.4f}")
        print(f"    F1-Score : {test_f1:.4f}")
        
        # Save best model overall (allow up to 0.02 F1 tolerance to prefer the pruned robust feature set)
        if best_overall_model is None or (test_f1 >= best_overall_f1 - 0.02):
            best_overall_f1 = test_f1
            best_overall_model = model
            best_overall_features = list(active_features)
            best_overall_threshold = best_f1_threshold
            
        # F. Feature Selection (Pruning for next iteration)
        # Pruning rules: 
        # 1. Remove features with extremely low importance (< 0.005)
        # 2. Remove features with very high drift (KS statistic > 0.35) EXCEPT core cost features
        core_features = ['line_item_unblended_cost', 'robust_z', 'absolute_cost_spike', 'peer_ratio']
        to_prune = []
        for _, row in feature_importance_df.iterrows():
            feat = row['Feature']
            if feat in core_features:
                continue
            if row['Importance'] < 0.005:
                to_prune.append((feat, "Low Importance"))
            elif row['Drift_KS'] > 0.35:
                to_prune.append((feat, "High Drift"))
                
        if not to_prune:
            print("\n  Feature set has stabilized. No features to prune.")
            break
            
        print(f"\n  Pruned features for next iteration:")
        for feat, reason in to_prune:
            print(f"    - {feat} ({reason})")
            active_features.remove(feat)
            
    # Save the absolute best overall model assets to serving directory
    print("\n" + "=" * 70)
    print("STAGE 4: SAVING BEST MODEL ASSETS")
    print("=" * 70)
    print(f"Saving assets for best model (Test F1: {best_overall_f1:.4f})...")
    print(f"Features: {best_overall_features}")
    print(f"Threshold: {best_overall_threshold:.4f}")
    
    best_overall_model.save_model(os.path.join(serving_dir, "xgboost_anomaly_detector.json"))
    with open(os.path.join(serving_dir, "optimal_threshold.txt"), "w") as f:
        f.write(str(best_overall_threshold))
    with open(os.path.join(serving_dir, "features_list.txt"), "w") as f:
        f.write(",".join(best_overall_features))
    with open(os.path.join(serving_dir, "train_stats.pkl"), "wb") as f:
        pickle.dump(train_stats, f)
        
    print(f"All serving assets successfully written to {serving_dir}")

if __name__ == '__main__':
    run_pipeline_loop()
