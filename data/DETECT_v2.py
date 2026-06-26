# DETECT_v2.py
# Same pipeline as DETECT.py but uses FEATURE_v2 feature set.
# Run: python DETECT_v2.py  (from data/ directory)
# Compare output with DETECT.py to see if v2 features improve results.

import warnings
import numpy as np
import pandas as pd
import mlflow
import mlflow.xgboost
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from xgboost import XGBClassifier
from sklearn.model_selection import TimeSeriesSplit
from sklearn.utils.class_weight import compute_sample_weight
from sklearn.metrics import (
    f1_score, precision_score, recall_score,
    classification_report, roc_auc_score,
    ConfusionMatrixDisplay, RocCurveDisplay, PrecisionRecallDisplay,
)

from FEATURE_v2 import (
    load_and_merge, temporal_split, build_xy,
    fit_feature_stats, transform_features, FEATURE_COLS_V2,
)

warnings.filterwarnings('ignore')
FEATURE_COLS = FEATURE_COLS_V2


def walk_forward_cv(df_train_raw, n_splits=5):
    print('=' * 65)
    print('STEP 4 - WALK-FORWARD CROSS VALIDATION  [v2]')
    print('=' * 65)

    tscv           = TimeSeriesSplit(n_splits=n_splits)
    cv_scores      = []
    cv_val_results = []

    for fold, (train_idx, val_idx) in enumerate(tscv.split(df_train_raw)):
        fold_train = df_train_raw.iloc[train_idx].copy()
        fold_val   = df_train_raw.iloc[val_idx].copy()

        fold_stats = fit_feature_stats(fold_train)
        X_tr       = transform_features(fold_train, fold_stats)
        X_vl       = transform_features(fold_val,   fold_stats)

        feats = [f for f in FEATURE_COLS if f in X_tr.columns]
        y_tr  = X_tr['label_encoded'].copy();  X_tr = X_tr[feats]
        y_vl  = X_vl['label_encoded'].copy();  X_vl = X_vl[feats]

        sw  = compute_sample_weight('balanced', y=y_tr)
        clf = XGBClassifier(
            objective='multi:softprob', num_class=3,
            eval_metric='mlogloss',
            learning_rate=0.05, n_estimators=400, max_depth=5,
            subsample=0.8, colsample_bytree=0.8,
            random_state=42, tree_method='hist', n_jobs=-1,
        )
        clf.fit(X_tr, y_tr, sample_weight=sw)

        val_proba = clf.predict_proba(X_vl)
        pred      = np.argmax(val_proba, axis=1)
        score     = f1_score(y_vl, pred, average='macro')
        cv_scores.append(score)
        cv_val_results.append((val_proba, y_vl.values))
        print(f'  Fold {fold + 1} F1-macro: {score:.4f}')

    print(f'  CV F1-macro: {np.mean(cv_scores):.4f} +/- {np.std(cv_scores):.4f}')
    return cv_scores, cv_val_results


def train_final_model(X_train, y_train, cv_scores,
                      experiment_name='AWS_Cost_Anomaly_Detection_v2_Features'):
    print('=' * 65)
    print('STEP 5 - TRAIN FINAL XGBOOST  [v2 features]')
    print('=' * 65)

    sample_weights   = compute_sample_weight('balanced', y=y_train)
    scale_pos_weight = float(
        (len(y_train) - (y_train == 1).sum()) / max((y_train == 1).sum(), 1)
    )

    SENSOR_COLS = ['cpu_mean', 'cpu_std', 'cpu_min', 'cpu_variance_24h',
                   'memory_mib', 'network_in_bytes', 'network_out_bytes', 'disk_io_ops']
    X_aug = X_train.copy()
    rng   = np.random.default_rng(seed=42)
    for col in SENSOR_COLS:
        if col in X_aug.columns:
            std = X_aug[col].std()
            if std > 0:
                X_aug[col] += rng.normal(0, 0.03 * std, size=len(X_aug))

    X_train_aug = pd.concat([X_train, X_aug], ignore_index=True)
    y_train_aug = pd.concat([y_train, y_train], ignore_index=True)
    sw_aug      = np.concatenate([sample_weights, sample_weights])

    mlflow.set_experiment(experiment_name)

    with mlflow.start_run(run_name='XGBoost_v2_Features_Augmented'):
        model = XGBClassifier(
            objective='multi:softprob', num_class=3,
            eval_metric='mlogloss',
            learning_rate=0.05, n_estimators=600, max_depth=5,
            min_child_weight=5, subsample=0.75, colsample_bytree=0.75,
            reg_alpha=0.5, reg_lambda=1.5,
            scale_pos_weight=scale_pos_weight,
            random_state=42, tree_method='hist', n_jobs=-1,
        )
        model.fit(X_train_aug, y_train_aug, sample_weight=sw_aug, verbose=50)

        mlflow.log_params(model.get_params())
        mlflow.log_metric('cv_f1_mean', float(np.mean(cv_scores)))
        mlflow.log_metric('cv_f1_std',  float(np.std(cv_scores)))
        mlflow.xgboost.log_model(model, 'model')
        print('  Model logged to MLflow.')

    return model, sample_weights


