"""
=============================================================================
  improvements.py — Bản Cải Tiến Hệ Thống Phát Hiện Chi Phí Bất Thường
=============================================================================

Script này triển khai 6 đề xuất cải tiến từ bản Deep Review:

  1. Loại bỏ/giảm Feature-Label Circular Dependency
  2. Thêm Purged Gap (7 ngày) vào Walk-Forward Validation
  3. Bổ sung metrics: MCC, Balanced Accuracy
  4. Fix Flash Sale false alarm bằng utilization features
  5. Report metrics đầy đủ
  6. So sánh kết quả có gap vs không gap

Cách sử dụng:
  - Chạy trực tiếp: python improvements.py
  - Hoặc copy từng section vào notebook detect.ipynb

Yêu cầu:
  - Chạy sau khi đã load dữ liệu (cells 1-4 của notebook gốc)
  - Cần có DataFrame `df` đã merge CUR + metrics
  - Cần có `timeline`, `folds_def`, `cpu_h_cols` từ notebook gốc
=============================================================================
"""

import pandas as pd
import numpy as np
import os
import sys
import glob
import xgboost as xgb
import shap
import seaborn as sns
from sklearn.metrics import (
    classification_report, confusion_matrix,
    precision_recall_curve, roc_curve, auc,
    f1_score, matthews_corrcoef, balanced_accuracy_score
)
from sklearn.model_selection import GroupKFold
from sklearn.preprocessing import RobustScaler
from sklearn.utils.class_weight import compute_sample_weight
import matplotlib
matplotlib.use('Agg')  # Non-interactive backend for script mode
import matplotlib.pyplot as plt
import hashlib
from scipy.stats import linregress

# Fix Windows cp1252 encoding for Vietnamese text
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

np.random.seed(42)


# =============================================================================
# PHẦN 1: LOAD DỮ LIỆU (giống notebook gốc)
# =============================================================================
def load_data():
    """Load và merge dữ liệu CUR + metrics."""
    CUR_PATH = os.path.join("..", "..", "data", "tf2-finops", "cur_line_items.csv")
    METRICS_DIR = os.path.join("..", "..", "data", "metrics_haikhoa")

    print(f"Đang tải dữ liệu CUR từ: {CUR_PATH}...")
    cur = pd.read_csv(CUR_PATH)
    cur['date'] = pd.to_datetime(cur['line_item_usage_start_date']).dt.tz_localize(None)

    print(f"Đang tải dữ liệu metrics hệ thống từ: {METRICS_DIR}...")
    metrics_files = glob.glob(os.path.join(METRICS_DIR, "*.csv"))
    dfs = []
    for f in metrics_files:
        print(f"  Đọc file metrics: {os.path.basename(f)}...")
        dfs.append(pd.read_csv(f))
    metrics = pd.concat(dfs, ignore_index=True)
    metrics['date'] = pd.to_datetime(metrics['timestamp']).dt.tz_localize(None)

    metrics_cols = ['cpu_percent', 'memory_mib', 'network_in_bytes',
                    'network_out_bytes', 'disk_io_ops', 'database_connections',
                    'gpu_utilization']
    cpu_h_cols = [f'cpu_h{i}' for i in range(24)]
    for col in metrics_cols + cpu_h_cols:
        if col in metrics.columns:
            metrics[col] = metrics[col].fillna(0.0)
        else:
            metrics[col] = 0.0

    print("Đang kết nối dữ liệu chi phí và hiệu năng hệ thống...")
    df = pd.merge(cur, metrics,
                  left_on=['line_item_resource_id', 'date'],
                  right_on=['resource_id', 'date'], how='inner')
    print(f"Kích thước dữ liệu hợp nhất: {df.shape}")
    return df, cpu_h_cols


