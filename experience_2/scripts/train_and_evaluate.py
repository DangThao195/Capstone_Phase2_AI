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
            
        # -------------------------------------------------------------
        # 4. Hyperparameter Tuning với RandomizedSearchCV + TimeSeriesSplit
        # -------------------------------------------------------------
        from sklearn.model_selection import RandomizedSearchCV, TimeSeriesSplit
        from sklearn.metrics import make_scorer
        
        num_neg = (y_train == 0).sum()
        num_pos = (y_train == 1).sum()
        scale_weight = num_neg / max(1, num_pos)
        
        param_distributions = {
            "n_estimators": [50, 100, 200, 300],
            "max_depth": [3, 4, 5, 6, 7],
            "learning_rate": [0.01, 0.03, 0.05, 0.1, 0.2],
            "subsample": [0.7, 0.8, 0.9, 1.0],
            "colsample_bytree": [0.6, 0.7, 0.8, 0.9, 1.0],
            "min_child_weight": [1, 3, 5, 7],
            "gamma": [0, 0.1, 0.3, 0.5],
            "reg_alpha": [0, 0.01, 0.1],
            "reg_lambda": [1, 1.5, 2, 3],
            "scale_pos_weight": [1, scale_weight, max(1, scale_weight * 0.5)],
        }
        
        tscv = TimeSeriesSplit(n_splits=3)
        
        # Custom F1 scorer: trả về 0.0 khi fold không có positive sample (thay vì crash)
        def safe_f1(y_true, y_pred):
            if y_true.sum() == 0:
                return 0.0
            return f1_score(y_true, y_pred, zero_division=0)
        
        safe_f1_scorer = make_scorer(safe_f1)
        
        base_estimator = XGBClassifier(
            random_state=42,
            eval_metric='logloss',
            verbosity=0,
        )
        
        print(f"\n  --- Bắt đầu RandomizedSearchCV (n_iter=30, cv=TimeSeriesSplit(3)) ---")
        print(f"  * Class balance: {num_neg} normal vs {num_pos} anomaly (ratio={scale_weight:.1f}:1)")
        print(f"  * Thử 30 tổ hợp ngẫu nhiên × 3 folds = 90 lần fit")
        
        search = RandomizedSearchCV(
            estimator=base_estimator,
            param_distributions=param_distributions,
            n_iter=30,
            scoring=safe_f1_scorer,
            cv=tscv,
            random_state=42,
            n_jobs=-1,
            verbose=0,
            return_train_score=True,
            error_score=0.0,  # Trả về 0 thay vì crash khi fold lỗi
        )
        
        search.fit(X_train_scaled, y_train)
        
        best_params = search.best_params_
        best_cv_f1 = search.best_score_
        
        print(f"\n  --- Kết quả RandomizedSearchCV ---")
        print(f"  * Best CV F1 Score: {best_cv_f1*100:.2f}%")
        print(f"  * Best Hyperparameters:")
        for param_name, param_val in sorted(best_params.items()):
            print(f"      {param_name}: {param_val}")
        
        # -------------------------------------------------------------
        # 5. Phân tích Chi tiết Per-Fold Metrics
        # -------------------------------------------------------------
        cv_results = search.cv_results_
        best_idx = search.best_index_
        
        cv_report = {
            "resource_type": rtype,
            "best_cv_f1": round(best_cv_f1, 4),
            "best_params": best_params,
            "per_fold": [],
        }
        
        print(f"\n  --- Chi tiết Metrics per Fold (Best Configuration) ---")
        for fold_i in range(3):
            train_score = cv_results[f"split{fold_i}_train_score"][best_idx]
            val_score = cv_results[f"split{fold_i}_test_score"][best_idx]
            overfit_gap = train_score - val_score
            fold_info = {
                "fold": fold_i + 1,
                "train_f1": round(train_score, 4),
                "val_f1": round(val_score, 4),
                "overfit_gap": round(overfit_gap, 4),
            }
            cv_report["per_fold"].append(fold_info)
            status = "⚠️ OVERFIT" if overfit_gap > 0.15 else "✅ OK"
            print(f"    Fold {fold_i+1}: Train F1={train_score*100:5.2f}% | Val F1={val_score*100:5.2f}% | Gap={overfit_gap*100:5.2f}% | {status}")
        
        # Tính std giữa các fold
        val_scores = [cv_report["per_fold"][i]["val_f1"] for i in range(3)]
        cv_std = float(np.std(val_scores))
        cv_report["cv_std"] = round(cv_std, 4)
        stability = "✅ ỔN ĐỊNH" if cv_std < 0.05 else "⚠️ BIẾN ĐỘNG"
        print(f"    Cross-Fold Std: {cv_std*100:.2f}% → {stability}")
        
        # -------------------------------------------------------------
        # 6. Retrain trên toàn bộ Train với best_params → Đánh giá Test
        # -------------------------------------------------------------
        print(f"\n  --- Retrain trên toàn bộ Train ({len(X_train_scaled)} samples) với best_params ---")
        final_model = XGBClassifier(
            **best_params,
            random_state=42,
            eval_metric='logloss',
            verbosity=0,
        )
        final_model.fit(X_train_scaled, y_train)
        
        # Đánh giá trên tập Test (tháng 5)
        if has_test and y_test is not None and y_test.sum() > 0:
            y_pred = final_model.predict(X_test_scaled)
            
            precision = precision_score(y_test, y_pred, zero_division=0)
            recall = recall_score(y_test, y_pred, zero_division=0)
            f1 = f1_score(y_test, y_pred, zero_division=0)
            
            tn, fp, fn, tp = confusion_matrix(y_test, y_pred).ravel()
            fpr = fp / (fp + tn) if (fp + tn) > 0 else 0
            
            # KPI Check
            kpi_pass = precision >= 0.80 and fpr <= 0.10
            kpi_status = "✅ ĐẠT KPI" if kpi_pass else "❌ CHƯA ĐẠT"
            
            print(f"\n  --- Đánh giá trên tập Test (Tháng 5) ---")
            print(f"    * Precision: {precision*100:5.2f}%")
            print(f"    * Recall:    {recall*100:5.2f}%")
            print(f"    * F1 Score:  {f1*100:5.2f}%")
            print(f"    * FPR:       {fpr*100:5.2f}%")
            print(f"    * Confusion: TP={tp} FP={fp} TN={tn} FN={fn}")
            print(f"    * KPI (Precision≥80% AND FPR≤10%): {kpi_status}")
            
            cv_report["test_metrics"] = {
                "precision": round(precision, 4),
                "recall": round(recall, 4),
                "f1": round(f1, 4),
                "fpr": round(fpr, 4),
                "tp": int(tp), "fp": int(fp), "tn": int(tn), "fn": int(fn),
                "kpi_pass": kpi_pass,
            }
        else:
            print("\n  - Không có dữ liệu Test. Dùng CV score làm đánh giá duy nhất.")
            cv_report["test_metrics"] = None
            
        # -------------------------------------------------------------
        # 7. Lưu model + params + cv_report
        # -------------------------------------------------------------
        import json as json_module
        
        model_file = os.path.join(output_dir, f"best_model_{rtype.lower()}.json")
        final_model.save_model(model_file)
        print(f"\n    * Saved best model to: {model_file}")
        
        features_file = os.path.join(output_dir, f"features_spec_{rtype.lower()}.txt")
        with open(features_file, "w") as f:
            f.write("\n".join(feature_cols))
        print(f"    * Saved feature specification to: {features_file}")
        
        # NEW: Export best hyperparameters
        params_file = os.path.join(output_dir, f"best_params_{rtype.lower()}.json")
        serializable_params = {k: (float(v) if isinstance(v, (np.floating, np.integer)) else v) for k, v in best_params.items()}
        with open(params_file, "w") as f:
            json_module.dump(serializable_params, f, indent=2)
        print(f"    * Saved best hyperparameters to: {params_file}")
        
        # NEW: Export CV report
        cv_report_file = os.path.join(output_dir, f"cv_report_{rtype.lower()}.json")
        with open(cv_report_file, "w") as f:
            json_module.dump(cv_report, f, indent=2, default=str)
        print(f"    * Saved CV report to: {cv_report_file}")
        
    print("\n=== HOÀN THÀNH BƯỚC 4 ===")

if __name__ == "__main__":
    run_training_and_evaluation()

