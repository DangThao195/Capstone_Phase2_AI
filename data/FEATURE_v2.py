# FEATURE_v2.py
# v2 improvements from ANALYSIS.py findings:
#   1. Removed 6 "CONSIDER REMOVING" features (low SHAP + high drift):
#      cpu_max, line_item_usage_amount, usage_density,
#      dayofweek, cost_pct_change, idle_hours_continuous
#   2. Added absolute_cost_spike = cost - 3*rolling_7d_std
#      (fixes the 2 missed anomalies: high-cost but ratio~1.0)
#   3. Added ddb_flag = 1 if service is AmazonDynamoDB
#      (helps reduce DDB false positives: FP_Rate was 16.2%)
# Compare v1 vs v2 by running both and checking CV F1 + perturbation drop.

import os
import warnings
import numpy as np
import pandas as pd
from scipy.stats import linregress

warnings.filterwarnings('ignore')

LABEL_MAP = {'normal': 0, 'anomaly': 1, 'benign': 2}

METRICS_MAPPING = {
    'AmazonEC2':       'ec2_metrics.csv',
    'AmazonRDS':       'rds_metrics.csv',
    'AmazonDynamoDB':  'ddb_metrics.csv',
    'AmazonSageMaker': 'sagemaker_metrics.csv',
    'AWSDataTransfer': 'other_services_metrics.csv',
    'AWSELB':          'other_services_metrics.csv',
}

# v2: removed cpu_max, line_item_usage_amount, usage_density,
#         dayofweek, cost_pct_change, idle_hours_continuous
# v2: added  absolute_cost_spike, ddb_flag
FEATURE_COLS_V2 = [
    'line_item_unblended_cost', 'rolling_7d_avg', 'rolling_7d_std',
    'cost_ratio_to_7d_avg', 'cost_diff', 'robust_z',
    'slope_14d', 'cost_pct_change_28d', 'age_days',
    'month', 'is_weekend',
    'cost_per_unit_usage',
    'cpu_mean', 'cpu_std', 'cpu_min', 'cpu_variance_24h',
    'memory_mib', 'network_in_bytes', 'network_out_bytes', 'disk_io_ops',
    'team_missing', 'owner_missing', 'peer_ratio',
    'absolute_cost_spike',   # NEW: cost - 3*rolling_7d_std
    'ddb_flag',              # NEW: 1 if AmazonDynamoDB
]

TRAIN_RATIO = 0.80


def _max_idle_streak(row):
    max_streak = current = 0
    for v in row:
        if v:
            current += 1
            max_streak = max(max_streak, current)
        else:
            current = 0
    return max_streak


def load_and_merge(data_dir='.'):
    print('=' * 65)
    print('STEP 1 - LOAD & MERGE  [v2]')
    print('=' * 65)

    cur_path = os.path.join(data_dir, 'cur_line_items.csv')
    if not os.path.exists(cur_path):
        raise FileNotFoundError(f'Not found: {cur_path}')

    df_cur = pd.read_csv(cur_path)
    df_cur['clean_date'] = pd.to_datetime(
        df_cur['line_item_usage_start_date']
    ).dt.normalize()
    print(f'  CUR loaded : {len(df_cur):,} rows')

    cpu_cols     = [f'cpu_h{i}' for i in range(24)]
    metrics_list = []

    for service, filename in METRICS_MAPPING.items():
        fpath = os.path.join(data_dir, filename)
        if not os.path.exists(fpath):
            continue
        df = pd.read_csv(fpath)
        df['derived_service_code'] = service
        df['clean_date'] = pd.to_datetime(df['timestamp']).dt.normalize()

        if set(cpu_cols).issubset(df.columns):
            cpu_matrix  = df[cpu_cols].fillna(0).values
            idle_matrix = cpu_matrix < 5
            df['idle_hours_continuous'] = np.apply_along_axis(
                _max_idle_streak, 1, idle_matrix
            )
            df['cpu_mean'] = cpu_matrix.mean(axis=1)
            df['cpu_std']  = cpu_matrix.std(axis=1)
            df['cpu_max']  = cpu_matrix.max(axis=1)
            df['cpu_min']  = cpu_matrix.min(axis=1)
        else:
            df['idle_hours_continuous'] = 0
            df['cpu_mean'] = df['cpu_std'] = df['cpu_max'] = df['cpu_min'] = np.nan

        metrics_list.append(df)

    if not metrics_list:
        raise RuntimeError('No metrics files loaded.')

    df_metrics = pd.concat(metrics_list, ignore_index=True)
    df_metrics  = df_metrics.drop_duplicates(subset=['resource_id', 'clean_date'])
    print(f'  Metrics loaded : {len(df_metrics):,} rows')

    df = pd.merge(
        df_cur, df_metrics,
        left_on=['line_item_resource_id', 'clean_date'],
        right_on=['resource_id',          'clean_date'],
        how='inner',
    )
    print(f'  After merge    : {len(df):,} rows')

    df = df.sort_values(['line_item_resource_id', 'clean_date']).reset_index(drop=True)
    df['label_encoded'] = df['label'].map(LABEL_MAP)
    print(df['label'].value_counts().to_string())
    return df