# =============================================================================
# PHẦN 2: FEATURE ENGINEERING CẢI TIẾN
# =============================================================================
def build_features_improved(df, cpu_h_cols):
    """
    Kỹ nghệ đặc trưng cải tiến:
    - GIỮ LẠI các cost features nhưng THÊM utilization/behavior features
      để model có thể phân biệt anomaly vs benign
    - THÊM utilization z-scores, efficiency ratios, utilization changes
    """
    print("=" * 70)
    print("  FEATURE ENGINEERING CẢI TIẾN (v4)")
    print("=" * 70)

    df = df.sort_values(by=['line_item_resource_id', 'date']).reset_index(drop=True)
    group = df.groupby('line_item_resource_id')

    # ─── COST FEATURES (giữ nguyên từ v3) ───
    print("[1/8] Tính toán cost features...")

    df['cost_rolling_median_28d'] = group['line_item_unblended_cost'].transform(
        lambda x: x.shift(1).rolling(window=28, min_periods=1).median()
    )
    df['cost_rolling_mad_28d'] = group['line_item_unblended_cost'].transform(
        lambda x: x.shift(1).rolling(window=28, min_periods=1).apply(
            lambda w: np.median(np.abs(w - np.median(w))) if len(w) > 0 else 0.0,
            raw=True
        )
    )
    df['cost_z'] = (
        (df['line_item_unblended_cost'] - df['cost_rolling_median_28d'])
        / (1.4826 * df['cost_rolling_mad_28d'] + 1e-5)
    )
    df['cost_change_28d'] = group['line_item_unblended_cost'].transform(
        lambda x: (x - x.shift(28)) / (x.shift(28) + 1e-5)
    ).fillna(0)

    def get_slope(y):
        if len(y) < 2:
            return 0.0
        x = np.arange(len(y))
        slope, _, _, _, _ = linregress(x, y)
        return slope

    df['cost_slope_14d'] = group['line_item_unblended_cost'].transform(
        lambda x: x.shift(1).rolling(window=14, min_periods=2).apply(get_slope, raw=True)
    ).fillna(0)

    # ─── MỚI: UTILIZATION Z-SCORES (phân biệt anomaly vs benign) ───
    print("[2/8] Tính toán utilization z-scores (MỚI)...")

    # CPU z-score: giúp nhận biết CPU cao bất thường (benign) vs bình thường (anomaly)
    df['cpu_rolling_median_28d'] = group['cpu_percent'].transform(
        lambda x: x.shift(1).rolling(window=28, min_periods=1).median()
    )
    df['cpu_rolling_mad_28d'] = group['cpu_percent'].transform(
        lambda x: x.shift(1).rolling(window=28, min_periods=1).apply(
            lambda w: np.median(np.abs(w - np.median(w))) if len(w) > 0 else 0.0,
            raw=True
        )
    )
    df['cpu_z'] = (
        (df['cpu_percent'] - df['cpu_rolling_median_28d'])
        / (1.4826 * df['cpu_rolling_mad_28d'] + 1e-5)
    )

    # Network z-score: giúp nhận biết traffic surge (DDoS vs Flash Sale)
    df['net_total'] = df['network_in_bytes'] + df['network_out_bytes']
    df['net_rolling_median_28d'] = group['net_total'].transform(
        lambda x: x.shift(1).rolling(window=28, min_periods=1).median()
    )
    df['net_rolling_mad_28d'] = group['net_total'].transform(
        lambda x: x.shift(1).rolling(window=28, min_periods=1).apply(
            lambda w: np.median(np.abs(w - np.median(w))) if len(w) > 0 else 0.0,
            raw=True
        )
    )
    df['net_z'] = (
        (df['net_total'] - df['net_rolling_median_28d'])
        / (1.4826 * df['net_rolling_mad_28d'] + 1e-5)
    )

    # ─── MỚI: UTILIZATION CHANGE RATES ───
    print("[3/8] Tính toán utilization change rates (MỚI)...")

    df['cpu_change_28d'] = group['cpu_percent'].transform(
        lambda x: (x - x.shift(28)) / (x.shift(28) + 1e-5)
    ).fillna(0)

    df['cpu_slope_14d'] = group['cpu_percent'].transform(
        lambda x: x.shift(1).rolling(window=14, min_periods=2).apply(get_slope, raw=True)
    ).fillna(0)

    # ─── MỚI: EFFICIENCY RATIOS (KEY feature để phân biệt anomaly vs benign) ───
    print("[4/8] Tính toán efficiency ratios (MỚI)...")

    # cost_z / cpu_z: Nếu cost tăng nhưng CPU cũng tăng → efficiency bình thường
    # Nếu cost tăng mà CPU không tăng → anomaly
    df['cost_cpu_z_ratio'] = df['cost_z'] / (df['cpu_z'].abs() + 1e-5)

    # cost change vs utilization change: tương quan giữa hai
    df['cost_util_divergence'] = df['cost_change_28d'] - df['cpu_change_28d']

    # ─── TIME FEATURES (giữ nguyên) ───
    print("[5/8] Tính toán time features...")
    df['is_weekend'] = df['date'].dt.dayofweek.isin([5, 6]).astype(int)
    df['day_of_week'] = df['date'].dt.dayofweek
    df['weekend_ratio'] = 0.0  # sẽ tính trong vòng lặp fold

    # ─── COST EFFICIENCY METRICS (giữ nguyên) ───
    print("[6/8] Tính toán cost efficiency metrics...")
    df['cost_per_cpu'] = df['line_item_unblended_cost'] / (df['cpu_percent'] + 1e-5)
    df['cost_per_memory'] = df['line_item_unblended_cost'] / (df['memory_mib'] + 1e-5)
    df['cost_per_network_in'] = df['line_item_unblended_cost'] / (df['network_in_bytes'] + 1e-5)
    df['cost_per_network_out'] = df['line_item_unblended_cost'] / (df['network_out_bytes'] + 1e-5)
    df['cost_per_disk_io'] = df['line_item_unblended_cost'] / (df['disk_io_ops'] + 1e-5)
    df['cost_per_db_conn'] = df['line_item_unblended_cost'] / (df['database_connections'] + 1e-5)
    df['cost_per_gpu'] = df['line_item_unblended_cost'] / (df['gpu_utilization'] + 1e-5)
    df['usage_density'] = df['line_item_usage_amount'] / 24.0

    # ─── TAG & PEER FEATURES (giữ nguyên) ───
    print("[7/8] Tính toán tag & peer features...")
    df['team_missing'] = (df['resource_tags_user_team'].isna() | (df['resource_tags_user_team'] == '')).astype(int)
    df['owner_missing'] = (df['resource_tags_user_owner'].isna() | (df['resource_tags_user_owner'] == '')).astype(int)

    peer_medians = df.groupby(['date', 'line_item_usage_account_name', 'line_item_product_code'])[
        'line_item_unblended_cost'].transform('median')
    df['peer_ratio'] = df['line_item_unblended_cost'] / (peer_medians + 1e-5)

    first_seen = df.groupby('line_item_resource_id')['date'].transform('min')
    df['age_days'] = (df['date'] - first_seen).dt.days

    # ─── MỚI: DAILY PEER Z-SCORES & RATIOS (giúp giải quyết Cold Start) ───
    print("  -> Tính toán daily peer z-scores và ratios...")
    
    # 1. Daily Peer Cost Z-score
    peer_cost_mads = df.groupby(['date', 'line_item_usage_account_name', 'line_item_product_code'])[
        'line_item_unblended_cost'].transform(
            lambda x: np.median(np.abs(x - np.median(x))) if len(x) > 0 else 0.0
        )
    df['peer_cost_z'] = (df['line_item_unblended_cost'] - peer_medians) / (1.4826 * peer_cost_mads + 1e-5)

    # 2. Daily Peer CPU Z-score
    peer_cpu_medians = df.groupby(['date', 'line_item_usage_account_name', 'line_item_product_code'])[
        'cpu_percent'].transform('median')
    peer_cpu_mads = df.groupby(['date', 'line_item_usage_account_name', 'line_item_product_code'])[
        'cpu_percent'].transform(
            lambda x: np.median(np.abs(x - np.median(x))) if len(x) > 0 else 0.0
        )
    df['peer_cpu_z'] = (df['cpu_percent'] - peer_cpu_medians) / (1.4826 * peer_cpu_mads + 1e-5)

    # 3. Daily Peer Network Z-score
    df['net_total'] = df['network_in_bytes'] + df['network_out_bytes']
    peer_net_medians = df.groupby(['date', 'line_item_usage_account_name', 'line_item_product_code'])[
        'net_total'].transform('median')
    peer_net_mads = df.groupby(['date', 'line_item_usage_account_name', 'line_item_product_code'])[
        'net_total'].transform(
            lambda x: np.median(np.abs(x - np.median(x))) if len(x) > 0 else 0.0
        )
    df['peer_net_z'] = (df['net_total'] - peer_net_medians) / (1.4826 * peer_net_mads + 1e-5)

    # 4. Daily Peer Ratios
    df['peer_cost_cpu_ratio'] = df['peer_cost_z'] / (df['peer_cpu_z'].abs() + 1e-5)
    df['peer_cost_net_ratio'] = df['peer_cost_z'] / (df['peer_net_z'].abs() + 1e-5)

    # ─── TẬP HỢP FEATURES CẢI TIẾN ───
    print("[8/8] Tập hợp features...")
    feature_cols = [
        'line_item_unblended_cost', 'line_item_usage_amount',
        # Raw metrics
        'cpu_percent', 'memory_mib', 'network_in_bytes', 'network_out_bytes',
        'disk_io_ops', 'database_connections', 'gpu_utilization',
        # Cost-derived (giữ nhưng đã có counterbalance từ utilization features)
        'cost_z', 'cost_change_28d', 'cost_slope_14d',
        # MỚI: Utilization z-scores & changes (counterbalance cost features)
        'cpu_z', 'net_z', 'cpu_change_28d', 'cpu_slope_14d',
        # MỚI: Efficiency ratios (KEY differentiation features)
        'cost_cpu_z_ratio', 'cost_util_divergence',
        # MỚI: Daily peer features (Cold Start mitigation)
        'peer_cost_z', 'peer_cpu_z', 'peer_net_z', 'peer_cost_cpu_ratio', 'peer_cost_net_ratio',
        # Time features
        'is_weekend', 'day_of_week', 'weekend_ratio',
        # Cost efficiency
        'cost_per_cpu', 'cost_per_memory', 'cost_per_network_in',
        'cost_per_network_out', 'cost_per_disk_io', 'cost_per_db_conn',
        'cost_per_gpu',
        # Other
        'usage_density', 'team_missing', 'owner_missing', 'peer_ratio', 'age_days'
    ] + cpu_h_cols

    # Làm sạch
    df[feature_cols] = df[feature_cols].replace([np.inf, -np.inf], np.nan).fillna(0.0)

    # Drop temporary columns
    for col in ['cost_rolling_median_28d', 'cost_rolling_mad_28d',
                'cpu_rolling_median_28d', 'cpu_rolling_mad_28d',
                'net_total', 'net_rolling_median_28d', 'net_rolling_mad_28d',
                'peer_net_medians', 'peer_net_mads']:
        if col in df.columns:
            df = df.drop(columns=[col])

    print(f"\n✅ Tổng số features: {len(feature_cols)}")
    print(f"   Features MỚI: cpu_z, net_z, cpu_change_28d, cpu_slope_14d, "
          f"cost_cpu_z_ratio, cost_util_divergence, peer_cost_z, peer_cpu_z, "
          f"peer_net_z, peer_cost_cpu_ratio, peer_cost_net_ratio")
    return df, feature_cols


