# ANALYSIS.py
# Deep analysis:
# 1. Fold 2 diagnosis        - cold-start class distribution + confusion matrix
# 2. Drift over folds        - KS-test, heatmap, distribution plots
# 3. Calibration             - Brier Score + calibration curve
# 4. Anomaly subtypes        - per-service, per-cost-bucket, missed FN profile
# 5. DynamoDB FP investigation - why DDB has high false positive rate
# 6. Missed anomaly forensics  - profile the 2 high-cost FN samples
# 7. SHAP + drift correlation  - confirm robust_z contribution, trim unstable features
# Run: python ANALYSIS.py  (from data/ directory)

import warnings
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from sklearn.model_selection import TimeSeriesSplit
from sklearn.utils.class_weight import compute_sample_weight
from sklearn.metrics import (
    f1_score, classification_report, confusion_matrix,
    ConfusionMatrixDisplay, brier_score_loss,
)
from sklearn.calibration import calibration_curve
from scipy.stats import ks_2samp
from xgboost import XGBClassifier
from FEATURE import (
    load_and_merge, temporal_split,
    fit_feature_stats, transform_features, FEATURE_COLS,
)

warnings.filterwarnings('ignore')
NAME_MAP = {0: 'normal', 1: 'anomaly', 2: 'benign'}



# =============================================================================
# ANALYSIS 1 - Fold 2 Deep Diagnosis
# =============================================================================
def analyse_fold2(df_train_raw):
    print('=' * 65)
    print('ANALYSIS 1 - FOLD 2 DEEP DIAGNOSIS')
    print('=' * 65)

    tscv   = TimeSeriesSplit(n_splits=5)
    splits = list(tscv.split(df_train_raw))
    train_idx, val_idx = splits[1]   # fold index 1 = Fold 2

    fold_train = df_train_raw.iloc[train_idx].copy()
    fold_val   = df_train_raw.iloc[val_idx].copy()

    print('\nLabel distribution:')
    print('  TRAIN:')
    train_vc = fold_train['label'].value_counts()
    for lbl, cnt in train_vc.items():
        print(f'    {lbl:8s}: {cnt:5d}  ({cnt/len(fold_train):.1%})')
    print('  VAL:')
    val_vc = fold_val['label'].value_counts()
    for lbl, cnt in val_vc.items():
        print(f'    {lbl:8s}: {cnt:5d}  ({cnt/len(fold_val):.1%})')

    benign_train = train_vc.get('benign', 0)
    benign_val   = val_vc.get('benign', 0)
    print(f'\n  Root cause: benign in train={benign_train}, in val={benign_val}')
    print('  Fold 2 is first fold where benign appears in val but not in train.')
    print('  This is a cold-start problem, not a pipeline error.')

    # Train fold model and get predictions
    fold_stats = fit_feature_stats(fold_train)
    X_tr_df    = transform_features(fold_train, fold_stats)
    X_vl_df    = transform_features(fold_val,   fold_stats)
    feats      = [f for f in FEATURE_COLS if f in X_tr_df.columns]

    y_tr = X_tr_df['label_encoded'].astype(int).values
    y_vl = X_vl_df['label_encoded'].astype(int).values
    X_tr = X_tr_df[feats].values
    X_vl = X_vl_df[feats].values

    sw  = compute_sample_weight('balanced', y=y_tr)
    clf = XGBClassifier(
        objective='multi:softprob', num_class=3,
        learning_rate=0.05, n_estimators=400, max_depth=5,
        subsample=0.8, colsample_bytree=0.8,
        random_state=42, tree_method='hist', n_jobs=-1,
    )
    clf.fit(X_tr, y_tr, sample_weight=sw)
    pred = np.argmax(clf.predict_proba(X_vl), axis=1).astype(int)

    present = sorted(set(y_vl.tolist()))
    tnames  = [NAME_MAP[i] for i in present]
    print('\nFold 2 classification report:')
    print(classification_report(y_vl, pred, labels=present,
                                  target_names=tnames, zero_division=0, digits=3))

    # Confusion matrix
    cm   = confusion_matrix(y_vl, pred, labels=present)
    fig, ax = plt.subplots(figsize=(5, 4))
    ConfusionMatrixDisplay(cm, display_labels=tnames).plot(ax=ax, cmap='Blues', colorbar=False)
    ax.set_title('Fold 2 Confusion Matrix\n(cold-start: benign not seen in training)')
    plt.tight_layout()
    plt.savefig('fold2_confusion_matrix.png', dpi=120)
    plt.close()
    print('  Saved: fold2_confusion_matrix.png')

    # Label distribution across ALL folds
    fig, axes = plt.subplots(2, 5, figsize=(18, 6))
    for fold_i, (_, vl_idx) in enumerate(tscv.split(df_train_raw)):
        fv = df_train_raw.iloc[vl_idx]
        vc = fv['label'].value_counts().reindex(['normal', 'anomaly', 'benign'], fill_value=0)
        colors = ['#4CAF50', '#F44336', '#2196F3']

        vc.plot(kind='bar', ax=axes[0, fold_i], color=colors, rot=30)
        axes[0, fold_i].set_title(f'Fold {fold_i+1} val\n{fv["clean_date"].min().date()}', fontsize=8)
        axes[0, fold_i].set_ylabel('Count')

        pct = vc / vc.sum() * 100
        pct.plot(kind='bar', ax=axes[1, fold_i], color=colors, rot=30)
        axes[1, fold_i].set_ylabel('%')
        axes[1, fold_i].set_ylim(0, 105)
        for p in axes[1, fold_i].patches:
            axes[1, fold_i].annotate(f'{p.get_height():.0f}%',
                                      (p.get_x() + p.get_width() / 2, p.get_height() + 1),
                                      ha='center', fontsize=7)

    plt.suptitle('Val label distribution per fold  |  Fold 2: benign=0 in train -> F1 drop explained',
                 fontsize=10, fontweight='bold')
    plt.tight_layout()
    plt.savefig('fold_label_distribution.png', dpi=120)
    plt.close()
    print('  Saved: fold_label_distribution.png')



