# DETECT.py
# Step 4: Walk-Forward Cross Validation
# Step 5: Train Final XGBoost (Gradient Boosting)
# Step 6: Threshold Optimization
# Step 7: Model Evaluation on Test Set
# Ref: data/plan.md - AWS Cost Anomaly Detection

import warnings
import numpy as np
import pandas as pd
import mlflow
import mlflow.xgboost
import matplotlib.pyplot as plt
from xgboost import XGBClassifier
from sklearn.model_selection import TimeSeriesSplit
from sklearn.utils.class_weight import compute_sample_weight
from sklearn.metrics import (
    f1_score, precision_score, recall_score,
    classification_report, roc_auc_score,
    ConfusionMatrixDisplay, RocCurveDisplay, PrecisionRecallDisplay,
)

from FEATURE import (
    load_and_merge, temporal_split, build_xy,
    fit_feature_stats, transform_features, FEATURE_COLS,
)

warnings.filterwarnings('ignore')


# ── Step 4: Walk-Forward Cross Validation ────────────────────────────────────
def walk_forward_cv(
    df_train_raw: pd.DataFrame,
    n_splits: int = 5,
) -> tuple:
    """
    Walk-forward CV on raw train data.
    Feature engineering is performed INSIDE each fold to prevent leakage.

    Fold layout (expanding window):
        Fold 1:  [=Train=]  [Val]  ...
        Fold 2:  [==Train==]  [Val]  ..
        Fold 3:  [===Train===]  [Val]  .
        ...

    Input  : df_train_raw (raw, no features yet)
    Output : cv_scores (list), sample_weights (for final model), cv_val_results
    """
    print('=' * 65)
    print('STEP 4 - WALK-FORWARD CROSS VALIDATION')
    print('=' * 65)

    tscv           = TimeSeriesSplit(n_splits=n_splits)
    cv_scores      = []
    cv_val_results = []   # list of (val_proba, val_true) per fold

    for fold, (train_idx, val_idx) in enumerate(tscv.split(df_train_raw)):
        fold_train = df_train_raw.iloc[train_idx].copy()
        fold_val   = df_train_raw.iloc[val_idx].copy()

        # Feature engineer independently per fold - no cross-fold leakage
        fold_stats = fit_feature_stats(fold_train)
        X_tr       = transform_features(fold_train, fold_stats)
        X_vl       = transform_features(fold_val,   fold_stats)

        feats  = [f for f in FEATURE_COLS if f in X_tr.columns]
        y_tr   = X_tr['label_encoded'].copy();  X_tr = X_tr[feats]
        y_vl   = X_vl['label_encoded'].copy();  X_vl = X_vl[feats]

        sw = compute_sample_weight('balanced', y=y_tr)

        # XGBoost Gradient Boosting - sequential trees on residuals
        clf = XGBClassifier(
            objective='multi:softprob',
            num_class=3,
            eval_metric='mlogloss',
            learning_rate=0.05,
            n_estimators=400,
            max_depth=5,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            tree_method='hist',
            n_jobs=-1,
        )
        clf.fit(X_tr, y_tr, sample_weight=sw)

        val_proba = clf.predict_proba(X_vl)
        pred      = np.argmax(val_proba, axis=1)
        score     = f1_score(y_vl, pred, average='macro')
        cv_scores.append(score)
        cv_val_results.append((val_proba, y_vl.values))
        print(f'  Fold {fold + 1} F1-macro: {score:.4f}')

    print(f'  CV F1-macro: {np.mean(cv_scores):.4f} +/- {np.std(cv_scores):.4f}')

    # Sample weights for the final model (computed on full train)
    # Will be passed to train_final_model
    return cv_scores, cv_val_results