def temporal_split(df_merged, train_ratio=TRAIN_RATIO):
    print('=' * 65)
    print('STEP 2 - TEMPORAL SPLIT  (no shuffle)  [v2]')
    print('=' * 65)
    df = df_merged.sort_values('clean_date').reset_index(drop=True)
    split_idx    = int(len(df) * train_ratio)
    df_train_raw = df.iloc[:split_idx].copy()
    df_test_raw  = df.iloc[split_idx:].copy()
    print(f'  Train : {len(df_train_raw):,}  Test : {len(df_test_raw):,}')
    return df_train_raw, df_test_raw


def fit_feature_stats(df_train):
    numeric_cols = df_train.select_dtypes(include='number').columns.tolist()
    return {
        'resource_medians': df_train.groupby('line_item_resource_id')[numeric_cols].median(),
        'global_medians':   df_train[numeric_cols].median(),
    }


def _slope(s):
    if len(s) < 14:
        return np.nan
    x = np.arange(len(s))
    slope, *_ = linregress(x, s.values)
    return slope


def transform_features(df_input, stats):
    df  = df_input.copy()
    grp = df.groupby('line_item_resource_id')

    # Cost temporal
    df['rolling_7d_avg'] = grp['line_item_unblended_cost'].transform(
        lambda x: x.shift(1).rolling(7, min_periods=1).mean()
    )
    df['rolling_7d_std'] = grp['line_item_unblended_cost'].transform(
        lambda x: x.shift(1).rolling(7, min_periods=2).std()
    )
    df['cost_ratio_to_7d_avg'] = (
        df['line_item_unblended_cost'] / (df['rolling_7d_avg'] + 1e-6)
    )
    df['cost_diff']       = df['line_item_unblended_cost'] - df['rolling_7d_avg']
    df['cost_pct_change'] = grp['line_item_unblended_cost'].pct_change()

    rolling_median = grp['line_item_unblended_cost'].transform(
        lambda x: x.shift(1).rolling(14, min_periods=3).median()
    )
    rolling_mad = grp['line_item_unblended_cost'].transform(
        lambda x: x.shift(1).rolling(14, min_periods=3).apply(
            lambda y: np.median(np.abs(y - np.median(y))), raw=True
        )
    )
    df['robust_z'] = (
        0.6745 * (df['line_item_unblended_cost'] - rolling_median)
        / (rolling_mad + 1e-6)
    )

    # NEW v2: absolute spike = how many std above the rolling average
    # Catches high-cost anomalies where ratio stays ~1.0 but absolute gap is large
    df['absolute_cost_spike'] = (
        df['line_item_unblended_cost'] - 3 * df['rolling_7d_std'].fillna(0)
    ).clip(lower=0)

    # Trend
    df['cost_lag_28d']        = grp['line_item_unblended_cost'].shift(28)
    df['cost_pct_change_28d'] = (
        (df['line_item_unblended_cost'] - df['cost_lag_28d'])
        / (df['cost_lag_28d'] + 1e-6)
    )
    df['slope_14d'] = grp['line_item_unblended_cost'].transform(
        lambda x: x.shift(1).rolling(14, min_periods=14).apply(_slope)
    )

    # Calendar (removed dayofweek - low SHAP + drifts)
    df['month']      = pd.to_datetime(df['clean_date']).dt.month
    df['is_weekend'] = (pd.to_datetime(df['clean_date']).dt.dayofweek >= 5).astype(int)

    # Usage (removed usage_density, line_item_usage_amount)
    df['cost_per_unit_usage'] = (
        df['line_item_unblended_cost'] / (df['line_item_usage_amount'] + 1e-6)
    )
    df['age_days'] = grp.cumcount() + 1

    # CPU (removed cpu_max, idle_hours_continuous)
    cpu_h_cols = [c for c in df.columns if c.startswith('cpu_h')]
    df['cpu_variance_24h'] = df[cpu_h_cols].var(axis=1) if cpu_h_cols else 0

    # Tag quality
    df['team_missing'] = (
        df.get('resource_tags_user_team', pd.Series([''] * len(df)))
        .fillna('MISSING').eq('MISSING (Untagged)').astype(int)
    )
    df['owner_missing'] = (
        df.get('resource_tags_user_owner', pd.Series([np.nan] * len(df)))
        .isna().astype(int)
    )

    # Peer ratio
    peer_group = ['line_item_usage_account_id', 'line_item_product_code', 'clean_date']
    if all(c in df.columns for c in peer_group):
        df['peer_median_cost'] = df.groupby(peer_group)[
            'line_item_unblended_cost'
        ].transform('median')
        df['peer_ratio'] = (
            df['line_item_unblended_cost'] / (df['peer_median_cost'] + 1e-6)
        )
    else:
        df['peer_ratio'] = 1.0

    # NEW v2: DynamoDB service flag — helps isolate DDB FP pattern
    svc_col = next((c for c in ['line_item_product_code', 'derived_service_code']
                    if c in df.columns), None)
    df['ddb_flag'] = (
        (df[svc_col] == 'AmazonDynamoDB').astype(int)
        if svc_col else 0
    )

    # Imputation using train stats
    numeric_cols = df.select_dtypes(include='number').columns
    df[numeric_cols] = df[numeric_cols].replace([np.inf, -np.inf], np.nan)

    res_med    = stats['resource_medians']
    global_med = stats['global_medians']

    for col in numeric_cols:
        if not df[col].isna().any():
            continue
        fallback = float(global_med.get(col, 0))
        if col in res_med.columns:
            per_res = res_med[col]
            df[col] = df.apply(
                lambda r, c=col, pr=per_res, fb=fallback: (
                    pr.get(r['line_item_resource_id'], fb) if pd.isna(r[c]) else r[c]
                ),
                axis=1,
            )
        else:
            df[col] = df[col].fillna(fallback)

    return df