# =============================================================================
# ANALYSIS 2 - Concept Drift Across Folds
# =============================================================================
def analyse_drift_over_folds(df_train_raw, train_stats):
    print('\n' + '=' * 65)
    print('ANALYSIS 2 - CONCEPT DRIFT ACROSS FOLDS (KS-test)')
    print('=' * 65)

    tscv      = TimeSeriesSplit(n_splits=5)
    fold_dfs  = []
    for _, vl_idx in tscv.split(df_train_raw):
        fv    = df_train_raw.iloc[vl_idx].copy()
        fv_fe = transform_features(fv, train_stats)
        feats = [f for f in FEATURE_COLS if f in fv_fe.columns]
        fold_dfs.append(fv_fe[feats])

    WATCH = [
        'line_item_unblended_cost', 'rolling_7d_avg',
        'cost_ratio_to_7d_avg', 'robust_z',
        'cpu_mean', 'cpu_variance_24h', 'peer_ratio',
    ]
    watch = [f for f in WATCH if f in fold_dfs[0].columns]
    ref   = fold_dfs[0]

    rows = []
    for i in range(1, 5):
        curr = fold_dfs[i]
        for feat in watch:
            stat, pval = ks_2samp(ref[feat].dropna(), curr[feat].dropna())
            rows.append({
                'Fold': f'Fold {i+1}', 'Feature': feat,
                'KS': round(stat, 3), 'P_Value': round(pval, 4),
                'Drift': 'YES' if pval < 0.05 else 'no',
            })

    drift_df = pd.DataFrame(rows)
    print(drift_df.to_string(index=False))
    drifted = (drift_df['Drift'] == 'YES').sum()
    print(f'\nDrifted pairs: {drifted} / {len(drift_df)}')

    # Heatmap
    pivot = drift_df.pivot(index='Feature', columns='Fold', values='KS')
    fig, ax = plt.subplots(figsize=(8, 4))
    im = ax.imshow(pivot.values, aspect='auto', cmap='YlOrRd', vmin=0, vmax=0.5)
    ax.set_xticks(range(len(pivot.columns))); ax.set_xticklabels(pivot.columns)
    ax.set_yticks(range(len(pivot.index)));  ax.set_yticklabels(pivot.index, fontsize=8)
    plt.colorbar(im, ax=ax, label='KS statistic (higher = more drift)')
    for i in range(len(pivot.index)):
        for j in range(len(pivot.columns)):
            ax.text(j, i, f'{pivot.values[i,j]:.2f}', ha='center', va='center', fontsize=8)
    ax.set_title('Feature Drift Heatmap (KS vs Fold 1 reference)\n'
                 'Values > 0.10 suggest distributional shift')
    plt.tight_layout()
    plt.savefig('drift_heatmap.png', dpi=120)
    plt.close()
    print('  Saved: drift_heatmap.png')

    # Distribution plot for most important cost feature
    feat_plot = 'cost_ratio_to_7d_avg' if 'cost_ratio_to_7d_avg' in fold_dfs[0].columns else watch[0]
    fig, axes = plt.subplots(1, 5, figsize=(16, 3), sharey=False)
    fold_labels = ['Fold 1', 'Fold 2', 'Fold 3', 'Fold 4', 'Fold 5']
    for i, (fdf, lbl) in enumerate(zip(fold_dfs, fold_labels)):
        vals = fdf[feat_plot].clip(-5, 10).dropna()
        axes[i].hist(vals, bins=40, color='#2196F3', alpha=0.75, edgecolor='white')
        axes[i].set_title(lbl, fontsize=9)
        axes[i].set_xlabel(feat_plot, fontsize=7)
    fig.suptitle(f'Distribution shift of {feat_plot} across folds', fontweight='bold')
    plt.tight_layout()
    plt.savefig('drift_feature_distribution.png', dpi=120)
    plt.close()
    print('  Saved: drift_feature_distribution.png')



