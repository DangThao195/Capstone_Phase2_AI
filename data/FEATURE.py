# FEATURE.py  (updated for unified metrics_data/metrics.csv + cost_explorer_daily.csv)
# Step 1: Load & Merge  |  Step 2: Temporal Split  |  Step 3: Leak-Free Feature Engineering
# Ref: data/plan.md
#
# Data changes vs original:
#   - No more individual service CSVs (ec2_metrics.csv etc.)
#     -> Single file: ../metrics_data/metrics.csv
#   - No more cpu_h0..cpu_h23 columns
#     -> cpu_percent (scalar) + cpu_utilization_hourly (JSON array of 12 samples)
#   - New columns in metrics: database_connections, gpu_utilization, resource_type, event_type
#   - New file: cost_explorer_daily.csv (daily agg cost per account+service)
#     -> Used to compute account_daily_cost_ratio feature

import json
import os
import warnings

import numpy as np
import pandas as pd
from scipy.stats import linregress

warnings.filterwarnings('ignore')

# ── Constants ────────────────────────────────────────────────────────────────
LABEL_MAP = {'normal': 0, 'anomaly': 1, 'benign': 2}

# Single unified metrics file (relative to data_dir)
METRICS_FILE     = os.path.join('..', 'metrics_data', 'metrics.csv')
COST_DAILY_FILE  = 'cost_explorer_daily.csv'

FEATURE_COLS = [
    # Cost temporal
    'line_item_unblended_cost', 'rolling_7d_avg', 'rolling_7d_std',
    'cost_ratio_to_7d_avg', 'cost_diff', 'cost_pct_change', 'robust_z',
    # Trend
    'slope_14d', 'cost_pct_change_28d', 'age_days',
    # Calendar
    'dayofweek', 'month', 'is_weekend',
    # Usage
    'line_item_usage_amount', 'usage_density', 'cost_per_unit_usage',
    # CPU (from cpu_utilization_hourly parsed into 12 samples)
    'cpu_mean', 'cpu_std', 'cpu_max', 'cpu_min', 'cpu_variance_12h',
    # I/O, memory
    'memory_mib', 'network_in_bytes', 'network_out_bytes', 'disk_io_ops',
    # DB & GPU (new from unified metrics)
    'database_connections', 'gpu_utilization',
    # Tag quality
    'team_missing', 'owner_missing',
    # Peer ratio + account daily cost ratio (new from cost_explorer_daily)
    'peer_ratio', 'account_daily_cost_ratio',
]

TRAIN_RATIO = 0.80


# ── Step 1: Load & Merge ─────────────────────────────────────────────────────
def _parse_cpu_hourly(val) -> np.ndarray:
    """Parse cpu_utilization_hourly JSON array -> numpy array of 12 floats."""
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return np.full(12, np.nan)
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            return np.array(parsed, dtype=float)
        except (json.JSONDecodeError, ValueError):
            return np.full(12, np.nan)
    if isinstance(val, (list, np.ndarray)):
        return np.array(val, dtype=float)
    return np.full(12, np.nan)


def _max_idle_streak_12(arr: np.ndarray) -> int:
    """Max consecutive 5-min samples with CPU < 5% (out of 12 samples)."""
    max_streak = current = 0
    for v in arr:
        if not np.isnan(v) and v < 5:
            current += 1
            max_streak = max(max_streak, current)
        else:
            current = 0
    return max_streak


