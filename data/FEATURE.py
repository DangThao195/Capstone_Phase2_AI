# FEATURE.py
# Step 1: Load & Merge  |  Step 2: Temporal Split  |  Step 3: Leak-Free Feature Engineering
# Ref: data/plan.md - AWS Cost Anomaly Detection

import os
import warnings
import numpy as np
import pandas as pd
from scipy.stats import linregress

warnings.filterwarnings('ignore')

# ── Constants ────────────────────────────────────────────────────────────────
LABEL_MAP = {'normal': 0, 'anomaly': 1, 'benign': 2}

METRICS_MAPPING = {
    'AmazonEC2':       'ec2_metrics.csv',
    'AmazonRDS':       'rds_metrics.csv',
    'AmazonDynamoDB':  'ddb_metrics.csv',
    'AmazonSageMaker': 'sagemaker_metrics.csv',
    'AWSDataTransfer': 'other_services_metrics.csv',
    'AWSELB':          'other_services_metrics.csv',
}

FEATURE_COLS = [
    'line_item_unblended_cost', 'rolling_7d_avg', 'rolling_7d_std',
    'cost_ratio_to_7d_avg', 'cost_diff', 'cost_pct_change', 'robust_z',
    'slope_14d', 'cost_pct_change_28d', 'age_days',
    'dayofweek', 'month', 'is_weekend',
    'line_item_usage_amount', 'usage_density', 'cost_per_unit_usage',
    'cpu_mean', 'cpu_std', 'cpu_max', 'cpu_min', 'cpu_variance_24h',
    'idle_hours_continuous', 'memory_mib', 'network_in_bytes',
    'network_out_bytes', 'disk_io_ops',
    'team_missing', 'owner_missing', 'peer_ratio',
]

TRAIN_RATIO = 0.80


# ── Step 1: Load & Merge ─────────────────────────────────────────────────────
def _max_idle_streak(row: np.ndarray) -> int:
    """Max consecutive hours with CPU utilisation below 5 percent."""
    max_streak = current = 0
    for v in row:
        if v:
            current += 1
            max_streak = max(max_streak, current)
        else:
            current = 0
    return max_streak


def load_and_merge(data_dir: str = '.') -> pd.DataFrame:
    """
    Load CUR + all metrics CSVs, compute CPU features,
    inner-join on (resource_id, clean_date), encode labels.

    Input  : raw CSV files in data_dir
    Output : df_merged sorted by (resource_id, clean_date)
             contains label_encoded, NO rolling features yet
    """
    print('=' * 65)
    print('STEP 1 - LOAD & MERGE')
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
            print(f'  Skip (not found): {filename}')
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

    df = df.sort_values(
        ['line_item_resource_id', 'clean_date']
    ).reset_index(drop=True)

    df['label_encoded'] = df['label'].map(LABEL_MAP)
    print(df['label'].value_counts().to_string())
    return df


# ── Step 2: Temporal Split ───────────────────────────────────────────────────
def temporal_split(
    df_merged: pd.DataFrame,
    train_ratio: float = TRAIN_RATIO,
) -> tuple:
    """
    Sort by clean_date then split chronologically. NO shuffle.

    Input  : df_merged
    Output : df_train_raw (80%), df_test_raw (20%)
    """
    print('=' * 65)
    print('STEP 2 - TEMPORAL SPLIT  (no shuffle)')
    print('=' * 65)

    df = df_merged.sort_values('clean_date').reset_index(drop=True)
    split_idx = int(len(df) * train_ratio)

    df_train_raw = df.iloc[:split_idx].copy()
    df_test_raw  = df.iloc[split_idx:].copy()

    t0, t1 = df_train_raw['clean_date'].min().date(), df_train_raw['clean_date'].max().date()
    v0, v1 = df_test_raw['clean_date'].min().date(),  df_test_raw['clean_date'].max().date()
    print(f'  Train : {len(df_train_raw):,}  ({t0} -> {t1})')
    print(f'  Test  : {len(df_test_raw):,}  ({v0} -> {v1})')
    return df_train_raw, df_test_raw


# ── Step 3a: Fit stats on train ──────────────────────────────────────────────
def fit_feature_stats(df_train: pd.DataFrame) -> dict:
    """
    Compute per-resource and global medians from train split only.
    These are later used for imputing val/test splits (no leakage).

    Input  : df_train_raw
    Output : stats dict {resource_medians, global_medians}
    """
    numeric_cols = df_train.select_dtypes(include='number').columns.tolist()
    return {
        'resource_medians': df_train.groupby('line_item_resource_id')[numeric_cols].median(),
        'global_medians':   df_train[numeric_cols].median(),
    }