def build_xy(df_train_raw, df_test_raw):
    print('=' * 65)
    print('STEP 3 - LEAK-FREE FEATURE ENGINEERING  [v2]')
    print('=' * 65)

    train_stats  = fit_feature_stats(df_train_raw)
    df_train_fe  = transform_features(df_train_raw, train_stats)
    df_test_fe   = transform_features(df_test_raw,  train_stats)

    features = [f for f in FEATURE_COLS_V2 if f in df_train_fe.columns]

    X_train = df_train_fe[features].copy()
    y_train = df_train_fe['label_encoded'].copy()
    X_test  = df_test_fe[features].copy()
    y_test  = df_test_fe['label_encoded'].copy()

    print(f'  Features v2 : {len(features)}  (v1 had 29)')
    print(f'  X_train     : {X_train.shape}  |  X_test : {X_test.shape}')
    print(f'  New features: absolute_cost_spike, ddb_flag')
    print(f'  Removed     : cpu_max, line_item_usage_amount, usage_density,')
    print(f'                dayofweek, cost_pct_change, idle_hours_continuous')
    return X_train, y_train, X_test, y_test, train_stats, features


if __name__ == '__main__':
    DATA_DIR = '.'
    df_merged                 = load_and_merge(DATA_DIR)
    df_train_raw, df_test_raw = temporal_split(df_merged)
    X_train, y_train, X_test, y_test, train_stats, features = build_xy(
        df_train_raw, df_test_raw
    )
    print('Feature Engineering v2 complete.')