def optimize_threshold(cv_val_results):
    print('=' * 65)
    print('STEP 6 - THRESHOLD OPTIMIZATION  [v2]')
    print('=' * 65)

    all_prob = np.concatenate([r[0][:, 1] for r in cv_val_results])
    all_true = np.concatenate([r[1]       for r in cv_val_results])

    thresholds = np.arange(0.05, 0.95, 0.01)
    rows = []
    for th in thresholds:
        pred = (all_prob >= th).astype(int)
        rows.append({
            'Threshold': th,
            'F1':        f1_score(all_true == 1, pred, zero_division=0),
            'Precision': precision_score(all_true == 1, pred, zero_division=0),
            'Recall':    recall_score(all_true == 1, pred, zero_division=0),
        })

    threshold_df   = pd.DataFrame(rows)
    best_threshold = float(threshold_df.loc[threshold_df['F1'].idxmax(), 'Threshold'])
    print(f'  Best threshold: {best_threshold:.3f}')

    plt.figure(figsize=(8, 4))
    for col, ls in [('F1', '-'), ('Precision', '--'), ('Recall', ':')]:
        plt.plot(threshold_df['Threshold'], threshold_df[col], ls, label=col)
    plt.axvline(best_threshold, color='red', linestyle='--',
                label=f'Best={best_threshold:.3f}')
    plt.legend(); plt.grid(True)
    plt.title('Threshold Optimization v2')
    plt.tight_layout(); plt.savefig('threshold_curve_v2.png', dpi=100)
    plt.close()

    return best_threshold, threshold_df


def evaluate_model(model, X_test, y_test, best_threshold):
    print('=' * 65)
    print('STEP 7 - MODEL EVALUATION  [v2]')
    print('=' * 65)

    test_prob_all = model.predict_proba(X_test)
    test_prob     = test_prob_all[:, 1]
    test_pred     = (test_prob >= best_threshold).astype(int)

    print('\n-- Classification Report (3 classes) --')
    print(classification_report(y_test, model.predict(X_test), digits=4))

    auc = roc_auc_score(y_test == 1, test_prob)
    print(f'ROC-AUC (anomaly vs rest): {auc:.4f}')

    fig, ax = plt.subplots(figsize=(5, 5))
    ConfusionMatrixDisplay.from_predictions(y_test == 1, test_pred, cmap='Blues', ax=ax)
    plt.title('Confusion Matrix v2'); plt.tight_layout()
    plt.savefig('confusion_matrix_v2.png', dpi=100); plt.close()

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    RocCurveDisplay.from_predictions(y_test == 1, test_prob, ax=axes[0])
    axes[0].set_title('ROC Curve v2')
    PrecisionRecallDisplay.from_predictions(y_test == 1, test_prob, ax=axes[1])
    axes[1].set_title('Precision-Recall Curve v2')
    plt.tight_layout(); plt.savefig('roc_pr_curves_v2.png', dpi=100); plt.close()

    # Perturbation test
    noise_level  = 0.05
    X_test_noisy = X_test.copy()
    sensor_cols  = ['cpu_mean', 'cpu_std', 'memory_mib', 'network_in_bytes', 'network_out_bytes']
    for col in sensor_cols:
        if col in X_test_noisy.columns:
            noise = np.random.normal(0, noise_level * X_test_noisy[col].std(), len(X_test_noisy))
            X_test_noisy[col] += noise
    noisy_pred = (model.predict_proba(X_test_noisy)[:, 1] >= best_threshold).astype(int)
    f1_orig  = f1_score(y_test == 1, test_pred,  zero_division=0)
    f1_noisy = f1_score(y_test == 1, noisy_pred, zero_division=0)
    print(f'Perturbation Test:')
    print(f'  Original F1 : {f1_orig:.4f}')
    print(f'  Noisy F1    : {f1_noisy:.4f}')
    print(f'  F1 drop     : {f1_orig - f1_noisy:.4f}')

    evaluation_df = X_test.copy()
    evaluation_df['Actual']      = (y_test == 1).values
    evaluation_df['Probability'] = test_prob
    evaluation_df['Prediction']  = test_pred

    return test_pred, test_prob, evaluation_df


if __name__ == '__main__':
    DATA_DIR = '.'

    df_merged                 = load_and_merge(DATA_DIR)
    df_train_raw, df_test_raw = temporal_split(df_merged)
    X_train, y_train, X_test, y_test, train_stats, features = build_xy(
        df_train_raw, df_test_raw
    )
    cv_scores, cv_val_results     = walk_forward_cv(df_train_raw)
    model, sample_weights         = train_final_model(X_train, y_train, cv_scores)
    best_threshold, threshold_df  = optimize_threshold(cv_val_results)
    test_pred, test_prob, eval_df = evaluate_model(model, X_test, y_test, best_threshold)

    print('\nDetection pipeline v2 complete.')