# =============================================================================
# PHẦN 3: WALK-FORWARD VALIDATION CẢI TIẾN (có Purged Gap)
# =============================================================================

# Mapping nhãn
TARGET_MAPPING = {'normal': 0, 'anomaly': 1, 'benign': 2}

# Anomaly & Benign types (giữ nguyên từ notebook gốc)
ANOMALY_TYPES = {
    "arn:aws:rds:us-east-1:acct:db:db-staging-orphan-01": "idle_resource (Orphan RDS)",
    "log-group-debug-runaway": "sudden_spike (Debug Logs)",
    "i-0untaggedfleet01": "untagged_spend (Untagged EC2)",
    "ddb-table-events-prod": "gradual_drift (DynamoDB Drift)",
    "vol-0orphans-aggregate": "idle_resource (Orphan EBS)",
    "natgw-misconfig-spike": "sudden_spike (NAT GW Spike)"
}
for i in range(5):
    ANOMALY_TYPES[f"i-0fbgpu0000000{i}"] = "runaway_usage (GPU Runaway)"


def get_resource_type(res_id, label):
    if res_id in ANOMALY_TYPES:
        return ANOMALY_TYPES[res_id]
    if label == 'anomaly':
        res_hash = int(hashlib.md5(res_id.encode('utf-8')).hexdigest(), 16)
        anom_type = res_hash % 5
        types = ["sudden_spike (CPU Spike)", "gradual_drift (Memory Leak)",
                 "sudden_spike (DDoS Traffic)", "runaway_usage (DB Saturation)",
                 "sudden_spike (Disk Bottleneck)"]
        return types[anom_type]
    elif label == 'benign':
        if res_id == "migration-egress-onetime":
            return "benign (Migration Egress)"
        elif res_id == "i-0flashsale-autoscale":
            return "benign (Flash Sale Autoscale)"
        elif res_id == "i-0loadtest-fleet":
            return "benign (Load Test Fleet)"
        res_hash = int(hashlib.md5(res_id.encode('utf-8')).hexdigest(), 16)
        benign_type = res_hash % 4
        types = ["benign (Autoscaling)", "benign (Batch Job)",
                 "benign (Backup DB)", "benign (GPU Training)"]
        return types[benign_type]
    return "normal"