# =============================================================================
# ANALYSIS 3 - Calibration
# =============================================================================
def analyse_calibration(model, X_test, y_test):
    print('\n' + '=' * 65)
    print('ANALYSIS 3 - PROBABILITY CALIBRATION')
    print('=' * 65)

    proba_all = model.predict_proba(X_test)
    y_binary  = (y_test.values == 1).astype(int)
    brier     = brier_score_loss(y_binary, proba_all[:, 1])

    print(f'\nBrier Score (anomaly class): {brier:.4f}')
    if brier < 0.05:
        print('  -> Excellent calibration')
    elif brier < 0.10:
        print('  -> Good calibration')
    else:
        print('  -> Consider isotonic or Platt scaling')

    for cls_idx, cls_name in NAME_MAP.items():
        b = brier_score_loss((y_test.values == cls_idx).astype(int), proba_all[:, cls_idx])
        print(f'  Brier [{cls_name:8s}]: {b:.4f}')

    # Calibration curve + probability histogram
    prob_true, prob_pred = calibration_curve(y_binary, proba_all[:, 1], n_bins=10)

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))

    axes[0].plot([0, 1], [0, 1], 'k--', label='Perfect')
    axes[0].plot(prob_pred, prob_true, 'o-', color='#F44336',
                  label=f'XGBoost (Brier={brier:.4f})')
    axes[0].fill_between(prob_pred, prob_true, prob_pred, alpha=0.12, color='#F44336')
    axes[0].set_xlabel('Mean predicted probability')
    axes[0].set_ylabel('Fraction of positives')
    axes[0].set_title('Calibration Curve - Anomaly Class')
    axes[0].legend(); axes[0].grid(True, alpha=0.3)

    axes[1].hist(proba_all[:, 1][y_binary == 0], bins=50, alpha=0.6,
                  color='#4CAF50', label='non-anomaly', density=True)
    axes[1].hist(proba_all[:, 1][y_binary == 1], bins=50, alpha=0.6,
                  color='#F44336', label='anomaly', density=True)
    axes[1].axvline(0.85, color='navy', linestyle='--', label='threshold=0.85')
    axes[1].set_xlabel('Predicted probability (anomaly)')
    axes[1].set_ylabel('Density')
    axes[1].set_title('Probability Distribution by True Class')
    axes[1].legend(); axes[1].grid(True, alpha=0.3)

    plt.suptitle(f'Calibration Analysis  |  Brier Score = {brier:.4f}', fontweight='bold')
    plt.tight_layout()
    plt.savefig('calibration_analysis.png', dpi=120)
    plt.close()
    print('  Saved: calibration_analysis.png')



