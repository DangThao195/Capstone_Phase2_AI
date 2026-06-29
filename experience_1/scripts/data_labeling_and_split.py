import os
import sys
import pandas as pd
import numpy as np

# Set stdout/stderr encoding to UTF-8
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

def run_labeling_and_split():
    print("=== BẮT ĐẦU QUÁ TRÌNH GÁN NHÃN & CHIA TẬP TRAIN/TEST (TEMPORAL SPLIT) ===")
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    workspace_dir = os.path.dirname(os.path.dirname(script_dir))
    input_dir = os.path.join(workspace_dir, "experience_1", "data", "engineered")
    output_dir = os.path.join(workspace_dir, "experience_1", "data", "splits")
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        print(f"Đã tạo thư mục output: {output_dir}")
        
    # Tìm các file engineered_*.csv
    files_to_process = [f for f in os.listdir(input_dir) if f.startswith("engineered_") and f.endswith(".csv")]
    
    if not files_to_process:
        print(f"[ERROR] Không tìm thấy file engineered_*.csv nào trong thư mục: {input_dir}")
        return
        
    print(f"Tìm thấy {len(files_to_process)} file dữ liệu đặc trưng để xử lý.\n")

    split_date = pd.to_datetime("2026-05-01").date()
    print(f"Mốc thời gian phân chia tập dữ liệu: {split_date}")

    for filename in files_to_process:
        filepath = os.path.join(input_dir, filename)
        service_name = filename.split("_")[1].upper()
        print(f"--- Đang phân tách dữ liệu cho: {service_name} ---")
        
        df = pd.read_csv(filepath, parse_dates=["timestamp"])
        df['date'] = df['timestamp'].dt.date
        
        if 'label' in df.columns:
            df['target'] = df['label'].map({
                'normal': 0,
                'benign': 0,
                'anomaly': 1
            })
            df['target'] = df['target'].fillna(0).astype(int)
        else:
            print("  [ERROR] Không tìm thấy cột 'label' trong dữ liệu để gán nhãn!")
            continue

        df_train = df[df['date'] < split_date].copy()
        df_test = df[df['date'] >= split_date].copy()
        
        # Ghi dữ liệu
        train_file = os.path.join(output_dir, f"train_{service_name.lower()}.csv")
        test_file = os.path.join(output_dir, f"test_{service_name.lower()}.csv")
        
        cols_to_drop = ['date', 'label']
        df_train_clean = df_train.drop(columns=[c for c in cols_to_drop if c in df_train.columns])
        df_test_clean = df_test.drop(columns=[c for c in cols_to_drop if c in df_test.columns])
        
        df_train_clean.to_csv(train_file, index=False)
        df_test_clean.to_csv(test_file, index=False)
        print(f"  * Đã lưu tập Train: {train_file}")
        print(f"  * Đã lưu tập Test: {test_file}\n")
        
    print("=== HOÀN THÀNH QUÁ TRÌNH GÁN NHÃN & PHÂN TÁCH DỮ LIỆU ===")

if __name__ == "__main__":
    run_labeling_and_split()