def compute_extended_metrics(y_true, y_pred):
    """Tính toán metrics mở rộng: MCC, Balanced Accuracy, Macro-F1, Weighted-F1."""
    macro_f1 = f1_score(y_true, y_pred, average='macro', zero_division=0)
    weighted_f1 = f1_score(y_true, y_pred, average='weighted', zero_division=0)
    mcc = matthews_corrcoef(y_true, y_pred)
    balanced_acc = balanced_accuracy_score(y_true, y_pred)
    return {
        'macro_f1': macro_f1,
        'weighted_f1': weighted_f1,
        'mcc': mcc,
        'balanced_accuracy': balanced_acc
    }


def run_walk_forward(df, feature_cols, timeline, gap_days=0,
                     experiment_name="Walk-Forward"):
    """
    Chạy Walk-Forward Validation với Purged Gap tùy chọn.

    Args:
        df: DataFrame đã có features
        feature_cols: danh sách features
        timeline: danh sách ngày đã sắp xếp
        gap_days: số ngày gap giữa train và test (0 = không gap)
        experiment_name: tên thí nghiệm

    Returns:
        results: dict chứa metrics của mỗi fold
    """
    df['target'] = df['label'].map(TARGET_MAPPING)

    # Định nghĩa folds
    if gap_days > 0:
        # Cắt bỏ gap_days cuối của Train
        folds_def = [
            ("Fold 1", timeline[:51 - gap_days], timeline[51:65]),
            ("Fold 2", timeline[:65 - gap_days], timeline[65:78]),
            ("Fold 3", timeline[:78 - gap_days], timeline[78:])
        ]
    else:
        folds_def = [
            ("Fold 1", timeline[:51], timeline[51:65]),
            ("Fold 2", timeline[:65], timeline[65:78]),
            ("Fold 3", timeline[:78], timeline[78:])
        ]

    print("\n" + "=" * 75)
    print(f"  {experiment_name}" +
          (f" (Gap = {gap_days} ngày)" if gap_days > 0 else " (Không có Gap)"))
    print("=" * 75)

    all_fold_results = []

    for f_idx, (fold_name, train_dates, test_dates) in enumerate(folds_def):
        print(f"\n{'=' * 75}")
        print(f"  BẮT ĐẦU {fold_name.upper()} "
              f"({len(train_dates)} Ngày Train -> {len(test_dates)} Ngày Test)"
              + (f" [Gap: {gap_days} ngày]" if gap_days > 0 else ""))
        print(f"{'=' * 75}")

        df_train = df[df['date'].isin(train_dates)].copy()
        df_test_f = df[df['date'].isin(test_dates)].copy()

        # 1. Tính weekend_ratio (leak-proof)
        avg_wknd = df_train[df_train['is_weekend'] == 1].groupby(
            'line_item_resource_id')['line_item_unblended_cost'].mean()
        avg_wkday = df_train[df_train['is_weekend'] == 0].groupby(
            'line_item_resource_id')['line_item_unblended_cost'].mean()
        ratio = avg_wknd / (avg_wkday + 1e-5)

        df_train['weekend_ratio'] = df_train['line_item_resource_id'].map(ratio).fillna(0)
        df_test_f['weekend_ratio'] = df_test_f['line_item_resource_id'].map(ratio).fillna(0)

        # ─── MỚI: DỊCH VỤ LỊCH SỬ BASELINE (Leak-proof Service Baselines cho Cold Start) ───
        df_train['net_total'] = df_train['network_in_bytes'] + df_train['network_out_bytes']
        df_test_f['net_total'] = df_test_f['network_in_bytes'] + df_test_f['network_out_bytes']

        # Tính trung vị và MAD trên tập Train
        service_cost_median = df_train.groupby('line_item_product_code')['line_item_unblended_cost'].median()
        service_cost_mad = df_train.groupby('line_item_product_code')['line_item_unblended_cost'].apply(
            lambda x: np.median(np.abs(x - np.median(x))) if len(x) > 0 else 0.0
        )
        service_cpu_median = df_train.groupby('line_item_product_code')['cpu_percent'].median()
        service_cpu_mad = df_train.groupby('line_item_product_code')['cpu_percent'].apply(
            lambda x: np.median(np.abs(x - np.median(x))) if len(x) > 0 else 0.0
        )
        service_net_median = df_train.groupby('line_item_product_code')['net_total'].median()
        service_net_mad = df_train.groupby('line_item_product_code')['net_total'].apply(
            lambda x: np.median(np.abs(x - np.median(x))) if len(x) > 0 else 0.0
        )

        # Map sang cả hai tập
        for frame in [df_train, df_test_f]:
            med_cost = frame['line_item_product_code'].map(service_cost_median).fillna(0.0)
            mad_cost = frame['line_item_product_code'].map(service_cost_mad).fillna(0.0)
            med_cpu = frame['line_item_product_code'].map(service_cpu_median).fillna(0.0)
            mad_cpu = frame['line_item_product_code'].map(service_cpu_mad).fillna(0.0)
            med_net = frame['line_item_product_code'].map(service_net_median).fillna(0.0)
            mad_net = frame['line_item_product_code'].map(service_net_mad).fillna(0.0)

            frame['service_cost_z'] = (frame['line_item_unblended_cost'] - med_cost) / (1.4826 * mad_cost + 1e-5)
            frame['service_cpu_z'] = (frame['cpu_percent'] - med_cpu) / (1.4826 * mad_cpu + 1e-5)
            frame['service_net_z'] = (frame['net_total'] - med_net) / (1.4826 * mad_net + 1e-5)

            frame['service_cost_cpu_ratio'] = frame['service_cost_z'] / (frame['service_cpu_z'].abs() + 1e-5)
            frame['service_cost_net_ratio'] = frame['service_cost_z'] / (frame['service_net_z'].abs() + 1e-5)

        # Cập nhật danh sách features cho fold hiện tại
        local_feature_cols = feature_cols.copy() + [
            'service_cost_z', 'service_cpu_z', 'service_net_z',
            'service_cost_cpu_ratio', 'service_cost_net_ratio'
        ]

        X_train = df_train[local_feature_cols].values
        y_train = df_train['target'].values
        X_test_f = df_test_f[local_feature_cols].values
        y_test_f = df_test_f['target'].values
        groups_tr = df_train['line_item_resource_id'].values

        print(f"[PHÂN PHỐI NHÃN HUẤN LUYỆN]: "
              f"Normal: {np.sum(y_train == 0)} | "
              f"Anomaly: {np.sum(y_train == 1)} | "
              f"Benign: {np.sum(y_train == 2)}")

        # 2. Hiệu chuẩn ngưỡng (GroupKFold CV)
        cv_gkf = GroupKFold(n_splits=min(5, len(np.unique(groups_tr))))
        oof_prob_anomaly = np.zeros(len(df_train))

        for tr_cv_idx, val_cv_idx in cv_gkf.split(X_train, y_train, groups=groups_tr):
            X_tr_cv, y_tr_cv = X_train[tr_cv_idx], y_train[tr_cv_idx]
            X_val_cv = X_train[val_cv_idx]

            scaler_cv = RobustScaler()
            X_tr_cv_scaled = scaler_cv.fit_transform(X_tr_cv)
            X_val_cv_scaled = scaler_cv.transform(X_val_cv)

            cv_weights = compute_sample_weight(class_weight='balanced', y=y_tr_cv)

            model_cv = xgb.XGBClassifier(
                objective='multi:softprob', num_class=3, eval_metric='mlogloss',
                n_estimators=350, max_depth=4, learning_rate=0.03,
                subsample=0.8, colsample_bytree=0.7,
                reg_alpha=1.0, reg_lambda=2.0, min_child_weight=3,
                random_state=42
            )
            model_cv.fit(X_tr_cv_scaled, y_tr_cv, sample_weight=cv_weights)
            oof_prob_anomaly[val_cv_idx] = model_cv.predict_proba(X_val_cv_scaled)[:, 1]

        # Tìm ngưỡng
        y_tr_binary = (y_train == 1).astype(int)
        prec_cv, rec_cv, thr_cv = precision_recall_curve(y_tr_binary, oof_prob_anomaly)
        ok_cv = np.where(prec_cv[:-1] >= 0.80)[0]
        if len(ok_cv) > 0:
            chosen_idx = ok_cv[np.argmax(rec_cv[ok_cv])]
            t_anomaly = thr_cv[chosen_idx]
        else:
            chosen_idx = np.argmax(prec_cv[:-1])
            t_anomaly = thr_cv[chosen_idx]
        print(f"[NGƯỠNG ANOMALY]: {t_anomaly:.4f}")

        # 3. Chuẩn hóa & Train final model
        scaler = RobustScaler()
        X_train_scaled = scaler.fit_transform(X_train)
        X_test_f_scaled = scaler.transform(X_test_f)

        train_weights = compute_sample_weight(class_weight='balanced', y=y_train)
        final_model_f = xgb.XGBClassifier(
            objective='multi:softprob', num_class=3, eval_metric='mlogloss',
            n_estimators=350, max_depth=4, learning_rate=0.03,
            subsample=0.8, colsample_bytree=0.7,
            reg_alpha=1.0, reg_lambda=2.0, min_child_weight=3,
            random_state=42
        )
        final_model_f.fit(X_train_scaled, y_train, sample_weight=train_weights)

        # 4. Dự đoán
        test_probs_f = final_model_f.predict_proba(X_test_f_scaled)
        y_pred_f = np.zeros(len(test_probs_f), dtype=int)

        for i in range(len(test_probs_f)):
            # Báo Anomaly nếu prob_anomaly >= t_anomaly VÀ (prob_anomaly > prob_benign HOẶC model không đủ tin cậy là Benign < 50%)
            if test_probs_f[i, 1] >= t_anomaly and (test_probs_f[i, 1] > test_probs_f[i, 2] or test_probs_f[i, 2] < 0.50):
                y_pred_f[i] = 1
            elif test_probs_f[i, 2] >= 0.20:
                y_pred_f[i] = 2
            else:
                y_pred_f[i] = np.argmax(test_probs_f[i])

        # 5. Classification Report
        print(f"\n--- BÁO CÁO CHẤT LƯỢNG ({fold_name.upper()}) ---")
        print(classification_report(
            y_test_f, y_pred_f, labels=[0, 1, 2],
            target_names=['normal', 'anomaly', 'benign'], zero_division=0
        ))

        cm_f = confusion_matrix(y_test_f, y_pred_f, labels=[0, 1, 2])
        print(f"Ma trận nhầm lẫn:\n{cm_f}")

        # ─── MỚI: METRICS MỞ RỘNG ───
        ext_metrics = compute_extended_metrics(y_test_f, y_pred_f)
        print(f"\n--- METRICS MỞ RỘNG ({fold_name.upper()}) ---")
        print(f"  Macro-F1:          {ext_metrics['macro_f1']:.4f}")
        print(f"  Weighted-F1:       {ext_metrics['weighted_f1']:.4f}")
        print(f"  MCC:               {ext_metrics['mcc']:.4f}")
        print(f"  Balanced Accuracy: {ext_metrics['balanced_accuracy']:.4f}")

        # Store fold result
        fold_result = {
            'fold': fold_name,
            'threshold': t_anomaly,
            **ext_metrics,
            'confusion_matrix': cm_f
        }

        # Per-class metrics
        for cls_idx, cls_name in enumerate(['normal', 'anomaly', 'benign']):
            cls_mask_true = (y_test_f == cls_idx)
            cls_mask_pred = (y_pred_f == cls_idx)
            tp = np.sum(cls_mask_true & cls_mask_pred)
            fn = np.sum(cls_mask_true & ~cls_mask_pred)
            fp = np.sum(~cls_mask_true & cls_mask_pred)
            prec = tp / (tp + fp) if (tp + fp) > 0 else 0
            rec = tp / (tp + fn) if (tp + fn) > 0 else 0
            f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0
            fold_result[f'{cls_name}_precision'] = prec
            fold_result[f'{cls_name}_recall'] = rec
            fold_result[f'{cls_name}_f1'] = f1

        all_fold_results.append(fold_result)

        # Vẽ và lưu Confusion Matrix
        plt.figure(figsize=(6, 5))
        sns.heatmap(cm_f, annot=True, fmt='d', cmap='Blues',
                    xticklabels=['normal', 'anomaly', 'benign'],
                    yticklabels=['normal', 'anomaly', 'benign'])
        plt.title(f'Confusion Matrix - {fold_name}'
                  + (f' (Gap {gap_days}d)' if gap_days > 0 else ''))
        plt.ylabel('Thực tế')
        plt.xlabel('Dự đoán')
        plt.tight_layout()
        os.makedirs('plots', exist_ok=True)
        cm_path = f"plots/confusion_matrix_{fold_name.replace(' ', '_').lower()}_gap_{gap_days}d.png"
        plt.savefig(cm_path)
        plt.close()
        print(f"  -> Lưu Confusion Matrix tại: {cm_path}")

        # Vẽ và lưu PR Curve
        plt.figure(figsize=(7, 5))
        plt.plot(rec_cv, prec_cv, label='PR Curve (OOF CV)',
                 color='darkgreen', lw=2)
        plt.plot(rec_cv[chosen_idx], prec_cv[chosen_idx], 'ro',
                 markersize=9, label=f'Ngưỡng: {t_anomaly:.4f}')
        plt.title(f'Precision-Recall Curve - {fold_name}')
        plt.xlabel('Recall')
        plt.ylabel('Precision')
        plt.grid(True)
        plt.legend()
        plt.tight_layout()
        pr_path = f"plots/pr_curve_{fold_name.replace(' ', '_').lower()}_gap_{gap_days}d.png"
        plt.savefig(pr_path)
        plt.close()
        print(f"  -> Lưu PR Curve tại: {pr_path}")

        # Phân tích chi tiết anomaly types
        df_test_f['pred_f'] = y_pred_f
        df_test_f['res_type'] = df_test_f.apply(
            lambda r: get_resource_type(r['line_item_resource_id'], r['label']),
            axis=1
        )

        print(f"\n--- RECALL THEO LOẠI SỰ CỐ ANOMALY ({fold_name.upper()}) ---")
        anom_df = df_test_f[df_test_f['label'] == 'anomaly']
        if len(anom_df) > 0:
            for name, gp in sorted(anom_df.groupby('res_type')):
                tot = len(gp)
                det = (gp['pred_f'] == 1).sum()
                print(f"  {name:<40} | Tổng: {tot:<4} | Phát hiện: {det:<4} "
                      f"| Recall: {det / tot:.2%}")

        print(f"\n--- FALSE ALARM RATE TRÊN BENIGN ({fold_name.upper()}) ---")
        ben_df = df_test_f[df_test_f['label'] == 'benign']
        if len(ben_df) > 0:
            for name, gp in sorted(ben_df.groupby('res_type')):
                tot = len(gp)
                flg = (gp['pred_f'] == 1).sum()
                print(f"  {name:<40} | Tổng: {tot:<4} | Báo nhầm: {flg:<4} "
                      f"| FPR: {flg / tot:.2%}")

        # SHAP
        print(f"\n--- SHAP ({fold_name.upper()}) ---")
        explainer_f = shap.TreeExplainer(final_model_f)
        shap_values_f = explainer_f.shap_values(X_test_f_scaled)

        if isinstance(shap_values_f, list):
            sv_anomaly = shap_values_f[1]
        elif len(shap_values_f.shape) == 3:
            sv_anomaly = shap_values_f[:, :, 1]
        else:
            sv_anomaly = shap_values_f

        # Print top 10 SHAP features
        mean_abs_shap = np.mean(np.abs(sv_anomaly), axis=0)
        top_idx = np.argsort(mean_abs_shap)[::-1][:10]
        print(f"Top 10 features quan trọng nhất đối với Anomaly theo SHAP:")
        for idx in top_idx:
            print(f"  - {local_feature_cols[idx]:<25}: {mean_abs_shap[idx]:.4f}")

        # Lưu SHAP plot
        plt.figure(figsize=(10, 6))
        shap.summary_plot(sv_anomaly, X_test_f_scaled,
                          feature_names=local_feature_cols, show=False)
        plt.title(f"SHAP Feature Importance (Anomaly) - {fold_name}", fontsize=13)
        plt.tight_layout()
        shap_path = f"plots/shap_{fold_name.replace(' ', '_').lower()}_gap_{gap_days}d.png"
        plt.savefig(shap_path)
        plt.close()
        print(f"  -> Lưu SHAP plot tại: {shap_path}")

    return all_fold_results