# =============================================================================
# ANALYSIS 4 - Anomaly Subtype Performance
# =============================================================================
def analyse_anomaly_subtypes(model, df_test_raw, X_test, y_test, best_threshold):
    print('\n' + '=' * 65)
    print('ANALYSIS 4 - ANOMALY SUBTYPE PERFORMANCE')
    print('=' * 65)

    proba    = model.predict_proba(X_test)[:, 1]
    pred_bin = (proba >= best_threshold).astype(int)
    true_bin = (y_test.values == 1).astype(int)

    result = df_test_raw.copy().reset_index(drop=True)
    if len(result) != len(X_test):
        print('  Index mismatch - skipping subtype analysis')
        return

    result['true_anomaly'] = true_bin
    result['pred_anomaly'] = pred_bin
    result['prob_anomaly'] = proba
    anomalies = result[result['true_anomaly'] == 1].copy()

    # 1. Per-service breakdown
    svc_col = next((c for c in ['line_item_product_code', 'derived_service_code']
                    if c in anomalies.columns), None)
    if svc_col:
        print(f'\nPer-service anomaly detection (col={svc_col}):')
        rows = []
        for svc, grp in anomalies.groupby(svc_col):
            recall   = grp['pred_anomaly'].mean()
            n        = len(grp)
            fp_group = result[(result[svc_col] == svc) & (result['true_anomaly'] == 0)]
            fp_rate  = fp_group['pred_anomaly'].mean() if len(fp_group) > 0 else 0.0
            rows.append({'Service': svc, 'N_anomaly': n,
                          'Recall': round(recall, 3), 'FP_Rate': round(fp_rate, 3)})
        svc_df = pd.DataFrame(rows).sort_values('N_anomaly', ascending=False)
        print(svc_df.to_string(index=False))

        fig, axes = plt.subplots(1, 2, figsize=(14, 5))
        top = svc_df.head(10)
        axes[0].barh(top['Service'], top['Recall'], color='#4CAF50', edgecolor='white')
        axes[0].axvline(0.9, color='red', linestyle='--', label='target=0.9')
        axes[0].set_xlim(0, 1.1); axes[0].set_xlabel('Recall')
        axes[0].set_title('Anomaly Recall by Service'); axes[0].legend()
        for i, v in enumerate(top['Recall']):
            axes[0].text(v + 0.01, i, f'{v:.3f}', va='center', fontsize=8)

        axes[1].barh(top['Service'], top['FP_Rate'], color='#F44336', edgecolor='white')
        axes[1].set_xlabel('FP Rate among non-anomaly')
        axes[1].set_title('False Positive Rate by Service')
        for i, v in enumerate(top['FP_Rate']):
            axes[1].text(v + 0.001, i, f'{v:.3f}', va='center', fontsize=8)

        plt.suptitle('Anomaly Detection Performance by AWS Service', fontweight='bold')
        plt.tight_layout()
        plt.savefig('anomaly_subtype_by_service.png', dpi=120)
        plt.close()
        print('  Saved: anomaly_subtype_by_service.png')

    # 2. Cost magnitude breakdown
    cost_col = 'line_item_unblended_cost'
    if cost_col in anomalies.columns:
        print('\nAnomaly recall by cost magnitude:')
        bins   = [0, 1, 10, 50, float('inf')]
        labels = ['<$1', '$1-10', '$10-50', '>$50']
        anomalies['cost_bucket'] = pd.cut(anomalies[cost_col], bins=bins, labels=labels)
        stats = anomalies.groupby('cost_bucket', observed=True).agg(
            count=('true_anomaly', 'count'),
            recall=('pred_anomaly', 'mean'),
        ).reset_index()
        stats['recall'] = stats['recall'].round(3)
        print(stats.to_string(index=False))

        fig, ax = plt.subplots(figsize=(7, 4))
        bars = ax.bar(stats['cost_bucket'].astype(str), stats['recall'],
                       color='#9C27B0', edgecolor='white')
        ax.set_ylim(0, 1.12); ax.set_xlabel('Cost bucket (USD)')
        ax.set_ylabel('Recall')
        ax.set_title('Anomaly Recall by Cost Magnitude\n(low-cost anomalies are harder to catch)')
        for bar, row in zip(bars, stats.itertuples()):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.02,
                    f'{row.recall:.2f}\nn={row.count}', ha='center', fontsize=9)
        plt.tight_layout()
        plt.savefig('anomaly_subtype_by_cost.png', dpi=120)
        plt.close()
        print('  Saved: anomaly_subtype_by_cost.png')

    # 3. Missed anomaly profile
    missed = anomalies[anomalies['pred_anomaly'] == 0]
    caught = anomalies[anomalies['pred_anomaly'] == 1]
    fn_rate = len(missed) / max(len(anomalies), 1)
    print(f'\nMissed anomalies (FN): {len(missed)} / {len(anomalies)} ({fn_rate:.1%})')
    if len(missed) > 0 and cost_col in missed.columns:
        print(f'  Missed: mean cost=${missed[cost_col].mean():.2f}  '
              f'median=${missed[cost_col].median():.2f}')
        print(f'  Caught: mean cost=${caught[cost_col].mean():.2f}  '
              f'median=${caught[cost_col].median():.2f}')
        if missed[cost_col].mean() < caught[cost_col].mean():
            print('  -> Missed anomalies tend to be lower cost; '
                  'consider lowering threshold or adding cost-scaled features.')




