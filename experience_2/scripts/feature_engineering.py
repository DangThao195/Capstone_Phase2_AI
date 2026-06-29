import os
import sys
import pandas as pd
import numpy as np
import json

# Reconfigure stdout to use UTF-8 to avoid Windows CP1252 encoding errors
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

def calculate_cpu_std(val):
    if pd.isna(val) or not isinstance(val, str):
        return 0.0
    try:
        arr = json.loads(val)
        return float(np.std(arr))
    except Exception:
        return 0.0

def run_feature_engineering():
    print("=== BẮT ĐẦU BƯỚC 2: FEATURE ENGINEERING (HOURLY) ===")
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    workspace_dir = os.path.dirname(os.path.dirname(script_dir))
    input_file = os.path.join(workspace_dir, "experience_2", "data", "joined", "joined_metrics_all.csv")
    output_dir = os.path.join(workspace_dir, "experience_2", "data", "engineered")
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        print(f"Đã tạo thư mục output: {output_dir}")
        
    if not os.path.exists(input_file):
        print(f"[ERROR] Không tìm thấy file dữ liệu đã join: {input_file}")
        return
        
    print(f"Loading joined dataset: {input_file}")
    df = pd.read_csv(input_file, parse_dates=["timestamp"])
    df = df.sort_values(by=["resource_id", "timestamp"]).reset_index(drop=True)
    print(f"Dataset loaded. Shape: {df.shape}")
    
    # -------------------------------------------------------------
    # 1. Đặc trưng thời gian (Time features)
    # -------------------------------------------------------------
    print("\n--- 1. Tính toán đặc trưng thời gian ---")
    df["hour_of_day"] = df["timestamp"].dt.hour
    df["day_of_week"] = df["timestamp"].dt.dayofweek
    df["is_weekend"] = df["day_of_week"].apply(lambda x: 1 if x >= 5 else 0)
    df["is_business_hours"] = ((df["hour_of_day"] >= 9) & (df["hour_of_day"] <= 17) & (df["day_of_week"] < 5)).astype(int)
    
    # -------------------------------------------------------------
    # 2. Đặc trưng trễ (Lags) và Sai khác (Diffs) cho tất cả metrics
    # -------------------------------------------------------------
    print("\n--- 2. Tính toán đặc trưng Lags & Diffs ---")
    metrics_cols = ["cost", "cpu_percent", "memory_mib", "network_in_bytes", "network_out_bytes", "disk_io_ops", "database_connections", "gpu_utilization"]
    
    for col in metrics_cols:
        if col in df.columns:
            df[f"{col}_lag_1h"] = df.groupby("resource_id")[col].shift(1)
            df[f"{col}_lag_1d"] = df.groupby("resource_id")[col].shift(24)
            df[f"{col}_lag_2d"] = df.groupby("resource_id")[col].shift(48)
            df[f"{col}_lag_7d"] = df.groupby("resource_id")[col].shift(168)
            df[f"{col}_diff_1h"] = df[col] - df[f"{col}_lag_1h"]
            df[f"{col}_diff_1d"] = df[col] - df[f"{col}_lag_1d"]
            
    # -------------------------------------------------------------
    # 3. Đặc trưng Robust Rolling (7 ngày = 168h, 14 ngày = 336h) cho tất cả metrics
    # -------------------------------------------------------------
    print("\n--- 3. Tính toán đặc trưng Robust Rolling (7d/14d) ---")
    
    for col in metrics_cols:
        if col in df.columns:
            # Rolling 7 ngày (168h)
            df[f'{col}_roll_median_7d'] = df.groupby('resource_id')[col].transform(
                lambda x: x.rolling(window=168, min_periods=1).median()
            )
            
            def calculate_mad_7d(group, c=col):
                diff = (group[c] - group[f'{c}_roll_median_7d']).abs()
                return diff.rolling(window=168, min_periods=1).median()
                
            df[f'{col}_roll_mad_7d'] = df.groupby('resource_id').apply(calculate_mad_7d).reset_index(level=0, drop=True)
            df[f'{col}_roll_mad_7d'] = df[f'{col}_roll_mad_7d'].replace(0, 0.001).fillna(0.001)
            df[f'{col}_deviation_ratio_7d'] = (df[col] - df[f'{col}_roll_median_7d']) / df[f'{col}_roll_mad_7d']
            
            # Rolling 14 ngày (336h)
            df[f'{col}_roll_median_14d'] = df.groupby('resource_id')[col].transform(
                lambda x: x.rolling(window=336, min_periods=1).median()
            )
            
            def calculate_mad_14d(group, c=col):
                diff = (group[c] - group[f'{c}_roll_median_14d']).abs()
                return diff.rolling(window=336, min_periods=1).median()
                
            df[f'{col}_roll_mad_14d'] = df.groupby('resource_id').apply(calculate_mad_14d).reset_index(level=0, drop=True)
            df[f'{col}_roll_mad_14d'] = df[f'{col}_roll_mad_14d'].replace(0, 0.001).fillna(0.001)
            df[f'{col}_deviation_ratio_14d'] = (df[col] - df[f'{col}_roll_median_14d']) / df[f'{col}_roll_mad_14d']

    # -------------------------------------------------------------
    # 4. Đặc trưng tỷ lệ & Sub-hourly CPU Std
    # -------------------------------------------------------------
    print("\n--- 4. Tính toán đặc trưng tỷ lệ & sub-hourly CPU Std ---")
    df['network_ratio'] = df['network_in_bytes'] / (df['network_out_bytes'] + 1e-5)
    df['cpu_per_dollar'] = df['cpu_percent'] / (df['cost'] + 1e-5)
    df['gpu_per_dollar'] = df['gpu_utilization'] / (df['cost'] + 1e-5)
    df['conn_per_dollar'] = df['database_connections'] / (df['cost'] + 1e-5)
    
    # Tính độ lệch chuẩn của CPU sub-hourly
    print("  * Đang tính toán cpu_hourly_std (sub-hourly CPU standard deviation)...")
    df['cpu_hourly_std'] = df['cpu_utilization_hourly'].apply(calculate_cpu_std)
    
    # -------------------------------------------------------------
    # 5. Xử lý NaNs (Backfill sau đó điền 0 cho lags/rolling khởi đầu)
    # -------------------------------------------------------------
    print("\n--- 5. Xử lý NaNs và Làm sạch dữ liệu ---")
    # Tự động tìm tất cả các cột lags, diff, rolling, ratio vừa sinh ra
    cols_to_fill = [
        c for c in df.columns 
        if any(suffix in c for suffix in ["_lag_", "_diff_", "_roll_", "_ratio", "_per_dollar", "_std"])
    ]
    for col in cols_to_fill:
        df[col] = df.groupby("resource_id")[col].transform(lambda x: x.bfill().fillna(0))
            
    # Ghi file kết quả đặc trưng thô
    output_file = os.path.join(output_dir, "engineered_raw_features.csv")
    df.to_csv(output_file, index=False)
    print(f"\nSaved engineered features to: {output_file}")
    print("=== HOÀN THÀNH BƯỚC 2 ===")

if __name__ == "__main__":
    run_feature_engineering()
