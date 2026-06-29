import os
import sys
import pandas as pd

# Reconfigure stdout to use UTF-8 to avoid Windows CP1252 encoding errors
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

def run_data_splitting():
    print("=== BẮT ĐẦU BƯỚC 3: PHÂN TÁCH DỮ LIỆU (TRAIN/TEST SPLIT) ===")
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    workspace_dir = os.path.dirname(os.path.dirname(script_dir))
    input_file = os.path.join(workspace_dir, "experience_2", "data", "engineered", "engineered_raw_features.csv")
    output_dir = os.path.join(workspace_dir, "experience_2", "data", "splits")
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        print(f"Đã tạo thư mục output cho splits: {output_dir}")
        
    if not os.path.exists(input_file):
        print(f"[ERROR] Không tìm thấy file đặc trưng: {input_file}")
        return
        
    print(f"Loading engineered dataset: {input_file}")
    df = pd.read_csv(input_file, parse_dates=["timestamp"])
    print(f"Dataset loaded. Shape: {df.shape}")
    
    # 1. Gán nhãn Target (Gộp benign và normal thành 0, anomaly thành 1)
    print("\n--- Gán nhãn nhị phân (Binary Target) ---")
    df['target'] = df['label'].map({'normal': 0, 'benign': 0, 'anomaly': 1})
    print("Phân bố Target sau gộp:")
    print(df['target'].value_counts(normalize=True))
    print(df['target'].value_counts())
    
    # 2. Chia tập Train/Test theo thời gian (Time-series Split)
    # Train: Tháng 3 + Tháng 4 (2026-03-01 -> 2026-04-30)
    # Test: Tháng 5 (2026-05-01 -> 2026-05-31)
    print("\n--- Tiến hành phân tách dữ liệu theo thời gian ---")
    
    train_mask = (df["timestamp"] >= "2026-03-01") & (df["timestamp"] <= "2026-04-30 23:00:00")
    test_mask = (df["timestamp"] >= "2026-05-01") & (df["timestamp"] <= "2026-05-31 23:00:00")
    
    df_train = df[train_mask].reset_index(drop=True)
    df_test = df[test_mask].reset_index(drop=True)
    
    print(f"Tập huấn luyện (Train - Tháng 3 & 4) shape: {df_train.shape}")
    print(f"Tập kiểm thử (Test - Tháng 5) shape: {df_test.shape}")
    
    # 3. Kiểm tra phân bố target theo từng resource_type trên cả hai tập
    print("\n--- Phân bố target và anomalies theo từng Resource Type ---")
    for rtype in df['resource_type'].unique():
        train_sub = df_train[df_train['resource_type'] == rtype]
        test_sub = df_test[df_test['resource_type'] == rtype]
        
        train_anom = train_sub['target'].sum()
        test_anom = test_sub['target'].sum()
        
        print(f"  * {rtype.upper():12}:")
        print(f"    - Train: {len(train_sub):6,} dòng | {train_anom:5,} anomalies ({train_anom/max(1, len(train_sub))*100:4.1f}%)")
        print(f"    - Test : {len(test_sub):6,} dòng | {test_anom:5,} anomalies ({test_anom/max(1, len(test_sub))*100:4.1f}%)")
        
    # 4. Ghi file kết quả
    train_output = os.path.join(output_dir, "train_features.csv")
    test_output = os.path.join(output_dir, "test_features.csv")
    
    df_train.to_csv(train_output, index=False)
    df_test.to_csv(test_output, index=False)
    
    print(f"\nSaved Train split to: {train_output}")
    print(f"Saved Test split to: {test_output}")
    print("=== HOÀN THÀNH BƯỚC 3 ===")

if __name__ == "__main__":
    run_data_splitting()
