# FEATURE_v2.py  (updated for unified metrics_data/metrics.csv + cost_explorer_daily.csv)
# v2 improvements: remove low-SHAP+high-drift features, add new signals
#
# Changes vs FEATURE.py v1:
#   REMOVED (low SHAP + drifts):  cpu_max, line_item_usage_amount, usage_density,
#                                  dayofweek, cost_pct_change, idle_hours_continuous (not in new data)
#   ADDED:                         absolute_cost_spike, ddb_flag
#   NEW from unified metrics:      database_connections, gpu_utilization
#   NEW from cost_explorer_daily:  account_daily_cost_ratio
#   CPU source:                    cpu_utilization_hourly (12 samples) instead of cpu_h0..h23

import json
import os
import warnings

import numpy as np
import pandas as pd
from scipy.stats import linregress

# Reuse shared helpers from FEATURE.py
from FEATURE import (
    LABEL_MAP, METRICS_FILE, COST_DAILY_FILE, TRAIN_RATIO,
    _parse_cpu_hourly, _load_metrics, _load_cost_explorer,
    temporal_split, fit_feature_stats, _slope,
)

warnings.filterwarnings('ignore')

# v2 feature set: 27 features
FEATURE_COLS_V2 = [
    # Cost temporal
    'line_item_unblended_cost', 'rolling_7d_avg', 'rolling_7d_std',
    'cost_ratio_to_7d_avg', 'cost_diff', 'robust_z',
    # Trend
    'slope_14d', 'cost_pct_change_28d', 'age_days',
    # Calendar (dayofweek removed - low SHAP + drifts)
    'month', 'is_weekend',
    # Usage (line_item_usage_amount, usage_density removed)
    'cost_per_unit_usage',
    # CPU (cpu_max removed)
    'cpu_mean', 'cpu_std', 'cpu_min', 'cpu_variance_12h',
    # I/O & memory
    'memory_mib', 'network_in_bytes', 'network_out_bytes', 'disk_io_ops',
    # New: DB connections + GPU (from unified metrics)
    'database_connections', 'gpu_utilization',
    # Tag quality
    'team_missing', 'owner_missing',
    # Peer + account context
    'peer_ratio', 'account_daily_cost_ratio',
    # NEW v2 signals
    'absolute_cost_spike',   # cost - 3*rolling_7d_std (catches high-cost FN)
    'ddb_flag',              # 1 if AmazonDynamoDB (reduce DDB FP)
]


def load_and_merge(data_dir: str = '.') -> pd.DataFrame:
    """Same as FEATURE.py but tagged as v2."""
    print('=' * 65)
    print('STEP 1 - LOAD & MERGE  [v2]')
    print('=' * 65)

    cur_path = os.path.join(data_dir, 'cur_line_items.csv')
    if not os.path.exists(cur_path):
        raise FileNotFoundError(f'Not found: {cur_path}')
    df_cur = pd.read_csv(cur_path)
    df_cur['clean_date'] = pd.to_datetime(df_cur['line_item_usage_start_date']).dt.tz_localize(None).dt.normalize()
    print(f'  CUR loaded            : {len(df_cur):,} rows')

    df_metrics    = _load_metrics(data_dir)
    print(f'  Metrics loaded (daily): {len(df_metrics):,} rows')

    df_cost_daily = _load_cost_explorer(data_dir)
    if not df_cost_daily.empty:
        print(f'  Cost daily loaded     : {len(df_cost_daily):,} rows')

    df = pd.merge(
        df_cur, df_metrics,
        left_on=['line_item_resource_id', 'clean_date'],
        right_on=['resource_id',          'clean_date'],
        how='inner',
    )
    print(f'  After CUR x metrics   : {len(df):,} rows')

    if not df_cost_daily.empty:
        df = pd.merge(
            df, df_cost_daily,
            left_on=['line_item_usage_account_id', 'clean_date'],
            right_on=['linked_account_id',         'clean_date'],
            how='left',
        )
        df['account_daily_cost_ratio'] = (
            df['line_item_unblended_cost'] / (df['account_daily_total_cost'] + 1e-6)
        )
    else:
        df['account_daily_cost_ratio'] = 0.0

    df = df.sort_values(['line_item_resource_id', 'clean_date']).reset_index(drop=True)
    df['label_encoded'] = df['label'].map(LABEL_MAP)
    print(f'  Final rows            : {len(df):,}')
    print(df['label'].value_counts().to_string())
    return df