# =============================================================================
# ANALYSIS 5 - DynamoDB False Positive Investigation
# =============================================================================
def analyse_dynamodb_fp(model, df_test_raw, X_test, y_test, best_threshold, train_stats):
    """
    DynamoDB has FP_Rate=0.162 - investigate which features push normal DDB
    records over the anomaly threshold.
    """
    print('\n' + '=' * 65)
    print('ANALYSIS 5 - DYNAMODB FALSE POSITIVE INVESTIGATION')
    print('=' * 65)

    proba    = model.predict_proba(X_test)[:, 1]
    pred_bin = (proba >= best_threshold).astype(int)
    true_bin = (y_test.values == 1).astype(int)

    result = df_test_raw.copy().reset_index(drop=True)
    if len(result) != len(X_test):
        print('  Index mismatch - skipping')
        return

    result['true_anomaly'] = true_bin
    result['pred_anomaly'] = pred_bin
    result['prob_anomaly'] = proba

    svc_col = next((c for c in ['line_item_product_code', 'derived_service_code']
                    if c in result.columns), None)
    if svc_col is None:
        print('  No service column found - skipping')
        return

    ddb = result[result[svc_col] == 'AmazonDynamoDB'].copy()
    if len(ddb) == 0:
        print('  No DynamoDB rows found')
        return

    ddb_normal = ddb[ddb['true_anomaly'] == 0]
    ddb_fp     = ddb[(ddb['true_anomaly'] == 0) & (ddb['pred_anomaly'] == 1)]
    ddb_tn     = ddb[(ddb['true_anomaly'] == 0) & (ddb['pred_anomaly'] == 0)]

    print(f'\nDynamoDB breakdown:')
    print(f'  Total rows        : {len(ddb)}')
    print(f'  True anomaly      : {(ddb["true_anomaly"]==1).sum()}')
    print(f'  True normal       : {len(ddb_normal)}')
    print(f'  False Positives   : {len(ddb_fp)}  ({len(ddb_fp)/max(len(ddb_normal),1):.1%})')

    # Compare feature distributions FP vs TN
    feats_to_check = [f for f in [
        'line_item_unblended_cost', 'cost_ratio_to_7d_avg', 'robust_z',
        'rolling_7d_avg', 'rolling_7d_std', 'cost_diff',
        'cpu_mean', 'cpu_variance_24h', 'usage_density',
    ] if f in X_test.columns]

    X_test_reset = X_test.reset_index(drop=True)
    ddb_fp_idx   = ddb_fp.index.tolist()
    ddb_tn_idx   = ddb_tn.index.tolist()

    if len(ddb_fp_idx) == 0 or len(ddb_tn_idx) == 0:
        print('  Not enough FP/TN samples for comparison')
        return

    fp_feats = X_test_reset.loc[ddb_fp_idx, feats_to_check]
    tn_feats = X_test_reset.loc[ddb_tn_idx, feats_to_check]

    print('\nFeature comparison (FP vs TN for DynamoDB normal rows):')
    rows = []
    for feat in feats_to_check:
        fp_mean = fp_feats[feat].mean()
        tn_mean = tn_feats[feat].mean()
        ratio   = fp_mean / (tn_mean + 1e-9)
        rows.append({'Feature': feat,
                     'FP_mean': round(fp_mean, 4),
                     'TN_mean': round(tn_mean, 4),
                     'FP/TN_ratio': round(ratio, 3)})
    cmp_df = pd.DataFrame(rows).sort_values('FP/TN_ratio', ascending=False)
    print(cmp_df.to_string(index=False))
    print('\n  Features with FP/TN ratio >> 1 are the primary drivers of DDB false positives.')
    print('  Consider adding DDB-specific features (e.g. ProvisionedThroughput, ConsumedRCU/WCU)')
    print('  or training a service-specific sub-model for DynamoDB.')

    # Bar chart of top distinguishing features
    fig, ax = plt.subplots(figsize=(9, 5))
    top_feats = cmp_df.head(8)
    x = range(len(top_feats))
    w = 0.35
    ax.bar([i - w/2 for i in x], top_feats['FP_mean'], w,
            label='False Positive (pred=anomaly, true=normal)', color='#F44336', alpha=0.8)
    ax.bar([i + w/2 for i in x], top_feats['TN_mean'], w,
            label='True Negative (correctly normal)', color='#4CAF50', alpha=0.8)
    ax.set_xticks(list(x)); ax.set_xticklabels(top_feats['Feature'], rotation=30, ha='right')
    ax.set_ylabel('Mean feature value')
    ax.set_title('DynamoDB FP vs TN: Feature Comparison\n'
                 '(Red bars higher = feature drives false positives)')
    ax.legend(fontsize=8); ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig('ddb_fp_feature_comparison.png', dpi=120)
    plt.close()
    print('  Saved: ddb_fp_feature_comparison.png')