def _load_metrics(data_dir: str) -> pd.DataFrame:
    """
    Load unified metrics_data/metrics.csv.
    Parse cpu_utilization_hourly JSON -> cpu_mean, cpu_std, cpu_max, cpu_min, cpu_variance_12h.
    Aggregate hourly rows -> 1 row per (resource_id, clean_date) using daily mean.
    """
    metrics_path = os.path.join(data_dir, METRICS_FILE)
    if not os.path.exists(metrics_path):
        # Try same-dir fallback
        metrics_path = os.path.join(data_dir, 'metrics.csv')
    if not os.path.exists(metrics_path):
        raise FileNotFoundError(f'metrics.csv not found at {metrics_path}')

    df = pd.read_csv(metrics_path)
    df['clean_date'] = pd.to_datetime(df['timestamp']).dt.tz_localize(None).dt.normalize()

    # Parse cpu_utilization_hourly (12-sample array) -> per-row CPU stats
    cpu_arrays = df['cpu_utilization_hourly'].apply(_parse_cpu_hourly)

    df['cpu_mean']         = cpu_arrays.apply(lambda a: float(np.nanmean(a)) if not np.all(np.isnan(a)) else np.nan)
    df['cpu_std']          = cpu_arrays.apply(lambda a: float(np.nanstd(a))  if not np.all(np.isnan(a)) else np.nan)
    df['cpu_max']          = cpu_arrays.apply(lambda a: float(np.nanmax(a))  if not np.all(np.isnan(a)) else np.nan)
    df['cpu_min']          = cpu_arrays.apply(lambda a: float(np.nanmin(a))  if not np.all(np.isnan(a)) else np.nan)
    df['cpu_variance_12h'] = cpu_arrays.apply(lambda a: float(np.nanvar(a))  if not np.all(np.isnan(a)) else np.nan)

    # Use cpu_percent as primary CPU signal when hourly array is sparse
    df['cpu_mean'] = df['cpu_mean'].fillna(df['cpu_percent'])

    # Aggregate hourly -> daily (metrics file is hourly, CUR is daily)
    agg = {
        'cpu_mean':            'mean',
        'cpu_std':             'mean',
        'cpu_max':             'max',
        'cpu_min':             'min',
        'cpu_variance_12h':    'mean',
        'memory_mib':          'mean',
        'network_in_bytes':    'sum',
        'network_out_bytes':   'sum',
        'disk_io_ops':         'sum',
        'database_connections':'mean',
        'gpu_utilization':     'mean',
        'event_type':          'first',
        'resource_type':       'first',
    }
    agg_cols = {k: v for k, v in agg.items() if k in df.columns}

    # Label: majority vote per resource-day (not 'first' which picks arbitrary hour)
    # Priority: anomaly > benign > normal (to not miss anomaly events)
    LABEL_PRIORITY = {'anomaly': 2, 'benign': 1, 'normal': 0}

    def majority_label(series):
        counts = series.value_counts()
        # Dung majority vote thuan: lay nhan xuat hien nhieu nhat trong ngay
        # Khong override benign bang anomaly - de giu du lieu benign phan bo theo thoi gian
        return counts.index[0]

    df_daily = (
        df.groupby(['resource_id', 'clean_date'], as_index=False)
        .agg(agg_cols)
    )

    # Merge majority label separately
    label_daily = (
        df.groupby(['resource_id', 'clean_date'])['label']
        .apply(majority_label)
        .reset_index()
    )
    df_daily = df_daily.merge(label_daily, on=['resource_id', 'clean_date'], how='left')

    # Fix 1: Clip cpu values to valid range [0, 100]
    for col in ['cpu_mean', 'cpu_std', 'cpu_max', 'cpu_min']:
        if col in df_daily.columns:
            df_daily[col] = df_daily[col].clip(0, 100)
    if 'cpu_variance_12h' in df_daily.columns:
        df_daily['cpu_variance_12h'] = df_daily['cpu_variance_12h'].clip(lower=0)

    # Fix 2: Zero out database_connections for non-DB resource types
    if 'resource_type' in df_daily.columns and 'database_connections' in df_daily.columns:
        non_db_mask = ~df_daily['resource_type'].isin(['database', 'cache'])
        df_daily.loc[non_db_mask, 'database_connections'] = 0.0
    return df_daily


def _load_cost_explorer(data_dir: str) -> pd.DataFrame:
    """
    Load cost_explorer_daily.csv and return daily account-level cost totals.
    Used to compute account_daily_cost_ratio.
    """
    path = os.path.join(data_dir, COST_DAILY_FILE)
    if not os.path.exists(path):
        return pd.DataFrame()

    df = pd.read_csv(path)
    df['clean_date'] = pd.to_datetime(df['date']).dt.tz_localize(None).dt.normalize()

    # Daily total per account (across all services)
    df_account_daily = (
        df.groupby(['linked_account_id', 'clean_date'], as_index=False)
        ['unblended_cost'].sum()
        .rename(columns={'unblended_cost': 'account_daily_total_cost'})
    )
    return df_account_daily


