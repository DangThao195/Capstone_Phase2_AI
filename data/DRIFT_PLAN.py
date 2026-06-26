# DRIFT_PLAN.py
# Step 8: SHAP Explainability
# Step 9: Drift Detection + Retrain Plan
# Ref: data/plan.md - AWS Cost Anomaly Detection

import warnings
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import shap
from scipy.stats import ks_2samp

warnings.filterwarnings('ignore')


# ── Step 8: SHAP Explainability ──────────────────────────────────────────────
def shap_global_explanation(model, X_test: pd.DataFrame, class_idx: int = 1):
    """
    Global SHAP analysis: which features drive anomaly predictions overall?

    class_idx=1 targets the anomaly class.
    Produces:
      - beeswarm summary plot (distribution of SHAP values per feature)
      - bar plot (mean absolute SHAP, feature ranking)

    Input  : fitted model, X_test, class_idx (1=anomaly)
    Output : shap_values (ndarray), explainer
    """
    print('=' * 65)
    print('STEP 8a - GLOBAL SHAP EXPLANATION')
    print('=' * 65)

    explainer = shap.TreeExplainer(model)
    sample_size = min(1000, len(X_test))
    X_sample    = X_test.sample(sample_size, random_state=42)
    shap_values = explainer.shap_values(X_sample)

    # XGBoost multi:softprob returns ndarray (n_samples, n_features, n_classes)
    sv_arr = np.array(shap_values)
    if sv_arr.ndim == 3:
        sv_anomaly = sv_arr[:, :, class_idx]
    elif isinstance(shap_values, list):
        sv_anomaly = np.array(shap_values[class_idx])
    else:
        sv_anomaly = sv_arr

    print('  Generating beeswarm summary plot ...')
    shap.summary_plot(sv_anomaly, X_sample, show=False)
    plt.title('Global SHAP - Anomaly Class (beeswarm)')
    plt.tight_layout(); plt.savefig('shap_beeswarm.png', dpi=100); plt.close()

    print('  Generating bar importance plot ...')
    shap.summary_plot(sv_anomaly, X_sample, plot_type='bar', show=False)
    plt.title('Global SHAP - Feature Importance (mean |SHAP|)')
    plt.tight_layout(); plt.savefig('shap_bar.png', dpi=100); plt.close()

    # Summary table
    mean_abs = np.abs(sv_anomaly).mean(axis=0).tolist()
    importance_df = pd.DataFrame({
        'Feature':       X_sample.columns.tolist(),
        'Mean_Abs_SHAP': mean_abs,
    }).sort_values('Mean_Abs_SHAP', ascending=False).reset_index(drop=True)

    print('  Top 10 features by SHAP importance:')
    print(importance_df.head(10).to_string(index=False))

    return shap_values, explainer, importance_df


def shap_local_explanation(
    model,
    explainer,
    X_test: pd.DataFrame,
    test_prob: np.ndarray,
    class_idx: int = 1,
    top_n: int = 3,
):
    """
    Local SHAP: explain WHY specific samples were predicted as anomaly.

    Generates waterfall plots for the top_n highest-probability anomalies.
    Reading the waterfall:
      SHAP > 0  -> pushes prediction toward anomaly
      SHAP < 0  -> pulls prediction toward normal/benign
      Red bar   -> high feature value, Blue bar -> low feature value

    Input  : model, explainer, X_test, test_prob, class_idx, top_n
    Output : None (saves waterfall PNG files)
    """
    print('=' * 65)
    print('STEP 8b - LOCAL SHAP EXPLANATION')
    print('=' * 65)

    top_indices = np.argsort(test_prob)[::-1][:top_n]

    for rank, idx in enumerate(top_indices):
        sample      = X_test.iloc[[idx]]
        sample_prob = test_prob[idx]
        sample_shap = explainer.shap_values(sample)
        sv_sample = np.array(sample_shap)
        if sv_sample.ndim == 3:
            sv = sv_sample[0, :, class_idx]
        elif isinstance(sample_shap, list):
            sv = np.array(sample_shap[class_idx])[0]
        else:
            sv = sv_sample[0]

        base = (
            explainer.expected_value[class_idx]
            if hasattr(explainer.expected_value, '__len__')
            else explainer.expected_value
        )

        print(f'  Sample {rank + 1}: index={idx}, anomaly_prob={sample_prob:.4f}')

        shap.waterfall_plot(
            shap.Explanation(
                values        = sv,
                base_values   = base,
                data          = sample.iloc[0],
                feature_names = X_test.columns.tolist(),
            ),
            show=False,
        )
        plt.title(f'Local SHAP - Sample {rank + 1} (prob={sample_prob:.3f})')
        plt.tight_layout()
        plt.savefig(f'shap_waterfall_rank{rank + 1}.png', dpi=100)
        plt.close()


