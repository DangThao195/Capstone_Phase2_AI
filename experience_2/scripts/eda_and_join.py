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
    print("=== BẮT ĐẦU BƯỚC 1: EDA & JOIN DỮ LIỆU ===")
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    workspace_dir = os.path.dirname(os.path.dirname(script_dir))
    data_dir = os.path.join(workspace_dir, "tf2-data")
    metrics_file = os.path.join(data_dir, "metrics_data", "metrics.csv")
    cur_file = os.path.join(data_dir, "cur_line_items.csv")
    
    output_dir = os.path.join(workspace_dir, "experience_2", "data", "joined")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        print(f"Đã tạo thư mục output: {output_dir}")
        
    print(f"Loading metrics file: {metrics_file}")
    df_metrics = pd.read_csv(metrics_file, parse_dates=["timestamp"])
    df_metrics['date'] = df_metrics['timestamp'].dt.date
    print(f"Metrics dataset loaded. Shape: {df_metrics.shape}")
    
    print(f"Loading CUR file: {cur_file}")
    df_cur = pd.read_csv(cur_file, parse_dates=["line_item_usage_start_date"])
    df_cur['date'] = df_cur['line_item_usage_start_date'].dt.date
    print(f"CUR dataset loaded. Shape: {df_cur.shape}")
    
    # 1. Gom nhóm chi phí CUR hàng ngày per resource_id
    print("\n--- Gom nhóm chi phí daily từ CUR ---")
    df_cur_daily = df_cur.groupby(['date', 'line_item_resource_id']).agg({
        'line_item_unblended_cost': 'sum',
        'resource_tags_user_environment': 'first',
        'resource_tags_user_team': 'first',
        'resource_tags_user_owner': 'first',
        'resource_tags_user_cost_center': 'first',
        'line_item_usage_account_name': 'first',
        'product_region_code': 'first'
    }).reset_index()
    
    df_cur_daily.rename(columns={
        'line_item_resource_id': 'resource_id',
        'line_item_unblended_cost': 'cost'
    }, inplace=True)
    
    print(f"Gom nhóm CUR daily hoàn thành. Shape: {df_cur_daily.shape}")
    print(f"Tổng cost trong CUR: ${df_cur_daily['cost'].sum():,.2f}")
    
    # 2. Thực hiện inner join giữa metrics (hourly) và CUR daily
    print("\n--- Tiến hành Join (Inner) Metrics + CUR daily ---")
    df_joined = pd.merge(df_metrics, df_cur_daily, on=['date', 'resource_id'], how='inner')
    
    print(f"Join hoàn thành. Shape: {df_joined.shape}")
    print(f"Tỷ lệ giữ lại dòng của metrics: {len(df_joined) / len(df_metrics) * 100:.2f}%")
    print(f"Số lượng unique resource_id sau join: {df_joined['resource_id'].nunique()} (trong tổng số {df_metrics['resource_id'].nunique()} unique metrics resources)")
    
    # 3. Phân bố các đặc trưng quan trọng
    print("\n--- Phân bố nhãn sau khi Join ---")
    print(df_joined['label'].value_counts())
    
    print("\n--- Phân bố Resource Type sau khi Join ---")
    print(df_joined['resource_type'].value_counts())
    
    # 4. Ghi file kết quả tổng hợp
    output_file = os.path.join(output_dir, "joined_metrics_all.csv")
    df_joined.to_csv(output_file, index=False)
    print(f"\nSaved joined dataset to: {output_file}")
    print("=== HOÀN THÀNH BƯỚC 1 ===")

if __name__ == "__main__":
    run_eda_and_join()
