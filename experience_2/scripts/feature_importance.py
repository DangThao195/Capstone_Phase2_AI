import os
import pandas as pd
import numpy as np
from xgboost import XGBClassifier
from sklearn.preprocessing import RobustScaler

train_file = r"D:\Cloude-DevOps\Phase-2\final-production\Capstone_Phase2_AI\experience_2\data\splits\train_features.csv"
df_train = pd.read_csv(train_file)

for rtype in ["container", "cache"]:
    print(f"\n================ FEATURE IMPORTANCE FOR: {rtype.upper()} ================")
    train_sub = df_train[df_train['resource_type'] == rtype]
    
    # Features list
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
    if "network_in_bytes" in dynamic_metrics:
        dynamic_metrics.append("network_ratio")
        
    feature_cols = sorted(list(set([c for c in base_features + dynamic_metrics if c in train_sub.columns])))
    
    X_train = train_sub[feature_cols].fillna(0)
    y_train = train_sub["target"]
    
    model = XGBClassifier(n_estimators=100, random_state=42, eval_metric='logloss')
    model.fit(X_train, y_train)
    
    importances = model.feature_importances_
    indices = np.argsort(importances)[::-1]
    
    print("Top 10 features:")
    for i in range(min(10, len(feature_cols))):
        print(f"{i+1}. {feature_cols[indices[i]]}: {importances[indices[i]]:.4f}")
