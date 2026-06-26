import pandas as pd
import numpy as np
import os
import sys
import glob
import xgboost as xgb
import hashlib
from sklearn.preprocessing import RobustScaler
from sklearn.utils.class_weight import compute_sample_weight

# Reconfigure stdout for UTF-8
sys.stdout.reconfigure(encoding='utf-8')

# Import functions from improvements.py
from improvements import load_data, build_features_improved, get_resource_type, TARGET_MAPPING

def run_error_analysis():
    df, cpu_h_cols = load_data()
    timeline = sorted(df['date'].unique())
    df, feature_cols = build_features_improved(df, cpu_h_cols)
    df['target'] = df['label'].map(TARGET_MAPPING)
    
    # We will analyze using Gap = 7 days as it is our most realistic scenario
    gap_days = 7
    folds_def = [
        ("Fold 1", timeline[:51 - gap_days], timeline[51:65]),
        ("Fold 2", timeline[:65 - gap_days], timeline[65:78]),
        ("Fold 3", timeline[:78 - gap_days], timeline[78:])
    ]
    
    print("\n" + "="*80)
    print("  BẮT ĐẦU PHÂN TÍCH LỖI PHÂN LOẠI (CLASSIFICATION ERROR ANALYSIS)")
    print("="*80)
    
    for fold_name, train_dates, test_dates in folds_def:
        print(f"\n>>> {fold_name.upper()} ({len(train_dates)} ngày Train -> {len(test_dates)} ngày Test)")
        
        df_train = df[df['date'].isin(train_dates)].copy()
        df_test = df[df['date'].isin(test_dates)].copy()
        
        # weekend_ratio
        avg_wknd = df_train[df_train['is_weekend'] == 1].groupby('line_item_resource_id')['line_item_unblended_cost'].mean()
        avg_wkday = df_train[df_train['is_weekend'] == 0].groupby('line_item_resource_id')['line_item_unblended_cost'].mean()
        ratio = avg_wknd / (avg_wkday + 1e-5)
        df_train['weekend_ratio'] = df_train['line_item_resource_id'].map(ratio).fillna(0)
        df_test['weekend_ratio'] = df_test['line_item_resource_id'].map(ratio).fillna(0)

        # Compute service baseline features (leak-proof)
        df_train['net_total'] = df_train['network_in_bytes'] + df_train['network_out_bytes']
        df_test['net_total'] = df_test['network_in_bytes'] + df_test['network_out_bytes']

        service_cost_median = df_train.groupby('line_item_product_code')['line_item_unblended_cost'].median()
        service_cost_mad = df_train.groupby('line_item_product_code')['line_item_unblended_cost'].apply(
            lambda x: np.median(np.abs(x - np.median(x))) if len(x) > 0 else 0.0
        )
        service_cpu_median = df_train.groupby('line_item_product_code')['cpu_percent'].median()
        service_cpu_mad = df_train.groupby('line_item_product_code')['cpu_percent'].apply(
            lambda x: np.median(np.abs(x - np.median(x))) if len(x) > 0 else 0.0
        )
        service_net_median = df_train.groupby('line_item_product_code')['net_total'].median()
        service_net_mad = df_train.groupby('line_item_product_code')['net_total'].apply(
            lambda x: np.median(np.abs(x - np.median(x))) if len(x) > 0 else 0.0
        )

        for frame in [df_train, df_test]:
            med_cost = frame['line_item_product_code'].map(service_cost_median).fillna(0.0)
            mad_cost = frame['line_item_product_code'].map(service_cost_mad).fillna(0.0)
            med_cpu = frame['line_item_product_code'].map(service_cpu_median).fillna(0.0)
            mad_cpu = frame['line_item_product_code'].map(service_cpu_mad).fillna(0.0)
            med_net = frame['line_item_product_code'].map(service_net_median).fillna(0.0)
            mad_net = frame['line_item_product_code'].map(service_net_mad).fillna(0.0)

            frame['service_cost_z'] = (frame['line_item_unblended_cost'] - med_cost) / (1.4826 * mad_cost + 1e-5)
            frame['service_cpu_z'] = (frame['cpu_percent'] - med_cpu) / (1.4826 * med_cpu + 1e-5)
            frame['service_net_z'] = (frame['net_total'] - med_net) / (1.4826 * med_net + 1e-5)

            frame['service_cost_cpu_ratio'] = frame['service_cost_z'] / (frame['service_cpu_z'].abs() + 1e-5)
            frame['service_cost_net_ratio'] = frame['service_cost_z'] / (frame['service_net_z'].abs() + 1e-5)

        local_feature_cols = feature_cols.copy() + [
            'service_cost_z', 'service_cpu_z', 'service_net_z',
            'service_cost_cpu_ratio', 'service_cost_net_ratio'
        ]

        X_train = df_train[local_feature_cols].values
        y_train = df_train['target'].values
        X_test = df_test[local_feature_cols].values
        y_test = df_test['target'].values
        
        # Train model (just like in improvements.py)
        from sklearn.model_selection import GroupKFold
        from sklearn.metrics import precision_recall_curve
        groups_tr = df_train['line_item_resource_id'].values
        cv_gkf = GroupKFold(n_splits=min(5, len(np.unique(groups_tr))))
        oof_prob_anomaly = np.zeros(len(df_train))
        
        for tr_cv_idx, val_cv_idx in cv_gkf.split(X_train, y_train, groups=groups_tr):
            X_tr_cv, y_tr_cv = X_train[tr_cv_idx], y_train[tr_cv_idx]
            X_val_cv = X_train[val_cv_idx]
            scaler_cv = RobustScaler()
            X_tr_cv_scaled = scaler_cv.fit_transform(X_tr_cv)
            X_val_cv_scaled = scaler_cv.transform(X_val_cv)
            cv_weights = compute_sample_weight(class_weight='balanced', y=y_tr_cv)
            model_cv = xgb.XGBClassifier(
                objective='multi:softprob', num_class=3, eval_metric='mlogloss',
                n_estimators=350, max_depth=4, learning_rate=0.03,
                subsample=0.8, colsample_bytree=0.7,
                reg_alpha=1.0, reg_lambda=2.0, min_child_weight=3,
                random_state=42
            )
            model_cv.fit(X_tr_cv_scaled, y_tr_cv, sample_weight=cv_weights)
            oof_prob_anomaly[val_cv_idx] = model_cv.predict_proba(X_val_cv_scaled)[:, 1]
            
        y_tr_binary = (y_train == 1).astype(int)
        prec_cv, rec_cv, thr_cv = precision_recall_curve(y_tr_binary, oof_prob_anomaly)
        ok_cv = np.where(prec_cv[:-1] >= 0.80)[0]
        if len(ok_cv) > 0:
            chosen_idx = ok_cv[np.argmax(rec_cv[ok_cv])]
            t_anomaly = thr_cv[chosen_idx]
        else:
            chosen_idx = np.argmax(prec_cv[:-1])
            t_anomaly = thr_cv[chosen_idx]
        
        scaler = RobustScaler()
        X_train_scaled = scaler.fit_transform(X_train)
        X_test_scaled = scaler.transform(X_test)
        
        train_weights = compute_sample_weight(class_weight='balanced', y=y_train)
        final_model = xgb.XGBClassifier(
            objective='multi:softprob', num_class=3, eval_metric='mlogloss',
            n_estimators=350, max_depth=4, learning_rate=0.03,
            subsample=0.8, colsample_bytree=0.7,
            reg_alpha=1.0, reg_lambda=2.0, min_child_weight=3,
            random_state=42
        )
        final_model.fit(X_train_scaled, y_train, sample_weight=train_weights)
        
        test_probs = final_model.predict_proba(X_test_scaled)
        y_pred = np.zeros(len(test_probs), dtype=int)
        for i in range(len(test_probs)):
            if test_probs[i, 1] >= t_anomaly and (test_probs[i, 1] > test_probs[i, 2] or test_probs[i, 2] < 0.50):
                y_pred[i] = 1
            elif test_probs[i, 2] >= 0.20:
                y_pred[i] = 2
            else:
                y_pred[i] = np.argmax(test_probs[i])
                
        df_test['pred'] = y_pred
        df_test['prob_normal'] = test_probs[:, 0]
        df_test['prob_anomaly'] = test_probs[:, 1]
        df_test['prob_benign'] = test_probs[:, 2]
        df_test['res_type'] = df_test.apply(lambda r: get_resource_type(r['line_item_resource_id'], r['label']), axis=1)
        
        # 1. False Negatives (Actual Anomaly, but predicted normal/benign)
        print("\n--- PHÂN TÍCH FALSE NEGATIVES (Bỏ sót Anomaly) ---")
        fns = df_test[(df_test['target'] == 1) & (df_test['pred'] != 1)]
        if len(fns) == 0:
            print("  Không có False Negatives nào!")
        else:
            print(f"  Có {len(fns)} trường hợp bị bỏ sót:")
            for name, gp in fns.groupby('res_type'):
                print(f"\n  * Loại sự cố: {name} (Tổng số ngày bị bỏ sót: {len(gp)})")
                sample = gp.sort_values(by='date').iloc[0]
                print(f"    - Ví dụ ngày: {sample['date'].strftime('%Y-%m-%d')}")
                print(f"    - ID tài nguyên: {sample['line_item_resource_id']}")
                print(f"    - Xác suất dự đoán: Normal={sample['prob_normal']:.2%}, Anomaly={sample['prob_anomaly']:.2%}, Benign={sample['prob_benign']:.2%} (Ngưỡng Anomaly: {t_anomaly:.4f})")
                print(f"    - Cost: {sample['line_item_unblended_cost']:.2f} | cost_z: {sample['cost_z']:.4f} | peer_ratio: {sample['peer_ratio']:.4f}")
                print(f"    - CPU: {sample['cpu_percent']:.2f}% | cpu_z: {sample['cpu_z']:.4f}")
                print(f"    - PEER FEATURES: peer_cost_z={sample['peer_cost_z']:.4f} | peer_cpu_z={sample['peer_cpu_z']:.4f} | peer_cost_cpu_ratio={sample['peer_cost_cpu_ratio']:.4f}")
                print(f"    - SERVICE BASELINE: service_cost_z={sample['service_cost_z']:.4f} | service_cpu_z={sample['service_cpu_z']:.4f} | service_cost_cpu_ratio={sample['service_cost_cpu_ratio']:.4f}")

        # 2. False Positives on Benign (Actual Benign, but predicted anomaly)
        print("\n--- PHÂN TÍCH FALSE POSITIVES TRÊN BENIGN (Báo nhầm Benign thành Anomaly) ---")
        fps = df_test[(df_test['target'] == 2) & (df_test['pred'] == 1)]
        if len(fps) == 0:
            print("  Không có báo nhầm trên các benign event nào!")
        else:
            print(f"  Có {len(fps)} trường hợp benign bị báo nhầm thành anomaly:")
            for name, gp in fps.groupby('res_type'):
                print(f"\n  * Loại benign: {name} (Tổng số ngày báo nhầm: {len(gp)})")
                sample = gp.sort_values(by='date').iloc[0]
                print(f"    - Ví dụ ngày: {sample['date'].strftime('%Y-%m-%d')}")
                print(f"    - ID tài nguyên: {sample['line_item_resource_id']}")
                print(f"    - Xác suất dự đoán: Normal={sample['prob_normal']:.2%}, Anomaly={sample['prob_anomaly']:.2%}, Benign={sample['prob_benign']:.2%} (Ngưỡng Anomaly: {t_anomaly:.4f})")
                print(f"    - Cost: {sample['line_item_unblended_cost']:.2f} | cost_z: {sample['cost_z']:.4f} | peer_ratio: {sample['peer_ratio']:.4f}")
                print(f"    - CPU: {sample['cpu_percent']:.2f}% | cpu_z: {sample['cpu_z']:.4f}")
                print(f"    - PEER FEATURES: peer_cost_z={sample['peer_cost_z']:.4f} | peer_cpu_z={sample['peer_cpu_z']:.4f} | peer_cost_cpu_ratio={sample['peer_cost_cpu_ratio']:.4f}")
                print(f"    - SERVICE BASELINE: service_cost_z={sample['service_cost_z']:.4f} | service_cpu_z={sample['service_cpu_z']:.4f} | service_cost_cpu_ratio={sample['service_cost_cpu_ratio']:.4f}")

if __name__ == "__main__":
    run_error_analysis()