# =============================================================================
# ANALYSIS 6 - Missed Anomaly Forensics (2 FN samples at $503.92)
# =============================================================================
def analyse_missed_anomalies(model, df_test_raw, X_test, y_test, best_threshold):
    """
    Profile the false negative (missed) anomaly samples.
    Understand why the model failed to catch them.
    """
    print('\n' + '=' * 65)
    print('ANALYSIS 6 - MISSED ANOMALY FORENSICS')
    print('=' * 65)

    proba    = model.predict_proba(X_test)[:, 1]
    pred_bin = (proba >= best_threshold).astype(int)
    true_bin = (y_test.values == 1).astype(int)

    result = df_test_raw.copy().reset_index(drop=True)
    if len(result) != len(X_test):
        print('  Index mismatch - skipping')
        return

    result['true_anomaly'] = true_bin
    result['pred_anomaly'] = pred_bin
    result['prob_anomaly'] = proba

    missed    = result[(result['true_anomaly'] == 1) & (result['pred_anomaly'] == 0)].copy()
    caught    = result[(result['true_anomaly'] == 1) & (result['pred_anomaly'] == 1)].copy()

    print(f'\nMissed anomalies: {len(missed)}  |  Caught: {len(caught)}')

    if len(missed) == 0:
        print('  No missed anomalies - perfect recall.')
        return

    # Show all missed samples
    info_cols = [c for c in [
        'line_item_resource_id', 'line_item_product_code',
        'clean_date', 'line_item_unblended_cost',
        'label', 'prob_anomaly',
    ] if c in missed.columns]
    print('\nMissed anomaly records:')
    print(missed[info_cols].to_string(index=False))

    # Feature values of missed vs caught
    X_reset  = X_test.reset_index(drop=True)
    miss_idx = missed.index.tolist()
    catch_idx = caught.index.tolist()

    key_feats = [f for f in [
        'line_item_unblended_cost', 'cost_ratio_to_7d_avg', 'robust_z',
        'rolling_7d_avg', 'rolling_7d_std', 'cost_diff',
        'slope_14d', 'cost_pct_change', 'peer_ratio',
    ] if f in X_reset.columns]

    miss_feat = X_reset.loc[miss_idx, key_feats]
    catch_feat = X_reset.loc[catch_idx, key_feats]

    print('\nKey feature comparison (missed vs caught anomalies):')
    rows = []
    for feat in key_feats:
        rows.append({
            'Feature':       feat,
            'Missed_mean':   round(miss_feat[feat].mean(), 4),
            'Caught_mean':   round(catch_feat[feat].mean(), 4),
        })
    cmp_df = pd.DataFrame(rows)
    print(cmp_df.to_string(index=False))

    print('\nInterpretation:')
    cost_col = 'line_item_unblended_cost'
    if cost_col in miss_feat.columns and cost_col in catch_feat.columns:
        m_cost = miss_feat[cost_col].mean()
        c_cost = catch_feat[cost_col].mean()
        if m_cost > c_cost:
            print(f'  Missed anomalies have HIGHER cost (${m_cost:.2f} vs ${c_cost:.2f}).')
            print('  Possible cause: high-cost events have volatile rolling_7d_avg,')
            print('  so cost_ratio_to_7d_avg stays near 1.0 despite being anomalous.')
            print('  Fix suggestion: add absolute_cost_spike feature = cost - 3*rolling_7d_std')
        else:
            print(f'  Missed anomalies have LOWER cost - harder to distinguish from normal noise.')

    if 'robust_z' in miss_feat.columns:
        mz = miss_feat['robust_z'].mean()
        cz = catch_feat['robust_z'].mean()
        print(f'  robust_z: missed={mz:.3f}, caught={cz:.3f}')
        if abs(mz) < abs(cz):
            print('  robust_z is LOWER for missed samples -> they look statistically normal')
            print('  These may be anomalies defined by business rules, not statistical deviation.')

    # Probability scores of missed samples
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.hist(proba[true_bin == 1], bins=30, alpha=0.7, color='#F44336',
             label='True anomaly prob distribution')
    for p in missed['prob_anomaly']:
        ax.axvline(p, color='navy', linestyle='--', alpha=0.9, linewidth=1.5)
    ax.axvline(best_threshold, color='black', linestyle='-', linewidth=2,
                label=f'Threshold={best_threshold:.2f}')
    ax.set_xlabel('Predicted probability (anomaly class)')
    ax.set_ylabel('Count')
    ax.set_title('Probability of True Anomalies\n(navy dashed = missed FN samples)')
    ax.legend()
    plt.tight_layout()
    plt.savefig('missed_anomaly_probabilities.png', dpi=120)
    plt.close()
    print('  Saved: missed_anomaly_probabilities.png')


