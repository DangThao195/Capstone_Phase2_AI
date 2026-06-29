import os
import sys
import pandas as pd
import numpy as np

# Set stdout/stderr encoding to UTF-8
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

def run_feature_engineering():
    print("=== BẮT ĐẦU QUÁ TRÌNH FEATURE ENGINEERING ===")
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    workspace_dir = os.path.dirname(os.path.dirname(script_dir))
    input_dir = os.path.join(workspace_dir, "experience_1", "data", "joined")
    output_dir = os.path.join(workspace_dir, "experience_1", "data", "engineered")
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        print(f"Đã tạo thư mục output: {output_dir}")
        
    # Danh sách các file joined cần xử lý
    files_to_process = [f for f in os.listdir(input_dir) if f.startswith("joined_") and f.endswith(".csv")]
    
    if not files_to_process:
        print(f"[ERROR] Không tìm thấy file joined_*.csv nào trong thư mục: {input_dir}")
        return
        
    print(f"Tìm thấy {len(files_to_process)} file dữ liệu đã join để xử lý.\n")

    try:
        from sklearn.preprocessing import RobustScaler
    except ImportError:
        print("[ERROR] Chưa cài đặt scikit-learn. Đang dừng...")
        return

    for filename in files_to_process:
        filepath = os.path.join(input_dir, filename)
        service_name = filename.split("_")[1].upper()
        print(f"--- Đang thực hiện Feature Engineering cho: {service_name} ---")
        
        df = pd.read_csv(filepath, parse_dates=["timestamp"])
        df = df.sort_values(by=["resource_id", "timestamp"]).reset_index(drop=True)
        
        # Đặc trưng thời gian
        df["day_of_week"] = df["timestamp"].dt.dayofweek
        df["is_weekend"] = df["day_of_week"].apply(lambda x: 1 if x >= 5 else 0)
        
        # Đặc trưng trễ (Lags)
        df["cost_lag_1d"] = df.groupby("resource_id")["cost"].shift(1)
        df["cost_lag_2d"] = df.groupby("resource_id")["cost"].shift(2)
        df["cost_lag_7d"] = df.groupby("resource_id")["cost"].shift(7)
        df["cost_diff_1d"] = df["cost"] - df["cost_lag_1d"]
        
        if "cpu_percent" in df.columns:
            df["cpu_lag_1d"] = df.groupby("resource_id")["cpu_percent"].shift(1)
            df["cpu_lag_2d"] = df.groupby("resource_id")["cpu_percent"].shift(2)
            df["cpu_lag_7d"] = df.groupby("resource_id")["cpu_percent"].shift(7)
            df["cpu_diff_1d"] = df["cpu_percent"] - df["cpu_lag_1d"]

        # Đặc trưng Robust Rolling 14 ngày
        df['cost_roll_median_14d'] = df.groupby('resource_id')['cost'].transform(
            lambda x: x.rolling(window=14, min_periods=1).median()
        )
        def calculate_mad_14d(group):
            diff = (group['cost'] - group['cost_roll_median_14d']).abs()
            return diff.rolling(window=14, min_periods=1).median()
            
        df['cost_roll_mad_14d'] = df.groupby('resource_id').apply(calculate_mad_14d).reset_index(level=0, drop=True)
        df['cost_roll_mad_14d'] = df['cost_roll_mad_14d'].replace(0, 0.001).fillna(0.001)
        df['cost_deviation_ratio_14d'] = (df['cost'] - df['cost_roll_median_14d']) / df['cost_roll_mad_14d']

        if "cpu_percent" in df.columns:
            # 7 ngày
            df['cpu_roll_median_7d'] = df.groupby('resource_id')['cpu_percent'].transform(
                lambda x: x.rolling(window=7, min_periods=1).median()
            )
            def calculate_cpu_mad_7d(group):
                diff = (group['cpu_percent'] - group['cpu_roll_median_7d']).abs()
                return diff.rolling(window=7, min_periods=1).median()
            df['cpu_roll_mad_7d'] = df.groupby('resource_id').apply(calculate_cpu_mad_7d).reset_index(level=0, drop=True)
            df['cpu_roll_mad_7d'] = df['cpu_roll_mad_7d'].replace(0, 0.001).fillna(0.001)
            df['cpu_deviation_ratio_7d'] = (df['cpu_percent'] - df['cpu_roll_median_7d']) / df['cpu_roll_mad_7d']

            # 14 ngày
            df['cpu_roll_median_14d'] = df.groupby('resource_id')['cpu_percent'].transform(
                lambda x: x.rolling(window=14, min_periods=1).median()
            )
            def calculate_cpu_mad_14d(group):
                diff = (group['cpu_percent'] - group['cpu_roll_median_14d']).abs()
                return diff.rolling(window=14, min_periods=1).median()
            df['cpu_roll_mad_14d'] = df.groupby('resource_id').apply(calculate_cpu_mad_14d).reset_index(level=0, drop=True)
            df['cpu_roll_mad_14d'] = df['cpu_roll_mad_14d'].replace(0, 0.001).fillna(0.001)
            df['cpu_deviation_ratio_14d'] = (df['cpu_percent'] - df['cpu_roll_median_14d']) / df['cpu_roll_mad_14d']

        # Fill NaNs
        fill_cols = [c for c in df.columns if 'lag' in c or 'diff' in c or 'deviation' in c]
        for col in fill_cols:
            df[col] = df.groupby("resource_id")[col].transform(lambda x: x.bfill().fillna(0))

        # Chuẩn hóa RobustScaler
        feature_cols_to_scale = [
            "cost", "cost_roll_median_7d", "cost_roll_mad_7d", "cost_deviation_ratio",
            "cost_roll_median_14d", "cost_roll_mad_14d", "cost_deviation_ratio_14d",
            "cost_lag_1d", "cost_lag_2d", "cost_lag_7d", "cost_diff_1d"
        ]
        if "cpu_percent" in df.columns:
            feature_cols_to_scale.extend([
                "cpu_percent", "cpu_roll_median_7d", "cpu_roll_mad_7d", "cpu_deviation_ratio_7d",
                "cpu_roll_median_14d", "cpu_roll_mad_14d", "cpu_deviation_ratio_14d",
                "cpu_lag_1d", "cpu_lag_2d", "cpu_lag_7d", "cpu_diff_1d", "cpu_per_dollar"
            ])
        if "database_connections" in df.columns:
            feature_cols_to_scale.extend(["database_connections", "conn_per_dollar"])
        if "gpu_utilization" in df.columns:
            feature_cols_to_scale.extend(["gpu_utilization", "gpu_per_dollar"])
            
        feature_cols_to_scale = list(set([c for c in feature_cols_to_scale if c in df.columns]))
        
        scaler = RobustScaler()
        scaled_features = scaler.fit_transform(df[feature_cols_to_scale])
        
        for i, col in enumerate(feature_cols_to_scale):
            df[f"{col}_scaled"] = scaled_features[:, i]
            
        # Ghi kết quả
        output_file = os.path.join(output_dir, f"engineered_{service_name.lower()}_features.csv")
        df.to_csv(output_file, index=False)
        print(f"  * Lưu tập dữ liệu đã hoàn thiện đặc trưng tại: {output_file}\n")
        
    print("=== HOÀN THÀNH QUÁ TRÌNH FEATURE ENGINEERING ===")

if __name__ == "__main__":
    run_feature_engineering()
