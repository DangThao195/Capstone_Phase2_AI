import os
import sys
import pandas as pd
import numpy as np

# Set stdout/stderr encoding to UTF-8
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

def run_training_and_evaluation():
    print("=== BẮT ĐẦU QUÁ TRÌNH HUẤN LUYỆN & ĐÁNH GIÁ MODEL ===")
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    workspace_dir = os.path.dirname(os.path.dirname(script_dir))
    input_dir = os.path.join(workspace_dir, "experience_1", "data", "splits")
    output_dir = os.path.join(workspace_dir, "experience_1", "models")
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        print(f"Đã tạo thư mục output cho models: {output_dir}")
        
    # Tìm các tập train/test
    train_files = sorted([f for f in os.listdir(input_dir) if f.startswith("train_") and f.endswith(".csv")])
    
    if not train_files:
        print(f"[ERROR] Không tìm thấy file train_*.csv nào trong thư mục: {input_dir}")
        return

    try:
        from xgboost import XGBClassifier
        from sklearn.metrics import precision_score, recall_score, f1_score, confusion_matrix
    except ImportError as e:
        print(f"[ERROR] Chưa cài đặt thư viện cần thiết. Lỗi: {e}")
        return

    # Check imbalanced-learn
    try:
        from imblearn.over_sampling import SMOTE
        has_smote = True
    except ImportError:
        has_smote = False

    for train_file in train_files:
        service_name = train_file.split("_")[1].split(".")[0].upper()
        test_file = f"test_{service_name.lower()}.csv"
        
        train_path = os.path.join(input_dir, train_file)
        test_path = os.path.join(input_dir, test_file)
        
        if not os.path.exists(test_path):
            continue
            
        print(f"\n================ HUẤN LUYỆN DỊCH VỤ: {service_name} ================")
        df_train = pd.read_csv(train_path)
        df_test = pd.read_csv(test_path)
        
        feature_cols = [col for col in df_train.columns if col.endswith("_scaled")]
        target_col = "target"
        
        X_train = df_train[feature_cols]
        y_train = df_train[target_col]
        X_test = df_test[feature_cols]
        y_test = df_test[target_col]
        
        # --- Model 1: XGBoost Baseline ---
        model_base = XGBClassifier(n_estimators=100, random_state=42, eval_metric='logloss')
        model_base.fit(X_train, y_train)
        
        # --- Model 2: XGBoost + scale_pos_weight ---
        num_neg = (y_train == 0).sum()
        num_pos = (y_train == 1).sum()
        scale_weight = num_neg / max(1, num_pos)
        model_weighted = XGBClassifier(
            n_estimators=100, 
            scale_pos_weight=scale_weight, 
            random_state=42, 
            eval_metric='logloss'
        )
        model_weighted.fit(X_train, y_train)
        
        # --- Model 3: XGBoost + SMOTE ---
        model_smote = None
        if has_smote:
            try:
                # Đảm bảo không có NaNs để tránh lỗi SMOTE
                X_train_clean = X_train.fillna(0)
                smote = SMOTE(random_state=42)
                X_train_res, y_train_res = smote.fit_resample(X_train_clean, y_train)
                model_smote = XGBClassifier(n_estimators=100, random_state=42, eval_metric='logloss')
                model_smote.fit(X_train_res, y_train_res)
            except Exception as e:
                print(f"  [WARNING] Không thể chạy SMOTE: {e}")
                model_smote = None

        models_to_eval = [
            ("XGBoost Baseline", model_base),
            ("XGBoost + scale_pos_weight", model_weighted)
        ]
        if model_smote is not None:
            models_to_eval.append(("XGBoost + SMOTE", model_smote))
            
        print("\n  - Kết quả đánh giá trên tập Test (Tháng 5):")
        best_f1 = -1
        best_model_name = ""
        best_model_obj = None
        
        for name, model in models_to_eval:
            # Đối phó với NaNs trên tập test nếu có
            X_test_clean = X_test.fillna(0)
            y_pred = model.predict(X_test_clean)
            
            precision = precision_score(y_test, y_pred, zero_division=0)
            recall = recall_score(y_test, y_pred, zero_division=0)
            f1 = f1_score(y_test, y_pred, zero_division=0)
            
            tn, fp, fn, tp = confusion_matrix(y_test, y_pred).ravel()
            fpr = fp / (fp + tn) if (fp + tn) > 0 else 0
            
            kpi_status = "✅ ĐẠT KPI" if (precision >= 0.80 and fpr <= 0.10) else "❌ CHƯA ĐẠT KPI"
            print(f"    * {name:28}: Precision={precision*100:5.2f}% | FPR={fpr*100:5.2f}% | Recall={recall*100:5.2f}% | F1={f1*100:5.2f}% | {kpi_status}")
            
            if f1 > best_f1:
                best_f1 = f1
                best_model_name = name
                best_model_obj = model
                
        # Lưu model tốt nhất
        if best_model_obj is not None:
            model_save_path = os.path.join(output_dir, f"best_model_{service_name.lower()}.json")
            best_model_obj.save_model(model_save_path)
            print(f"  -> Lưu model tốt nhất ({best_model_name}) tại: {model_save_path}")
            
            feature_spec_path = os.path.join(output_dir, f"features_spec_{service_name.lower()}.txt")
            with open(feature_spec_path, "w") as f:
                f.write("\n".join(feature_cols))

    print("\n=== HOÀN THÀNH QUÁ TRÌNH HUẤN LUYỆN & ĐÁNH GIÁ MODEL ===")

if __name__ == "__main__":
    run_training_and_evaluation()