def load_and_merge(data_dir: str = '.') -> pd.DataFrame:
    """
    Load CUR + unified metrics + cost_explorer_daily, join them.

    Join keys:
      CUR <-> metrics     : (line_item_resource_id, clean_date) = (resource_id, clean_date)
      CUR <-> cost_daily  : (line_item_usage_account_id, clean_date) = (linked_account_id, clean_date)

    Input  : data_dir containing cur_line_items.csv, cost_explorer_daily.csv
             and ../metrics_data/metrics.csv
    Output : df_merged with label_encoded, NO rolling features yet
    """
    print('=' * 65)
    print('STEP 1 - LOAD & MERGE')
    print('=' * 65)

    # CUR
    cur_path = os.path.join(data_dir, 'cur_line_items.csv')
    if not os.path.exists(cur_path):
        raise FileNotFoundError(f'Not found: {cur_path}')
    df_cur = pd.read_csv(cur_path)
    df_cur['clean_date'] = pd.to_datetime(df_cur['line_item_usage_start_date']).dt.tz_localize(None).dt.normalize()
    print(f'  CUR loaded           : {len(df_cur):,} rows')

    # Metrics (unified)
    df_metrics = _load_metrics(data_dir)
    print(f'  Metrics loaded (daily): {len(df_metrics):,} rows')

    # Cost explorer daily
    df_cost_daily = _load_cost_explorer(data_dir)
    if not df_cost_daily.empty:
        print(f'  Cost daily loaded    : {len(df_cost_daily):,} rows')
    else:
        print('  Cost daily           : not found, skipping account_daily_cost_ratio')

    # Merge CUR <-> metrics (inner join on resource_id + date)
    df = pd.merge(
        df_cur, df_metrics,
        left_on=['line_item_resource_id', 'clean_date'],
        right_on=['resource_id',          'clean_date'],
        how='inner',
    )
    print(f'  After CUR x metrics  : {len(df):,} rows')

    # Merge with cost_explorer_daily (left join, enrich with account context)
    if not df_cost_daily.empty:
        df = pd.merge(
            df, df_cost_daily,
            left_on=['line_item_usage_account_id', 'clean_date'],
            right_on=['linked_account_id',         'clean_date'],
            how='left',
        )
        # account_daily_cost_ratio = this resource cost / total account cost that day
        df['account_daily_cost_ratio'] = (
            df['line_item_unblended_cost'] / (df['account_daily_total_cost'] + 1e-6)
        )
    else:
        df['account_daily_cost_ratio'] = 0.0

    df = df.sort_values(['line_item_resource_id', 'clean_date']).reset_index(drop=True)
    df['label_encoded'] = df['label'].map(LABEL_MAP)

    print(f'  Final rows           : {len(df):,}')
    print(df['label'].value_counts().to_string())
    return df


# ── Step 2: Temporal Split ───────────────────────────────────────────────────
def temporal_split(df_merged: pd.DataFrame, train_ratio: float = TRAIN_RATIO) -> tuple:
    """Sort by clean_date then split chronologically. NO shuffle."""
    print('=' * 65)
    print('STEP 2 - TEMPORAL SPLIT  (no shuffle)')
    print('=' * 65)
    df = df_merged.sort_values('clean_date').reset_index(drop=True)
    split_idx    = int(len(df) * train_ratio)
    df_train_raw = df.iloc[:split_idx].copy()
    df_test_raw  = df.iloc[split_idx:].copy()
    t0, t1 = df_train_raw['clean_date'].min().date(), df_train_raw['clean_date'].max().date()
    v0, v1 = df_test_raw['clean_date'].min().date(),  df_test_raw['clean_date'].max().date()
    print(f'  Train : {len(df_train_raw):,}  ({t0} -> {t1})')
    print(f'  Test  : {len(df_test_raw):,}  ({v0} -> {v1})')
    return df_train_raw, df_test_raw


# ── Step 3a: Fit stats ───────────────────────────────────────────────────────
def fit_feature_stats(df_train: pd.DataFrame) -> dict:
    numeric_cols = df_train.select_dtypes(include='number').columns.tolist()
    return {
        'resource_medians': df_train.groupby('line_item_resource_id')[numeric_cols].median(),
        'global_medians':   df_train[numeric_cols].median(),
    }


# ── Step 3b: Transform features ─────────────────────────────────────────────
def _slope(s: pd.Series) -> float:
    if len(s) < 14:
        return np.nan
    x = np.arange(len(s))
    slope, *_ = linregress(x, s.values)
    return slope


