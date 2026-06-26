import os
import pickle
import numpy as np
import pandas as pd
from xgboost import XGBClassifier
from sklearn.utils.class_weight import compute_sample_weight
from sklearn.metrics import f1_score, precision_score, recall_score, classification_report, confusion_matrix
from sklearn.model_selection import TimeSeriesSplit

import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from FEATURE_v2 import load_and_merge, temporal_split, build_xy, fit_feature_stats, transform_features, FEATURE_COLS_V2

def train_and_save():
    DATA_DIR = os.path.dirname(os.path.abspath(__file__))

    print("Step 1: Loading and merging CUR and metrics data...")
    df_merged = load_and_merge(DATA_DIR)
    
    print("Step 2: Splitting dataset chronologically...")
    df_train_raw, df_test_raw = temporal_split(df_merged)
    
    print("Step 3: Extracting features...")
    X_train, y_train, X_test, y_test, train_stats, features = build_xy(
        df_train_raw, df_test_raw
    )
    
    print("Step 4: Walk-Forward CV to estimate performance...")
    tscv = TimeSeriesSplit(n_splits=5)
    cv_scores = []
    cv_val_results = []
    
    for fold, (train_idx, val_idx) in enumerate(tscv.split(df_train_raw)):
        fold_train = df_train_raw.iloc[train_idx].copy()
        fold_val = df_train_raw.iloc[val_idx].copy()
        fold_stats = fit_feature_stats(fold_train)
        X_tr = transform_features(fold_train, fold_stats)
        X_vl = transform_features(fold_val, fold_stats)
        
        feats = [f for f in FEATURE_COLS_V2 if f in X_tr.columns]
        y_tr = X_tr['label_encoded'].copy()
        X_tr = X_tr[feats]
        y_vl = X_vl['label_encoded'].copy()
        X_vl = X_vl[feats]
        
        sw = compute_sample_weight('balanced', y=y_tr)
        clf = XGBClassifier(
            objective='multi:softprob', num_class=3,
            eval_metric='mlogloss',
            learning_rate=0.05, n_estimators=400, max_depth=5,
            subsample=0.8, colsample_bytree=0.8,
            random_state=42, tree_method='hist', n_jobs=-1,
        )
        clf.fit(X_tr, y_tr, sample_weight=sw)
        
        val_proba = clf.predict_proba(X_vl)
        pred = np.argmax(val_proba, axis=1)
        score = f1_score(y_vl, pred, average='macro')
        cv_scores.append(score)
        cv_val_results.append((val_proba, y_vl.values))
        print(f"  Fold {fold+1} F1-macro: {score:.4f}")
        
    print(f"  Average CV F1-macro: {np.mean(cv_scores):.4f}")
    
    print("Step 5: Training final model with augmented features...")
    sample_weights = compute_sample_weight('balanced', y=y_train)
    scale_pos_weight = float((len(y_train) - (y_train == 1).sum()) / max((y_train == 1).sum(), 1))
    
    SENSOR_COLS = ['cpu_mean', 'cpu_std', 'cpu_min', 'cpu_variance_24h',
                   'memory_mib', 'network_in_bytes', 'network_out_bytes', 'disk_io_ops']
    X_aug = X_train.copy()
    rng = np.random.default_rng(seed=42)
    for col in SENSOR_COLS:
        if col in X_aug.columns:
            std = X_aug[col].std()
            if std > 0:
                X_aug[col] += rng.normal(0, 0.03 * std, size=len(X_aug))
                
    X_train_aug = pd.concat([X_train, X_aug], ignore_index=True)
    y_train_aug = pd.concat([y_train, y_train], ignore_index=True)
    sw_aug = np.concatenate([sample_weights, sample_weights])
    
    model = XGBClassifier(
        objective='multi:softprob', num_class=3,
        eval_metric='mlogloss',
        learning_rate=0.05, n_estimators=600, max_depth=5,
        min_child_weight=5, subsample=0.75, colsample_bytree=0.75,
        reg_alpha=0.5, reg_lambda=1.5,
        scale_pos_weight=scale_pos_weight,
        random_state=42, tree_method='hist', n_jobs=-1,
    )
    model.fit(X_train_aug, y_train_aug, sample_weight=sw_aug)
    
    print("Step 6: Optimizing threshold on validation predictions...")
    all_prob = np.concatenate([r[0][:, 1] for r in cv_val_results])
    all_true = np.concatenate([r[1] for r in cv_val_results])
    
    thresholds = np.arange(0.05, 0.95, 0.01)
    best_f1 = -1
    best_threshold = 0.5
    for th in thresholds:
        pred = (all_prob >= th).astype(int)
        score = f1_score(all_true == 1, pred, zero_division=0)
        if score > best_f1:
            best_f1 = score
            best_threshold = th
            
    print(f"  Best Threshold: {best_threshold:.4f} (Validation Anomaly F1: {best_f1:.4f})")
    
    print("Step 7: Evaluating on Test Set...")
    test_prob_all = model.predict_proba(X_test)
    test_prob = test_prob_all[:, 1]
    test_pred = (test_prob >= best_threshold).astype(int)
    
    print("\n-- Test Classification Report (3 classes) --")
    print(classification_report(y_test, model.predict(X_test), digits=4))
    
    cm = confusion_matrix(y_test == 1, test_pred)
    tn, fp, fn, tp = cm.ravel()
    prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    fpr = fp / (fp + tn) if (fp + tn) > 0 else 0.0
    print(f"Test Precision on Anomaly class: {prec:.4f}")
    print(f"Test False Positive Rate (FPR): {fpr:.4f}")
    
    serving_dir = os.path.join(DATA_DIR, "serving_models")
    os.makedirs(serving_dir, exist_ok=True)
    
    model.save_model(os.path.join(serving_dir, "xgboost_anomaly_detector.json"))
    with open(os.path.join(serving_dir, "optimal_threshold.txt"), "w") as f:
        f.write(str(best_threshold))
    with open(os.path.join(serving_dir, "features_list.txt"), "w") as f:
        f.write(",".join(features))
    with open(os.path.join(serving_dir, "train_stats.pkl"), "wb") as f:
        pickle.dump(train_stats, f)
        
    print(f"\nAll assets saved to {serving_dir}")

if __name__ == '__main__':
    train_and_save()