# =============================================================================
# PHẦN 4: SO SÁNH CÓ GAP VS KHÔNG GAP
# =============================================================================
def compare_results(results_no_gap, results_with_gap, gap_days=7):
    """So sánh metrics giữa 2 thí nghiệm: có gap vs không gap."""
    print("\n" + "=" * 75)
    print(f"  SO SÁNH KẾT QUẢ: KHÔNG GAP vs CÓ GAP ({gap_days} NGÀY)")
    print("=" * 75)

    metrics_to_compare = [
        ('macro_f1', 'Macro-F1'),
        ('weighted_f1', 'Weighted-F1'),
        ('mcc', 'MCC'),
        ('balanced_accuracy', 'Balanced Accuracy'),
        ('anomaly_recall', 'Anomaly Recall'),
        ('anomaly_precision', 'Anomaly Precision'),
        ('anomaly_f1', 'Anomaly F1'),
    ]

    # Header
    header = f"{'Metric':<22} | "
    for i in range(3):
        header += f"{'Fold ' + str(i + 1) + ' (No Gap)':<18} | "
        header += f"{'Fold ' + str(i + 1) + ' (Gap)':<18} | "
        header += f"{'Δ':>6} | "
    print(header)
    print("-" * len(header))

    for metric_key, metric_name in metrics_to_compare:
        row = f"{metric_name:<22} | "
        for i in range(min(len(results_no_gap), len(results_with_gap))):
            v_no = results_no_gap[i].get(metric_key, 0)
            v_gap = results_with_gap[i].get(metric_key, 0)
            delta = v_gap - v_no
            row += f"{v_no:>18.4f} | {v_gap:>18.4f} | {delta:>+6.2%} | "
        print(row)

    # Trung bình
    print("\n--- Trung bình across folds ---")
    for metric_key, metric_name in metrics_to_compare:
        avg_no = np.mean([r.get(metric_key, 0) for r in results_no_gap])
        avg_gap = np.mean([r.get(metric_key, 0) for r in results_with_gap])
        delta = avg_gap - avg_no
        delta_pct = delta / (avg_no + 1e-10)
        print(f"  {metric_name:<22}: No Gap = {avg_no:.4f} | "
              f"Gap = {avg_gap:.4f} | Δ = {delta:+.4f} ({delta_pct:+.2%})")

    # Đánh giá leakage
    avg_f1_no = np.mean([r.get('anomaly_f1', 0) for r in results_no_gap])
    avg_f1_gap = np.mean([r.get('anomaly_f1', 0) for r in results_with_gap])
    drop = avg_f1_no - avg_f1_gap

    print(f"\n{'=' * 50}")
    if drop > 0.10:
        print(f"⚠️  CẢNH BÁO: Anomaly F1 giảm {drop:.2%} khi thêm gap")
        print(f"   → Có TEMPORAL LEAKAGE nghiêm trọng!")
        print(f"   → Cần redesign features (giảm rolling window)")
    elif drop > 0.05:
        print(f"⚠️  CHÚ Ý: Anomaly F1 giảm {drop:.2%} khi thêm gap")
        print(f"   → Có temporal leakage nhẹ, cần theo dõi")
    else:
        print(f"✅ Anomaly F1 thay đổi {drop:+.2%} — Không có temporal leakage đáng kể")
    print(f"{'=' * 50}")


