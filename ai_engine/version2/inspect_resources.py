import pandas as pd
import numpy as np
import os
import sys

# Reconfigure stdout for UTF-8
sys.stdout.reconfigure(encoding='utf-8')

from improvements import load_data, build_features_improved

def inspect():
    df, cpu_h_cols = load_data()
    df, feature_cols = build_features_improved(df, cpu_h_cols)
    
    target_resources = [
        "log-group-debug-runaway",
        "natgw-misconfig-spike",
        "i-0loadtest-fleet",
        "i-0flashsale-autoscale"
    ]
    
    for res_id in target_resources:
        print(f"\n=======================================================")
        print(f" RESOURCE: {res_id}")
        print(f"=======================================================")
        res_df = df[df['line_item_resource_id'] == res_id].sort_values(by='date')
        if len(res_df) == 0:
            print("  Không tìm thấy tài nguyên này trong dữ liệu!")
            continue
            
        print(f"Số lượng bản ghi: {len(res_df)}")
        print(f"Phạm vi ngày: {res_df['date'].min().strftime('%Y-%m-%d')} đến {res_df['date'].max().strftime('%Y-%m-%d')}")
        print(f"Nhãn thực tế: {res_df['label'].unique()}")
        
        # In ra các cột quan trọng
        cols = ['date', 'line_item_unblended_cost', 'cost_z', 'peer_ratio', 
                'cpu_percent', 'cpu_z', 'cost_cpu_z_ratio', 'cost_util_divergence', 'age_days']
        cols = [c for c in cols if c in res_df.columns]
        
        print(res_df[cols].to_string(index=False))

if __name__ == "__main__":
    inspect()
