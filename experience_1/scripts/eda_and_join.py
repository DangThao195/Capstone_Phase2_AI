import os
import sys
import pandas as pd
import numpy as np

# Reconfigure stdout to use UTF-8 to avoid Windows CP1252 encoding errors
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

def run_eda_and_join():
    print("=== BẮT ĐẦU QUÁ TRÌNH EDA & NỐI DỮ LIỆU ===")
    
    # Xác định đường dẫn thư mục gốc
    script_dir = os.path.dirname(os.path.abspath(__file__))
    workspace_dir = os.path.dirname(os.path.dirname(script_dir))
    data_dir = os.path.join(workspace_dir, "tf2-data")
    metrics_dir = os.path.join(data_dir, "metrics_haikhoa")
    output_dir = os.path.join(workspace_dir, "experience_1", "data", "joined")
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        print(f"Đã tạo thư mục output: {output_dir}")

    print(f"Thư mục dữ liệu: {data_dir}")
    print(f"Thư mục metrics: {metrics_dir}\n")

    # -------------------------------------------------------------
    # 1. LOAD DỮ LIỆU CƠ BẢN
    # -------------------------------------------------------------
    print("--- 1. Loading dữ liệu chính ---")
    
    cur_path = os.path.join(data_dir, "cur_line_items.csv")
    ce_path = os.path.join(data_dir, "cost_explorer_daily.csv")
    label_path = os.path.join(data_dir, "anomaly_labels_public.csv")
    
    df_cur = pd.read_csv(cur_path, parse_dates=["line_item_usage_start_date"])
    df_ce = pd.read_csv(ce_path, parse_dates=["date"])
    df_labels = pd.read_csv(label_path)
    print(f"CUR shape: {df_cur.shape} | CE shape: {df_ce.shape}")

    # -------------------------------------------------------------
    # 2. EDA SƠ BỘ VỀ CHI PHÍ (COST EDA)
    # -------------------------------------------------------------
    print("--- 2. Khai phá dữ liệu Chi phí (CUR) ---")
    total_spend = df_cur["line_item_unblended_cost"].sum()
    print(f"Tổng chi tiêu 3 tháng trong CUR: ${total_spend:,.2f}")
    
    # Chuẩn bị cột date cấp ngày cho CUR
    df_cur['date'] = df_cur['line_item_usage_start_date'].dt.date

    # -------------------------------------------------------------
    # 3. ĐỌC VÀ EDA SƠ BỘ VỀ HIỆU NĂNG (METRICS EDA)
    # -------------------------------------------------------------
    print("\n--- 3. Khai phá dữ liệu Metrics tự sinh ---")
    metrics_files = [f for f in os.listdir(metrics_dir) if f.endswith(".csv")]
    
    metrics_data = {}
    for filename in metrics_files:
        filepath = os.path.join(metrics_dir, filename)
        df_metric = pd.read_csv(filepath, parse_dates=["timestamp"])
        df_metric['date'] = df_metric['timestamp'].dt.date
        service_name = filename.split("_")[0].upper()
        metrics_data[service_name] = df_metric
        print(f"  * Dịch vụ {service_name}: {df_metric.shape[0]} dòng, {df_metric['resource_id'].nunique()} resources unique.")

    # -------------------------------------------------------------
    # 4. NỐI DỮ LIỆU (DATA JOINING)
    # -------------------------------------------------------------
    print("\n--- 4. Thực hiện Nối dữ liệu (CUR + Metrics) ---")
    df_cur_daily = df_cur.groupby(['date', 'line_item_resource_id'])['line_item_unblended_cost'].sum().reset_index()
    df_cur_daily.rename(columns={'line_item_resource_id': 'resource_id', 'line_item_unblended_cost': 'cost'}, inplace=True)
    
    for service_name, df_metric in metrics_data.items():
        print(f"\n--- Nối dữ liệu cho {service_name} ---")
        df_joined = pd.merge(df_metric, df_cur_daily, on=['date', 'resource_id'], how='inner')
        print(f"  - Số lượng dòng sau khi Join thành công: {df_joined.shape[0]} dòng")
        
        # Đặc trưng Robust Rolling Median & MAD cơ bản
        df_joined = df_joined.sort_values(by=['resource_id', 'date'])
        df_joined['cost_roll_median_7d'] = df_joined.groupby('resource_id')['cost'].transform(
            lambda x: x.rolling(window=7, min_periods=1).median()
        )
        
        def calculate_mad(group):
            diff = (group['cost'] - group['cost_roll_median_7d']).abs()
            return diff.rolling(window=7, min_periods=1).median()
            
        df_joined['cost_roll_mad_7d'] = df_joined.groupby('resource_id').apply(calculate_mad).reset_index(level=0, drop=True)
        df_joined['cost_roll_mad_7d'] = df_joined['cost_roll_mad_7d'].replace(0, 0.001).fillna(0.001)
        df_joined['cost_deviation_ratio'] = (df_joined['cost'] - df_joined['cost_roll_median_7d']) / df_joined['cost_roll_mad_7d']
        
        # Đặc trưng lai
        if 'cpu_percent' in df_joined.columns:
            df_joined['cpu_per_dollar'] = df_joined['cpu_percent'] / (df_joined['cost'] + 1e-5)
        if 'database_connections' in df_joined.columns:
            df_joined['conn_per_dollar'] = df_joined['database_connections'] / (df_joined['cost'] + 1e-5)
        if 'gpu_utilization' in df_joined.columns:
            df_joined['gpu_per_dollar'] = df_joined['gpu_utilization'] / (df_joined['cost'] + 1e-5)

        # Ghi file kết quả
        output_file = os.path.join(output_dir, f"joined_{service_name.lower()}_metrics.csv")
        df_joined.to_csv(output_file, index=False)
        print(f"  - Đã lưu file nối dữ liệu: {output_file}")
        
    print("\n=== QUÁ TRÌNH EDA & NỐI DỮ LIỆU HOÀN THÀNH THÀNH CÔNG ===")

if __name__ == "__main__":
    run_eda_and_join()