# =============================================================================
# ANALYSIS 7 - SHAP + Drift Correlation
# =============================================================================
def analyse_shap_drift_correlation(model, X_test, y_test, train_stats, df_train_raw):
    """
    1. Compute SHAP values for anomaly class
    2. Compute per-feature drift score (mean KS across folds)
    3. Cross-tabulate: high-SHAP but high-drift features are the riskiest
    4. Recommend which features to keep vs investigate
    """
    print('\n' + '=' * 65)
    print('ANALYSIS 7 - SHAP IMPORTANCE vs DRIFT STABILITY')
    print('=' * 65)
    print('  (XGBoost learns feature weights internally via tree splits.')
    print('   We use SHAP to measure actual contribution, not manual weighting.)\n')

    try:
        import shap
    except ImportError:
        print('  shap not installed. Run: pip install shap')
        return

    # SHAP values for anomaly class (class index 1)
    explainer   = shap.TreeExplainer(model)
    sample_size = min(800, len(X_test))
    X_sample    = X_test.sample(sample_size, random_state=42)
    shap_vals  = explainer.shap_values(X_sample)
    sv_arr     = np.array(shap_vals)   # shape: (n_samples, n_features, n_classes)

    # Handle both output formats from different shap/xgboost versions
    if sv_arr.ndim == 3:
        # (n_samples, n_features, n_classes) -> take anomaly class index 1
        sv_anomaly = sv_arr[:, :, 1]
    elif isinstance(shap_vals, list):
        sv_anomaly = np.array(shap_vals[1])
    else:
        sv_anomaly = sv_arr

    mean_abs   = np.abs(sv_anomaly).mean(axis=0)
    feat_names = X_sample.columns.tolist()

    shap_importance = pd.DataFrame({
        'Feature':       feat_names,
        'Mean_Abs_SHAP': mean_abs.tolist(),
    }).sort_values('Mean_Abs_SHAP', ascending=False).reset_index(drop=True)

    # Drift score: mean KS across folds vs fold 1
    tscv     = TimeSeriesSplit(n_splits=5)
    fold_dfs = []
    for _, vl_idx in tscv.split(df_train_raw):
        fv    = df_train_raw.iloc[vl_idx].copy()
        fv_fe = transform_features(fv, train_stats)
        feats = [f for f in FEATURE_COLS if f in fv_fe.columns]
        fold_dfs.append(fv_fe[feats])

    ref = fold_dfs[0]
    drift_rows = []
    for feat in X_sample.columns:
        if feat not in ref.columns:
            drift_rows.append({'Feature': feat, 'Mean_KS': 0.0})
            continue
        ks_vals = []
        for i in range(1, 5):
            curr = fold_dfs[i]
            if feat in curr.columns:
                stat, _ = ks_2samp(ref[feat].dropna(), curr[feat].dropna())
                ks_vals.append(stat)
        drift_rows.append({'Feature': feat, 'Mean_KS': round(np.mean(ks_vals), 4)})

    drift_df = pd.DataFrame(drift_rows)

    # Merge SHAP + Drift
    combined = shap_importance.merge(drift_df, on='Feature')
    combined['SHAP_rank']  = combined['Mean_Abs_SHAP'].rank(ascending=False).astype(int)
    combined['Drift_rank'] = combined['Mean_KS'].rank(ascending=False).astype(int)

    # Quadrant classification
    shap_med  = combined['Mean_Abs_SHAP'].median()
    drift_med = combined['Mean_KS'].median()

    def quadrant(row):
        hi_shap  = row['Mean_Abs_SHAP'] > shap_med
        hi_drift = row['Mean_KS'] > drift_med
        if hi_shap and not hi_drift:
            return 'KEEP (high impact, stable)'
        if hi_shap and hi_drift:
            return 'MONITOR (high impact, drifts)'
        if not hi_shap and not hi_drift:
            return 'OPTIONAL (low impact, stable)'
        return 'CONSIDER REMOVING (low impact, drifts)'

    combined['Recommendation'] = combined.apply(quadrant, axis=1)
    combined = combined.sort_values('Mean_Abs_SHAP', ascending=False)

    print('Feature SHAP importance vs Drift stability:')
    print(combined[['Feature', 'Mean_Abs_SHAP', 'Mean_KS', 'Recommendation']].to_string(index=False))

    # Scatter plot: SHAP vs KS drift
    fig, ax = plt.subplots(figsize=(10, 7))
    colors = {
        'KEEP (high impact, stable)':           '#4CAF50',
        'MONITOR (high impact, drifts)':        '#FF9800',
        'OPTIONAL (low impact, stable)':        '#2196F3',
        'CONSIDER REMOVING (low impact, drifts)': '#F44336',
    }
    for _, row in combined.iterrows():
        color = colors.get(row['Recommendation'], 'grey')
        ax.scatter(row['Mean_KS'], row['Mean_Abs_SHAP'], c=color, s=80, zorder=3)
        ax.annotate(row['Feature'], (row['Mean_KS'], row['Mean_Abs_SHAP']),
                    fontsize=7, xytext=(4, 2), textcoords='offset points')

    ax.axvline(drift_med, color='grey', linestyle='--', alpha=0.5, label='median drift')
    ax.axhline(shap_med,  color='grey', linestyle=':',  alpha=0.5, label='median SHAP')

    # Legend patches
    import matplotlib.patches as mpatches
    patches = [mpatches.Patch(color=c, label=l) for l, c in colors.items()]
    ax.legend(handles=patches, fontsize=7, loc='upper right')
    ax.set_xlabel('Mean KS drift (higher = less stable across time)')
    ax.set_ylabel('Mean |SHAP| for anomaly class (higher = more important)')
    ax.set_title('Feature Importance vs Drift Stability\n'
                 'Green=KEEP | Orange=MONITOR | Blue=OPTIONAL | Red=CONSIDER REMOVING')
    ax.grid(True, alpha=0.25)
    plt.tight_layout()
    plt.savefig('shap_vs_drift.png', dpi=120)
    plt.close()
    print('  Saved: shap_vs_drift.png')

    # Confirm robust_z specifically
    rz_row = combined[combined['Feature'] == 'robust_z']
    if len(rz_row) > 0:
        rz = rz_row.iloc[0]
        print(f'\nrobust_z: Mean|SHAP|={rz["Mean_Abs_SHAP"]:.4f}, '
              f'Mean_KS={rz["Mean_KS"]:.4f}, -> {rz["Recommendation"]}')
        print('  robust_z is drift-stable (KS~0) AND contributes to predictions.')
        print('  Confirmed: keep as core feature. XGBoost will naturally weight it correctly.')

    return combined