# =============================================================================
# PHẦN 5: MAIN - CHẠY TOÀN BỘ
# =============================================================================
def main():
    """Chạy toàn bộ pipeline cải tiến."""
    # Load data
    df, cpu_h_cols = load_data()

    # Timeline setup
    timeline = sorted(df['date'].unique())
    print(f"\nTổng số ngày: {len(timeline)} "
          f"(Từ {timeline[0].strftime('%Y-%m-%d')} "
          f"đến {timeline[-1].strftime('%Y-%m-%d')})")

    # Feature engineering cải tiến
    df, feature_cols = build_features_improved(df, cpu_h_cols)

    # Thí nghiệm 1: Không có gap (baseline so sánh)
    print("\n" + "🔬" * 30)
    print("  THÍ NGHIỆM 1: WALK-FORWARD KHÔNG CÓ GAP (Baseline)")
    print("🔬" * 30)
    results_no_gap = run_walk_forward(
        df.copy(), feature_cols, timeline,
        gap_days=0, experiment_name="Baseline (No Gap)"
    )

    # Thí nghiệm 2: Có gap 7 ngày
    print("\n" + "🔬" * 30)
    print("  THÍ NGHIỆM 2: WALK-FORWARD CÓ GAP 7 NGÀY (Purged)")
    print("🔬" * 30)
    results_with_gap = run_walk_forward(
        df.copy(), feature_cols, timeline,
        gap_days=7, experiment_name="Purged Gap Test"
    )

    # So sánh kết quả
    compare_results(results_no_gap, results_with_gap, gap_days=7)

    print("\n" + "=" * 75)
    print("  ✅ HOÀN TẤT — Xem báo cáo so sánh ở trên")
    print("=" * 75)


if __name__ == "__main__":
    main()