# ── Step 5: Train Final XGBoost (Gradient Boosting) ─────────────────────────
def train_final_model(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    cv_scores: list,
    experiment_name: str = 'AWS_Cost_Anomaly_Detection_LeakFree',
) -> tuple:
    """
    Train XGBoost on full X_train using Gradient Boosting.

    Gradient Boosting mechanics:
      F_m(x) = F_{m-1}(x) + learning_rate * h_m(x)
      where h_m is a decision tree fit on pseudo-residuals of F_{m-1}.
      Each new tree corrects the errors of all previous trees.

    Key params:
      objective=multi:softprob  -> cross-entropy loss for 3 classes
      n_estimators=600          -> number of boosting rounds (trees)
      learning_rate=0.05        -> shrinkage per tree
      subsample/colsample_bytree -> stochastic boosting (random row/col subsets)
      reg_alpha/reg_lambda      -> L1/L2 regularisation

    Input  : X_train, y_train, cv_scores
    Output : model (fitted XGBClassifier), sample_weights
    """
    print('=' * 65)
    print('STEP 5 - TRAIN FINAL XGBOOST (Gradient Boosting)')
    print('=' * 65)

    sample_weights = compute_sample_weight('balanced', y=y_train)

    # scale_pos_weight chỉ có tác dụng với binary classification (binary:logistic).
    # Với multi:softprob (3 class), imbalance được xử lý bằng sample_weight ở trên.
    # Tính per-class weights để log MLflow, không pass vào XGBClassifier.
    class_counts  = np.bincount(y_train.astype(int))
    total         = len(y_train)
    # weight_class_i = total / (n_classes * count_i)
    per_class_w   = {int(i): round(total / (len(class_counts) * max(c, 1)), 4)
                     for i, c in enumerate(class_counts)}
    print(f'  Class imbalance weights: {per_class_w}')
    print(f'  sample_weight min={sample_weights.min():.3f} '
          f'mean={sample_weights.mean():.3f} '
          f'max={sample_weights.max():.3f}')

    # Noise augmentation: add Gaussian noise to sensor features during training
    # Reduces over-reliance on CPU/memory metrics, improves perturbation robustness
    SENSOR_COLS = ['cpu_mean', 'cpu_std', 'cpu_max', 'cpu_min', 'cpu_variance_24h',
                   'memory_mib', 'network_in_bytes', 'network_out_bytes', 'disk_io_ops',
                   'idle_hours_continuous']
    X_aug = X_train.copy()
    rng   = np.random.default_rng(seed=42)
    for col in SENSOR_COLS:
        if col in X_aug.columns:
            std = X_aug[col].std()
            if std > 0:
                X_aug[col] += rng.normal(0, 0.03 * std, size=len(X_aug))
    # Stack original + augmented to double train size
    X_train_aug = pd.concat([X_train, X_aug], ignore_index=True)
    y_train_aug = pd.concat([y_train, y_train], ignore_index=True)
    sw_aug      = np.concatenate([sample_weights, sample_weights])

    mlflow.set_experiment(experiment_name)

    with mlflow.start_run(run_name='XGBoost_Boosting_Final_Augmented'):

        model = XGBClassifier(
            objective='multi:softprob',
            num_class=3,
            eval_metric='mlogloss',
            learning_rate=0.05,
            n_estimators=600,
            max_depth=5,
            min_child_weight=5,
            subsample=0.75,
            colsample_bytree=0.75,
            reg_alpha=0.5,
            reg_lambda=1.5,
            # scale_pos_weight removed: has NO effect with multi:softprob.
            # Imbalance handled by sample_weight passed to model.fit().
            random_state=42,
            tree_method='hist',
            n_jobs=-1,
        )

        model.fit(X_train_aug, y_train_aug, sample_weight=sw_aug, verbose=50)

        mlflow.log_params(model.get_params())
        mlflow.log_metric('cv_f1_mean', float(np.mean(cv_scores)))
        mlflow.log_metric('cv_f1_std',  float(np.std(cv_scores)))
        for cls_idx, w in per_class_w.items():
            mlflow.log_metric(f'class_weight_{cls_idx}', w)
        mlflow.xgboost.log_model(model, 'model')
        print('  Model logged to MLflow.')

    return model, sample_weights


# ── Step 6: Threshold Optimization ──────────────────────────────────────────
def optimize_threshold(cv_val_results: list) -> tuple:
    """
    Find the best probability threshold for the anomaly class (class=1)
    using the aggregated predictions from all CV validation folds.

    Using CV folds instead of a separate holdout avoids wasting labelled data.

    Input  : cv_val_results - list of (val_proba shape(n,3), val_true) per fold
    Output : best_threshold (float), threshold_df (DataFrame)
    """
    print('=' * 65)
    print('STEP 6 - THRESHOLD OPTIMIZATION')
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

    # Plot
    plt.figure(figsize=(8, 4))
    for col, ls in [('F1', '-'), ('Precision', '--'), ('Recall', ':')]:
        plt.plot(threshold_df['Threshold'], threshold_df[col], ls, label=col)
    plt.axvline(best_threshold, color='red', linestyle='--',
                label=f'Best={best_threshold:.3f}')
    plt.legend(); plt.grid(True)
    plt.title('Threshold Optimization (CV folds)')
    plt.xlabel('Threshold'); plt.ylabel('Score')
    plt.tight_layout(); plt.savefig('threshold_curve.png', dpi=100)
    plt.close()

    return best_threshold, threshold_df