# ── Step 9a: KS-Test Drift Detection ────────────────────────────────────────
def detect_drift_ks(
    X_reference: pd.DataFrame,
    X_production: pd.DataFrame,
    threshold: float = 0.05,
) -> tuple:
    """
    Kolmogorov-Smirnov test between reference (train) and production distributions.
    p-value < threshold indicates statistically significant drift.

    Input  : X_reference (X_train), X_production (latest 30-day window)
    Output : drift_df (per-feature results), drift_pct (fraction drifted)
    """
    rows = []
    for col in X_reference.select_dtypes(include=np.number).columns:
        stat, p = ks_2samp(
            X_reference[col].dropna(),
            X_production[col].dropna(),
        )
        rows.append({
            'Feature':      col,
            'KS_Statistic': round(stat, 4),
            'P_Value':      round(p, 6),
            'Drift':        'Yes' if p < threshold else 'No',
        })

    drift_df  = pd.DataFrame(rows).sort_values('P_Value').reset_index(drop=True)
    drift_pct = (drift_df['Drift'] == 'Yes').mean()
    return drift_df, drift_pct


# ── Step 9b: PSI Drift Detection ────────────────────────────────────────────
def calculate_psi(
    expected: np.ndarray,
    actual: np.ndarray,
    buckets: int = 10,
) -> float:
    """
    Population Stability Index between reference and production distributions.
    PSI is more stable than KS for continuous monitoring.

    Thresholds:
      PSI < 0.10  -> stable
      0.10-0.20   -> monitor closely
      PSI > 0.20  -> significant drift, trigger retrain

    Input  : expected (train), actual (production), buckets
    Output : psi value (float)
    """
    breakpoints        = np.percentile(expected, np.linspace(0, 100, buckets + 1))
    breakpoints[0]     = -np.inf
    breakpoints[-1]    =  np.inf

    exp_counts = np.histogram(expected, breakpoints)[0] / len(expected)
    act_counts = np.histogram(actual,   breakpoints)[0] / len(actual)

    # Avoid log(0)
    exp_counts = np.where(exp_counts == 0, 1e-4, exp_counts)
    act_counts = np.where(act_counts == 0, 1e-4, act_counts)

    return float(np.sum((act_counts - exp_counts) * np.log(act_counts / exp_counts)))


def detect_drift_psi(
    X_reference: pd.DataFrame,
    X_production: pd.DataFrame,
    top_features: list,
) -> pd.DataFrame:
    """
    Compute PSI for each feature in top_features list.

    Input  : X_reference, X_production, top_features (list of col names)
    Output : psi_df (DataFrame with Feature, PSI, Status columns)
    """
    rows = []
    for col in top_features:
        if col not in X_reference.columns or col not in X_production.columns:
            continue
        psi = calculate_psi(
            X_reference[col].dropna().values,
            X_production[col].dropna().values,
        )
        if psi >= 0.20:
            status = 'DRIFT - retrain now'
        elif psi >= 0.10:
            status = 'WARNING - monitor'
        else:
            status = 'Stable'
        rows.append({'Feature': col, 'PSI': round(psi, 4), 'Status': status})

    psi_df = pd.DataFrame(rows).sort_values('PSI', ascending=False).reset_index(drop=True)
    return psi_df


# ── Step 9c: Retrain Decision ────────────────────────────────────────────────
def should_retrain(
    drift_pct: float,
    psi_df: pd.DataFrame,
    current_f1: float,
    baseline_f1: float,
    days_since_last_retrain: int,
    drift_pct_threshold:    float = 0.30,
    psi_threshold:          float = 0.20,
    f1_drop_threshold:      float = 0.05,
    max_days_threshold:     int   = 30,
) -> tuple:
    """
    Evaluate all retrain trigger conditions and return decision.

    Conditions (any ONE triggers retrain):
      1. KS drift in > 30% of features
      2. Mean PSI of drifted features > 0.20
      3. Production F1 drops > 5% vs baseline
      4. More than 30 days since last retrain

    Input  : drift metrics, performance metrics, elapsed days
    Output : (should_retrain: bool, reasons: list of str)
    """
    reasons = []

    if drift_pct > drift_pct_threshold:
        reasons.append(
            f'KS drift: {drift_pct:.1%} features drifted (threshold {drift_pct_threshold:.0%})'
        )

    drifted_psi = psi_df.loc[psi_df['PSI'] >= psi_threshold, 'PSI']
    if len(drifted_psi) > 0:
        reasons.append(
            f'PSI alert: {len(drifted_psi)} features with PSI >= {psi_threshold}'
        )

    f1_drop = baseline_f1 - current_f1
    if f1_drop > f1_drop_threshold:
        reasons.append(
            f'F1 degradation: dropped {f1_drop:.3f} (threshold {f1_drop_threshold})'
        )

    if days_since_last_retrain >= max_days_threshold:
        reasons.append(
            f'Scheduled retrain: {days_since_last_retrain} days elapsed'
        )

    return len(reasons) > 0, reasons