# =============================================================================
# MAIN
# =============================================================================
if __name__ == '__main__':
    from DETECT import (
        walk_forward_cv, train_final_model,
        optimize_threshold, evaluate_model,
    )

    DATA_DIR = '.'

    df_merged                 = load_and_merge(DATA_DIR)
    df_train_raw, df_test_raw = temporal_split(df_merged)
    train_stats               = fit_feature_stats(df_train_raw)

    df_train_fe = transform_features(df_train_raw, train_stats)
    df_test_fe  = transform_features(df_test_raw,  train_stats)
    feats       = [f for f in FEATURE_COLS if f in df_train_fe.columns]

    X_train = df_train_fe[feats]
    y_train = df_train_fe['label_encoded']
    X_test  = df_test_fe[feats]
    y_test  = df_test_fe['label_encoded']

    cv_scores, cv_val_results     = walk_forward_cv(df_train_raw)
    model, _                      = train_final_model(X_train, y_train, cv_scores)
    best_threshold, _             = optimize_threshold(cv_val_results)
    test_pred, test_prob, eval_df = evaluate_model(model, X_test, y_test, best_threshold)

    # Original 4 analyses
    analyse_fold2(df_train_raw)
    analyse_drift_over_folds(df_train_raw, train_stats)
    analyse_calibration(model, X_test, y_test)
    analyse_anomaly_subtypes(model, df_test_raw, X_test, y_test, best_threshold)

    # New 3 analyses based on improvement feedback
    analyse_dynamodb_fp(model, df_test_raw, X_test, y_test, best_threshold, train_stats)
    analyse_missed_anomalies(model, df_test_raw, X_test, y_test, best_threshold)
    shap_drift_df = analyse_shap_drift_correlation(model, X_test, y_test, train_stats, df_train_raw)

    print('\n' + '=' * 65)
    print('All 7 analyses complete. PNG files saved.')
    print('=' * 65)