def transform_features(df_input: pd.DataFrame, stats: dict) -> pd.DataFrame:
    """
    v2 feature engineering.
    Same as FEATURE.py but:
      - absolute_cost_spike added
      - ddb_flag added
      - dayofweek, cost_pct_change, line_item_usage_amount, usage_density NOT computed
        (removed from feature set to reduce drift noise)
    """
    df  = df_input.copy()
    grp = df.groupby('line_item_resource_id')

    # Cost temporal
    df['rolling_7d_avg'] = grp['line_item_unblended_cost'].transform(
        lambda x: x.shift(1).rolling(7, min_periods=1).mean()
    )
    df['rolling_7d_std'] = grp['line_item_unblended_cost'].transform(
        lambda x: x.shift(1).rolling(7, min_periods=2).std()
    )
    df['cost_ratio_to_7d_avg'] = df['line_item_unblended_cost'] / (df['rolling_7d_avg'] + 1e-6)
    df['cost_diff']             = df['line_item_unblended_cost'] - df['rolling_7d_avg']

    rolling_median = grp['line_item_unblended_cost'].transform(
        lambda x: x.shift(1).rolling(14, min_periods=3).median()
    )
    rolling_mad = grp['line_item_unblended_cost'].transform(
        lambda x: x.shift(1).rolling(14, min_periods=3).apply(
            lambda y: np.median(np.abs(y - np.median(y))), raw=True
        )
    )
    df['robust_z'] = 0.6745 * (df['line_item_unblended_cost'] - rolling_median) / (rolling_mad + 1e-6)

    # NEW v2: absolute spike = excess cost above 3-sigma band
    df['absolute_cost_spike'] = (
        df['line_item_unblended_cost'] - 3 * df['rolling_7d_std'].fillna(0)
    ).clip(lower=0)

    # Trend
    df['cost_lag_28d']        = grp['line_item_unblended_cost'].shift(28)
    df['cost_pct_change_28d'] = (df['line_item_unblended_cost'] - df['cost_lag_28d']) / (df['cost_lag_28d'] + 1e-6)
    df['slope_14d']           = grp['line_item_unblended_cost'].transform(
        lambda x: x.shift(1).rolling(14, min_periods=14).apply(_slope)
    )

    # Calendar (no dayofweek in v2)
    df['month']      = pd.to_datetime(df['clean_date']).dt.month
    df['is_weekend'] = (pd.to_datetime(df['clean_date']).dt.dayofweek >= 5).astype(int)

    # Usage (no usage_density, no line_item_usage_amount in v2)
    df['cost_per_unit_usage'] = df['line_item_unblended_cost'] / (df['line_item_usage_amount'] + 1e-6)
    df['age_days']            = grp.cumcount() + 1

    # cpu_variance_12h
    if 'cpu_variance_12h' not in df.columns:
        df['cpu_variance_12h'] = 0.0

    # DB connections + GPU (new unified metrics columns)
    df['database_connections'] = df.get('database_connections', pd.Series([0.0]*len(df))).fillna(0.0)
    df['gpu_utilization']      = df.get('gpu_utilization',      pd.Series([0.0]*len(df))).fillna(0.0)

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
        df['peer_median_cost'] = df.groupby(peer_group)['line_item_unblended_cost'].transform('median')
        df['peer_ratio']       = df['line_item_unblended_cost'] / (df['peer_median_cost'] + 1e-6)
    else:
        df['peer_ratio'] = 1.0

    # account_daily_cost_ratio
    if 'account_daily_cost_ratio' not in df.columns:
        df['account_daily_cost_ratio'] = 0.0

    # NEW v2: DynamoDB flag
    svc_col = next((c for c in ['line_item_product_code', 'derived_service_code'] if c in df.columns), None)
    df['ddb_flag'] = ((df[svc_col] == 'AmazonDynamoDB').astype(int) if svc_col else 0)

    # Imputation
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
                ), axis=1,
            )
        else:
            df[col] = df[col].fillna(fallback)

    return df


def build_xy(df_train_raw: pd.DataFrame, df_test_raw: pd.DataFrame) -> tuple:
    print('=' * 65)
    print('STEP 3 - LEAK-FREE FEATURE ENGINEERING  [v2]')
    print('=' * 65)
    train_stats = fit_feature_stats(df_train_raw)
    df_train_fe = transform_features(df_train_raw, train_stats)
    df_test_fe  = transform_features(df_test_raw,  train_stats)
    features = [f for f in FEATURE_COLS_V2 if f in df_train_fe.columns]
    X_train = df_train_fe[features].copy()
    y_train = df_train_fe['label_encoded'].copy()
    X_test  = df_test_fe[features].copy()
    y_test  = df_test_fe['label_encoded'].copy()
    print(f'  Features v2 : {len(features)}  (baseline had 29)')
    print(f'  X_train     : {X_train.shape}  |  X_test : {X_test.shape}')
    print(f'  New cols: database_connections, gpu_utilization, account_daily_cost_ratio,')
    print(f'            absolute_cost_spike, ddb_flag, cpu_variance_12h')
    return X_train, y_train, X_test, y_test, train_stats, features


if __name__ == '__main__':
    DATA_DIR = '.'
    df_merged                 = load_and_merge(DATA_DIR)
    df_train_raw, df_test_raw = temporal_split(df_merged)
    X_train, y_train, X_test, y_test, train_stats, features = build_xy(df_train_raw, df_test_raw)
    print('Feature Engineering v2 complete.')