# ── Step 9d: Retrain Plan Summary ────────────────────────────────────────────
def print_retrain_plan():
    """Print the 5-step retrain workflow for reference."""
    plan = """
RETRAIN PLAN (execute when should_retrain() returns True)
==========================================================

[R1] Collect new data
     - Pull production data from last retrain date to today
     - Verify / collect labels (manual review or weak supervision)

[R2] Build new dataset
     - Append new data to existing df_train_raw
     - Re-split 80% train / 20% test by date (no shuffle)
     - Verify class distribution is reasonable

[R3] Re-run Steps 3 to 8
     - fit_feature_stats() on new train split
     - Walk-forward CV 5 folds (new raw data)
     - Train new XGBoost with new MLflow experiment run
     - Compare new cv_f1_mean vs old: deploy only if improved

[R4] Deploy new model
     - Register model in MLflow Model Registry
     - Update train_stats (for production transform)
     - Update best_threshold
     - Append row to retrain_log.csv:
       {date, trigger_reason, old_f1, new_f1, mlflow_run_id}

[R5] Reset monitoring baseline
     - X_reference = new X_train
     - Reset KS / PSI counters
     - Resume monitoring on production predictions
"""
    print(plan)


# ── Full Drift Check ─────────────────────────────────────────────────────────
def run_drift_check(
    model,
    X_train: pd.DataFrame,
    X_test: pd.DataFrame,
    y_test: pd.Series,
    test_prob: np.ndarray,
    importance_df: pd.DataFrame,
    baseline_f1: float,
    days_since_last_retrain: int = 0,
):
    """
    Run complete drift detection pipeline and decide whether to retrain.

    Input  : model, X_train (reference), X_test (production proxy),
             y_test, test_prob, importance_df, baseline_f1
    Output : drift_df, psi_df, retrain_flag, reasons
    """
    print('=' * 65)
    print('STEP 9 - DRIFT DETECTION + RETRAIN PLAN')
    print('=' * 65)

    # KS test
    drift_df, drift_pct = detect_drift_ks(X_train, X_test)
    print(f'\nKS-Test results (top 10 drifted):')
    print(drift_df.head(10).to_string(index=False))
    print(f'\nFraction of features with drift: {drift_pct:.1%}')

    # PSI on top 20 features
    top_features = importance_df['Feature'].head(20).tolist()
    psi_df       = detect_drift_psi(X_train, X_test, top_features)
    print('\nPSI results:')
    print(psi_df.to_string(index=False))

    # Current F1 on test set (proxy for production performance)
    from sklearn.metrics import f1_score
    test_pred_hard = model.predict(X_test)
    current_f1     = f1_score(y_test, test_pred_hard, average='macro')
    print(f'\nCurrent F1-macro (test): {current_f1:.4f}')
    print(f'Baseline F1-macro      : {baseline_f1:.4f}')

    # Retrain decision
    retrain_flag, reasons = should_retrain(
        drift_pct=drift_pct,
        psi_df=psi_df,
        current_f1=current_f1,
        baseline_f1=baseline_f1,
        days_since_last_retrain=days_since_last_retrain,
    )

    print(f'\nRetrain decision: {"YES" if retrain_flag else "NO"}')
    if reasons:
        for r in reasons:
            print(f'  - {r}')

    if retrain_flag:
        print_retrain_plan()

    return drift_df, psi_df, retrain_flag, reasons


# ── Main ─────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    from FEATURE import load_and_merge, temporal_split, build_xy
    from DETECT  import (
        walk_forward_cv, train_final_model,
        optimize_threshold, evaluate_model,
    )

    DATA_DIR = '.'

    # Steps 1-3
    df_merged                 = load_and_merge(DATA_DIR)
    df_train_raw, df_test_raw = temporal_split(df_merged)
    X_train, y_train, X_test, y_test, train_stats, features = build_xy(
        df_train_raw, df_test_raw
    )

    # Steps 4-7
    cv_scores, cv_val_results         = walk_forward_cv(df_train_raw)
    model, sample_weights             = train_final_model(X_train, y_train, cv_scores)
    best_threshold, threshold_df      = optimize_threshold(cv_val_results)
    test_pred, test_prob, eval_df     = evaluate_model(model, X_test, y_test, best_threshold)

    # Step 8
    shap_values, explainer, importance_df = shap_global_explanation(model, X_test)
    shap_local_explanation(model, explainer, X_test, test_prob)

    # Step 9
    import numpy as np
    from sklearn.metrics import f1_score as f1
    baseline_f1 = float(f1(y_test, model.predict(X_train.iloc[:len(y_test)]),
                           average='macro', zero_division=0))

    drift_df, psi_df, retrain_flag, reasons = run_drift_check(
        model=model,
        X_train=X_train,
        X_test=X_test,
        y_test=y_test,
        test_prob=test_prob,
        importance_df=importance_df,
        baseline_f1=float(np.mean(cv_scores)),  # use CV mean as baseline
    )

    print('\nDrift & Retrain check complete.')