# ── Step 3b: Transform features ─────────────────────────────────────────────
def _slope(s: pd.Series) -> float:
    """Linear regression slope over a rolling window."""
    if len(s) < 14:
        return np.nan
    x = np.arange(len(s))
    slope, *_ = linregress(x, s.values)
    return slope


def transform_features(df_input: pd.DataFrame, stats: dict) -> pd.DataFrame:
    """
    Compute all engineered features for one split.
    Rolling/lag use .shift(1) so no current-day info leaks into the window.
    Imputation uses train stats only.

    Input  : raw split DataFrame + stats from fit_feature_stats(train)
    Output : DataFrame with all feature columns + label_encoded
    """
    df  = df_input.copy()
    grp = df.groupby('line_item_resource_id')

    # Cost temporal features (shift prevents look-ahead)
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

    # Trend
    df['cost_lag_28d']        = grp['line_item_unblended_cost'].shift(28)
    df['cost_pct_change_28d'] = (
        (df['line_item_unblended_cost'] - df['cost_lag_28d'])
        / (df['cost_lag_28d'] + 1e-6)
    )
    df['slope_14d'] = grp['line_item_unblended_cost'].transform(
        lambda x: x.shift(1).rolling(14, min_periods=14).apply(_slope)
    )

    # Calendar
    df['dayofweek']  = pd.to_datetime(df['clean_date']).dt.dayofweek
    df['month']      = pd.to_datetime(df['clean_date']).dt.month
    df['is_weekend'] = (df['dayofweek'] >= 5).astype(int)

    # Usage
    df['cost_per_unit_usage'] = (
        df['line_item_unblended_cost'] / (df['line_item_usage_amount'] + 1e-6)
    )
    df['usage_density'] = df['line_item_usage_amount'] / 24
    df['age_days']      = grp.cumcount() + 1

    # CPU variance across 24 hourly buckets
    cpu_h_cols = [c for c in df.columns if c.startswith('cpu_h')]
    df['cpu_variance_24h'] = df[cpu_h_cols].var(axis=1) if cpu_h_cols else 0

    # Tag quality flags
    df['team_missing'] = (
        df.get('resource_tags_user_team', pd.Series([''] * len(df)))
        .fillna('MISSING').eq('MISSING (Untagged)').astype(int)
    )
    df['owner_missing'] = (
        df.get('resource_tags_user_owner', pd.Series([np.nan] * len(df)))
        .isna().astype(int)
    )

    # Peer ratio (computed within this split only, no cross-split leakage)
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

    # Imputation using train stats (no leakage for val/test)
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


# ── Step 3c: Build X/y arrays ────────────────────────────────────────────────
def build_xy(df_train_raw: pd.DataFrame, df_test_raw: pd.DataFrame) -> tuple:
    """
    Fit stats on train, transform both splits, return X/y.

    Input  : df_train_raw, df_test_raw
    Output : X_train, y_train, X_test, y_test, train_stats, features
    """
    print('=' * 65)
    print('STEP 3 - LEAK-FREE FEATURE ENGINEERING')
    print('=' * 65)

    train_stats  = fit_feature_stats(df_train_raw)
    df_train_fe  = transform_features(df_train_raw, train_stats)
    df_test_fe   = transform_features(df_test_raw,  train_stats)

    features = [f for f in FEATURE_COLS if f in df_train_fe.columns]

    X_train = df_train_fe[features].copy()
    y_train = df_train_fe['label_encoded'].copy()
    X_test  = df_test_fe[features].copy()
    y_test  = df_test_fe['label_encoded'].copy()

    print(f'  Features : {len(features)}')
    print(f'  X_train  : {X_train.shape}  |  X_test : {X_test.shape}')
    print(f'  y_train dist:')
    print(y_train.value_counts().sort_index().to_string())
    return X_train, y_train, X_test, y_test, train_stats, features


# ── Main ─────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    DATA_DIR = '.'
    df_merged                 = load_and_merge(DATA_DIR)
    df_train_raw, df_test_raw = temporal_split(df_merged)
    X_train, y_train, X_test, y_test, train_stats, features = build_xy(
        df_train_raw, df_test_raw
    )
    print('Feature Engineering complete.')