def transform_features(df_input: pd.DataFrame, stats: dict) -> pd.DataFrame:
    """
    Compute all engineered features. No look-ahead: shift(1) on all rolling windows.
    Imputation uses train stats only.
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
    df['cost_pct_change']       = grp['line_item_unblended_cost'].pct_change()

    rolling_median = grp['line_item_unblended_cost'].transform(
        lambda x: x.shift(1).rolling(14, min_periods=3).median()
    )
    rolling_mad = grp['line_item_unblended_cost'].transform(
        lambda x: x.shift(1).rolling(14, min_periods=3).apply(
            lambda y: np.median(np.abs(y - np.median(y))), raw=True
        )
    )
    df['robust_z'] = 0.6745 * (df['line_item_unblended_cost'] - rolling_median) / (rolling_mad + 1e-6)

    # Trend
    df['cost_lag_28d']        = grp['line_item_unblended_cost'].shift(28)
    df['cost_pct_change_28d'] = (df['line_item_unblended_cost'] - df['cost_lag_28d']) / (df['cost_lag_28d'] + 1e-6)
    df['slope_14d']           = grp['line_item_unblended_cost'].transform(
        lambda x: x.shift(1).rolling(14, min_periods=14).apply(_slope)
    )

    # Calendar
    df['dayofweek']  = pd.to_datetime(df['clean_date']).dt.dayofweek
    df['month']      = pd.to_datetime(df['clean_date']).dt.month
    df['is_weekend'] = (df['dayofweek'] >= 5).astype(int)

    # Usage
    df['cost_per_unit_usage'] = df['line_item_unblended_cost'] / (df['line_item_usage_amount'] + 1e-6)
    df['usage_density']       = df['line_item_usage_amount'] / 24
    df['age_days']            = grp.cumcount() + 1

    # cpu_variance_12h from parsed hourly array (already computed in _load_metrics)
    # Fallback: if column missing, derive from cpu_mean rolling std
    if 'cpu_variance_12h' not in df.columns:
        df['cpu_variance_12h'] = 0.0

    # database_connections: fill NaN for non-DB resources
    if 'database_connections' in df.columns:
        df['database_connections'] = df['database_connections'].fillna(0.0)
    else:
        df['database_connections'] = 0.0

    # gpu_utilization: fill NaN for non-GPU resources
    if 'gpu_utilization' in df.columns:
        df['gpu_utilization'] = df['gpu_utilization'].fillna(0.0)
    else:
        df['gpu_utilization'] = 0.0

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

    # account_daily_cost_ratio already computed in load_and_merge
    if 'account_daily_cost_ratio' not in df.columns:
        df['account_daily_cost_ratio'] = 0.0

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
                ), axis=1,
            )
        else:
            df[col] = df[col].fillna(fallback)

    return df


# ── Step 3c: Build X/y ───────────────────────────────────────────────────────
def build_xy(df_train_raw: pd.DataFrame, df_test_raw: pd.DataFrame) -> tuple:
    print('=' * 65)
    print('STEP 3 - LEAK-FREE FEATURE ENGINEERING')
    print('=' * 65)
    train_stats = fit_feature_stats(df_train_raw)
    df_train_fe = transform_features(df_train_raw, train_stats)
    df_test_fe  = transform_features(df_test_raw,  train_stats)
    features = [f for f in FEATURE_COLS if f in df_train_fe.columns]
    X_train = df_train_fe[features].copy()
    y_train = df_train_fe['label_encoded'].copy()
    X_test  = df_test_fe[features].copy()
    y_test  = df_test_fe['label_encoded'].copy()
    print(f'  Features : {len(features)}')
    print(f'  X_train  : {X_train.shape}  |  X_test : {X_test.shape}')
    print(y_train.value_counts().sort_index().to_string())
    return X_train, y_train, X_test, y_test, train_stats, features


# ── Main ─────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    DATA_DIR = '.'
    df_merged                 = load_and_merge(DATA_DIR)
    df_train_raw, df_test_raw = temporal_split(df_merged)
    X_train, y_train, X_test, y_test, train_stats, features = build_xy(df_train_raw, df_test_raw)
    print('Feature Engineering complete.')