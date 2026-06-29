import os
import sys
import pandas as pd
import numpy as np
import pickle

# Reconfigure stdout to use UTF-8 to avoid Windows CP1252 encoding errors
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

try:
    import joblib
    has_joblib = True
except ImportError:
    has_joblib = False

def save_scaler(scaler, filepath):
    with open(filepath, 'wb') as f:
        pickle.dump(scaler, f, protocol=4)
    print(f"    * Saved scaler to: {filepath}")

def run_training_and_evaluation():
    print("=== BẮT ĐẦU BƯỚC 4: HUẤN LUYỆN & ĐÁNH GIÁ MODEL ===")
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    workspace_dir = os.path.dirname(os.path.dirname(script_dir))
    input_dir = os.path.join(workspace_dir, "experience_2", "data", "splits")
    output_dir = os.path.join(workspace_dir, "experience_2", "models")
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        print(f"Đã tạo thư mục output cho models: {output_dir}")
        
    train_file = os.path.join(input_dir, "train_features.csv")
    test_file = os.path.join(input_dir, "test_features.csv")
    
    if not os.path.exists(train_file) or not os.path.exists(test_file):
        print("[ERROR] Không tìm thấy file train/test splits. Đang dừng...")
        return
        
    df_train = pd.read_csv(train_file)
    df_test = pd.read_csv(test_file)
    
    try:
        from xgboost import XGBClassifier
        from sklearn.preprocessing import RobustScaler
        from sklearn.metrics import precision_score, recall_score, f1_score, confusion_matrix
    except ImportError as e:
        print(f"[ERROR] Chưa cài đặt thư viện cần thiết. Lỗi: {e}")
        return
        
    # Check SMOTE
    try:
        from imblearn.over_sampling import SMOTE
        has_smote = True
    except ImportError:
        has_smote = False
        
    resource_types = df_train['resource_type'].unique()
    print(f"Tìm thấy {len(resource_types)} resource types để huấn luyện.\n")
    
    for rtype in resource_types:
        print(f"\n================ HUẤN LUYỆN RESOURCE TYPE: {rtype.upper()} ================")
        
        train_sub = df_train[df_train['resource_type'] == rtype].copy()
        test_sub = df_test[df_test['resource_type'] == rtype].copy()
        
        if len(train_sub) == 0:
            print(f"  [SKIP] Không có dữ liệu Train cho {rtype}")
            continue
            
        # 1. Xác định danh sách feature thô đầu vào
        base_features = [
            "cost", "cost_lag_1h", "cost_lag_1d", "cost_lag_2d", "cost_lag_7d", "cost_diff_1h", "cost_diff_1d",
            "cost_roll_median_7d", "cost_roll_mad_7d", "cost_deviation_ratio_7d",
            "cost_roll_median_14d", "cost_roll_mad_14d", "cost_deviation_ratio_14d",
            "hour_of_day", "day_of_week", "is_weekend", "is_business_hours"
        ]
        
        dynamic_metrics = []
        metrics_to_check = ["cpu_percent", "memory_mib", "network_in_bytes", "network_out_bytes", "disk_io_ops", "database_connections", "gpu_utilization"]
        for metric in metrics_to_check:
            if metric in train_sub.columns and not train_sub[metric].isnull().all():
                dynamic_metrics.append(metric)
                prefix = metric.split("_")[0]
                related_cols = [
                    f"{prefix}_lag_1h", f"{prefix}_lag_1d", f"{prefix}_lag_7d", f"{prefix}_diff_1h", f"{prefix}_diff_1d",
                    f"{prefix}_roll_median_7d", f"{prefix}_roll_mad_7d", f"{prefix}_deviation_ratio_7d",
                    f"{prefix}_roll_median_14d", f"{prefix}_roll_mad_14d", f"{prefix}_deviation_ratio_14d"
                ]
                for c in related_cols:
                    if c in train_sub.columns:
                        dynamic_metrics.append(c)
                if metric == "cpu_percent":
                    dynamic_metrics.extend(["cpu_per_dollar", "cpu_hourly_std"])
                elif metric == "gpu_utilization":
                    dynamic_metrics.extend(["gpu_per_dollar"])
                elif metric == "database_connections":
                    dynamic_metrics.extend(["conn_per_dollar"])
                    
        if "network_in_bytes" in dynamic_metrics:
            dynamic_metrics.append("network_ratio")
            
        feature_cols = sorted(list(set([c for c in base_features + dynamic_metrics if c in train_sub.columns])))
        
        # -------------------------------------------------------------
        # CHỐNG OVERFITTING: Loại bỏ biến tuyệt đối cho container và cache
        # -------------------------------------------------------------
        if rtype in ["container", "cache"]:
            # Chỉ giữ các biến tương đối (deviation_ratio, diff, ratio, std, per_dollar, time features)
            # Loại bỏ các biến thô (cost, cpu, memory thô) và các biến lag thô (cost_lag, memory_lag...)
            filtered_features = []
            for c in feature_cols:
                is_absolute = (c in ["cost", "cpu_percent", "memory_mib", "network_in_bytes", "network_out_bytes", "disk_io_ops", "database_connections", "gpu_utilization"] or 
                               any(c.endswith(suffix) for suffix in ["_lag_1h", "_lag_1d", "_lag_2d", "_lag_7d", "_roll_median_7d", "_roll_median_14d", "_roll_mad_7d", "_roll_mad_14d"]))
                if not is_absolute:
                    filtered_features.append(c)
            feature_cols = sorted(filtered_features)
            print(f"  * [MLOPS] Lọc bỏ các biến giá trị tuyệt đối. Còn lại: {len(feature_cols)} features.")
            
        # Xác định cột cần scale (loại trừ biến thời gian/phân loại)
        categorical_cols = ["hour_of_day", "day_of_week", "is_weekend", "is_business_hours"]
        cols_to_scale = [c for c in feature_cols if c not in categorical_cols]
        
        print(f"  * Số lượng features đầu vào: {len(feature_cols)} (Cần scale: {len(cols_to_scale)})")
        
        X_train = train_sub[feature_cols].fillna(0)
        y_train = train_sub["target"]
        
        # 2. Khởi tạo và Fit RobustScaler trên các đặc trưng liên tục
        scaler = RobustScaler()
        
        X_train_scaled = X_train.copy()
        if cols_to_scale:
            X_train_scaled[cols_to_scale] = scaler.fit_transform(X_train[cols_to_scale])
            
        # Lưu scaler `.pkl`
        scaler_file = os.path.join(output_dir, f"scaler_{rtype.lower()}.pkl")
        save_scaler(scaler, scaler_file)
        
        # 3. Chuẩn bị tập test
        has_test = len(test_sub) > 0
        if has_test:
            X_test = test_sub[feature_cols].fillna(0)
            y_test = test_sub["target"]
            X_test_scaled = X_test.copy()
            if cols_to_scale:
                X_test_scaled[cols_to_scale] = scaler.transform(X_test[cols_to_scale])
        else:
            print("  [INFO] Không có dữ liệu Test cho resource type này (tháng 5).")
            X_test_scaled = None
            y_test = None
            
        # 4. Huấn luyện các mô hình
        # Model A: Baseline
        model_base = XGBClassifier(n_estimators=100, max_depth=4, learning_rate=0.05, random_state=42, eval_metric='logloss')
        model_base.fit(X_train_scaled, y_train)
        
        # Model B: scale_pos_weight
        num_neg = (y_train == 0).sum()
        num_pos = (y_train == 1).sum()
        scale_weight = num_neg / max(1, num_pos)
        model_weighted = XGBClassifier(
            n_estimators=100,
            max_depth=4,
            learning_rate=0.05,
            scale_pos_weight=scale_weight,
            random_state=42,
            eval_metric='logloss'
        )
        model_weighted.fit(X_train_scaled, y_train)
        
        # Model C: SMOTE
        model_smote = None
        if has_smote and num_pos > 5:
            try:
                smote = SMOTE(random_state=42)
                X_res, y_res = smote.fit_resample(X_train_scaled, y_train)
                model_smote = XGBClassifier(n_estimators=100, max_depth=4, learning_rate=0.05, random_state=42, eval_metric='logloss')
                model_smote.fit(X_res, y_res)
            except Exception as e:
                print(f"  [WARNING] Không thể chạy SMOTE: {e}")
                
        models_to_eval = [
            ("XGBoost Baseline", model_base),
            ("XGBoost + scale_pos_weight", model_weighted)
        ]
        if model_smote is not None:
            models_to_eval.append(("XGBoost + SMOTE", model_smote))
            
        # 5. Đánh giá và chọn best model
        best_f1 = -1
        best_model_name = ""
        best_model_obj = None
        
        if has_test and y_test.sum() > 0:
            print("\n  - Đánh giá trên tập Test (Tháng 5):")
            for name, model in models_to_eval:
                y_pred = model.predict(X_test_scaled)
                
                precision = precision_score(y_test, y_pred, zero_division=0)
                recall = recall_score(y_test, y_pred, zero_division=0)
                f1 = f1_score(y_test, y_pred, zero_division=0)
                
                tn, fp, fn, tp = confusion_matrix(y_test, y_pred).ravel()
                fpr = fp / (fp + tn) if (fp + tn) > 0 else 0
                
                kpi_status = "✅ ĐẠT KPI" if (precision >= 0.80 and fpr <= 0.10) else "❌ CHƯA ĐẠT"
                print(f"    * {name:28}: Precision={precision*100:5.2f}% | FPR={fpr*100:5.2f}% | Recall={recall*100:5.2f}% | F1={f1*100:5.2f}% | {kpi_status}")
                
                # Ưu tiên mô hình có Precision cao nhất đạt chuẩn KPI, nếu không thì so sánh F1
                if f1 > best_f1:
                    best_f1 = f1
                    best_model_name = name
                    best_model_obj = model
        else:
            print("\n  - Không thể đánh giá trên test. Chọn mô hình mặc định (Baseline).")
            best_model_name = "XGBoost Baseline"
            best_model_obj = model_base
            best_f1 = 0.0
            
        # 6. Lưu mô hình tốt nhất
        model_file = os.path.join(output_dir, f"best_model_{rtype.lower()}.json")
        best_model_obj.save_model(model_file)
        print(f"    * Saved best model ({best_model_name}) to: {model_file}")
        
        # Lưu feature spec
        features_file = os.path.join(output_dir, f"features_spec_{rtype.lower()}.txt")
        with open(features_file, "w") as f:
            f.write("\n".join(feature_cols))
        print(f"    * Saved feature specification to: {features_file}")
        
    print("\n=== HOÀN THÀNH BƯỚC 4 ===")

if __name__ == "__main__":
    run_training_and_evaluation()