# ── Step 7: Evaluate on Test Set ─────────────────────────────────────────────
def evaluate_model(
    model: XGBClassifier,
    X_test: pd.DataFrame,
    y_test: pd.Series,
    best_threshold: float,
) -> tuple:
    """
    Evaluate final model on the held-out test set (20% most recent data).

    Metrics:
      - Classification report (Precision/Recall/F1 for all 3 classes)
      - ROC-AUC (binary: anomaly vs rest)
      - Confusion Matrix
      - Perturbation test (Gaussian noise 5% on sensor features)

    Input  : model, X_test, y_test, best_threshold
    Output : test_pred, test_prob, evaluation_df
    """
    print('=' * 65)
    print('STEP 7 - MODEL EVALUATION ON TEST SET')
    print('=' * 65)

    test_prob_all = model.predict_proba(X_test)         # shape (n, 3)
    test_prob     = test_prob_all[:, 1]                 # prob of class anomaly
    test_pred     = (test_prob >= best_threshold).astype(int)

    # 1. Full 3-class report - only show labels present in test set
    y_pred_full   = model.predict(X_test)
    labels_in_test = sorted(y_test.unique().tolist())
    name_map       = {0: 'normal', 1: 'anomaly', 2: 'benign'}
    target_names   = [name_map[l] for l in labels_in_test]

    print('\n-- Classification Report (labels present in test) --')
    print(classification_report(y_test, y_pred_full,
                                 labels=labels_in_test,
                                 target_names=target_names,
                                 digits=4, zero_division=0))

    missing = set([0,1,2]) - set(labels_in_test)
    if missing:
        missing_names = [name_map[m] for m in missing]
        print(f'  [NOTE] Classes absent from test set: {missing_names}')
        print(f'  Reason: benign events clustered in early dates -> all fall in train split')
        print(f'  benign F1 in CV folds: model learned to predict it, but no test samples to evaluate')

    # 2. Binary ROC-AUC (anomaly vs rest)
    auc = roc_auc_score(y_test == 1, test_prob)
    print(f'ROC-AUC (anomaly vs rest): {auc:.4f}')

    # 3. Confusion Matrix
    fig, ax = plt.subplots(figsize=(5, 5))
    ConfusionMatrixDisplay.from_predictions(y_test == 1, test_pred,
                                             cmap='Blues', ax=ax)
    plt.title('Confusion Matrix'); plt.tight_layout()
    plt.savefig('confusion_matrix.png', dpi=100); plt.close()

    # 4. ROC and PR curves
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    RocCurveDisplay.from_predictions(y_test == 1, test_prob, ax=axes[0])
    axes[0].set_title('ROC Curve')
    PrecisionRecallDisplay.from_predictions(y_test == 1, test_prob, ax=axes[1])
    axes[1].set_title('Precision-Recall Curve')
    plt.tight_layout(); plt.savefig('roc_pr_curves.png', dpi=100); plt.close()

    # 5. Perturbation test - model robustness check
    noise_level  = 0.05
    X_test_noisy = X_test.copy()
    sensor_cols  = ['cpu_mean', 'cpu_std', 'memory_mib', 'network_in_bytes', 'network_out_bytes']
    for col in sensor_cols:
        if col in X_test_noisy.columns:
            noise = np.random.normal(
                0, noise_level * X_test_noisy[col].std(), len(X_test_noisy)
            )
            X_test_noisy[col] += noise

    noisy_pred = (
        model.predict_proba(X_test_noisy)[:, 1] >= best_threshold
    ).astype(int)
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


# ── Main ─────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    DATA_DIR = '.'

    # Step 1-3 via FEATURE.py
    df_merged                 = load_and_merge(DATA_DIR)
    df_train_raw, df_test_raw = temporal_split(df_merged)
    X_train, y_train, X_test, y_test, train_stats, features = build_xy(
        df_train_raw, df_test_raw
    )

    # Step 4
    cv_scores, cv_val_results = walk_forward_cv(df_train_raw)

    # Step 5
    model, sample_weights = train_final_model(X_train, y_train, cv_scores)

    # Step 6
    best_threshold, threshold_df = optimize_threshold(cv_val_results)

    # Step 7
    test_pred, test_prob, evaluation_df = evaluate_model(
        model, X_test, y_test, best_threshold
    )

    print('Detection pipeline complete.')