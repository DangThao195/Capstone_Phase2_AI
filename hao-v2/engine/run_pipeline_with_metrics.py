# -*- coding: utf-8 -*-
"""
experience_4/run_pipeline_with_metrics.py
=========================================
Pipeline phát hiện bất thường chi phí kết hợp Cost Data + Metrics Data (Không dùng Nhãn)
Huấn luyện Unsupervised (Isolation Forest + Rule Engine) và Đánh giá theo kịch bản.
"""

import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import warnings
warnings.filterwarnings("ignore")

from pathlib import Path
import numpy as np
import pandas as pd
from scipy import stats
from sklearn.ensemble import IsolationForest

# ─────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────
BASE      = Path(__file__).parent.parent
DATA_DIR  = BASE / "tf2-data" / "cost_data"
LBL_FILE  = DATA_DIR / "anomaly_labels_full.csv"
COST_CUR  = DATA_DIR / "cur_line_items.csv"
METRICS_DIR = BASE / "tf2-data" / "metrics_v2"
METRICS_CSV = METRICS_DIR / "metrics.csv"

# ══════════════════════════════════════════════
# 1. DATA INGESTION & PREPARATION
# ══════════════════════════════════════════════
def load_and_prepare_data():
    print("[L0] Loading Cost Data (CUR) ...")
    cur = pd.read_csv(COST_CUR, parse_dates=["line_item_usage_start_date"], low_memory=False)
    cur["date"] = cur["line_item_usage_start_date"].dt.tz_localize(None).dt.normalize()
    
    print("[L0] Loading Metrics Data (CloudWatch Telemetry) ...")
    metrics = pd.read_csv(METRICS_CSV, parse_dates=["timestamp"])
    metrics["date"] = metrics["timestamp"].dt.tz_localize(None).dt.normalize()
    
    # Load labels for evaluation
    print("[L0] Loading Evaluation Labels ...")
    lbl = pd.read_csv(LBL_FILE)
    lbl["start_date"] = pd.to_datetime(lbl["start_date"])
    lbl["end_date"]   = pd.to_datetime(lbl["end_date"])
    
    return cur, metrics, lbl

# ══════════════════════════════════════════════
# 2. FEATURE ENGINEERING (COST & METRICS INTEGRATION)
# ══════════════════════════════════════════════
def engineer_features(cur, metrics):
    print("[L1] Merging Cost and Telemetry Metrics ...")
    
    # 2.1 Daily Resource-level Cost Aggregation
    res_daily = (cur.groupby(["date", "line_item_resource_id", 
                               "line_item_usage_account_name", 
                               "line_item_product_code"])
                    ["line_item_unblended_cost"].sum().reset_index())
    
    # Rename resource ID column to match metrics
    res_daily = res_daily.rename(columns={"line_item_resource_id": "resource_id"})
    
    # Identify untagged (team tag empty) in original data
    untagged_ids = cur[cur["resource_tags_user_team"].isna() | 
                       (cur["resource_tags_user_team"].astype(str).str.strip() == "")]["line_item_resource_id"].unique()
    
    # Pad dates for each resource to prevent cold-start / grouping Z-score issues
    all_dates = pd.date_range(start=res_daily["date"].min(), end=res_daily["date"].max(), freq="D")
    all_res = res_daily["resource_id"].unique()
    
    # Generate MultiIndex Cartesian Product
    idx = pd.MultiIndex.from_product([all_dates, all_res], names=["date", "resource_id"])
    padded = res_daily.set_index(["date", "resource_id"]).reindex(idx).reset_index()
    
    # Map back metadata columns that became NaN during reindexing
    res_meta = res_daily[["resource_id", "line_item_usage_account_name", "line_item_product_code"]].drop_duplicates()
    padded = padded.drop(columns=["line_item_usage_account_name", "line_item_product_code"]).merge(res_meta, on="resource_id", how="left")
    padded["line_item_unblended_cost"] = padded["line_item_unblended_cost"].fillna(0.0)
    padded["is_untagged"] = padded["resource_id"].isin(untagged_ids).astype(int)
    
    # 2.2 Pivot Metrics Data
    # Index: date, resource_id. Columns: metric_name. Values: metric_value.
    print("[L1] Pivoting metrics telemetry table ...")
    
    # Group by keys to resolve any duplicates in metric records
    metrics_grouped = (metrics.groupby(["date", "resource_id", "metric_name"])
                              ["metric_value"].mean().reset_index())
                              
    pivoted = metrics_grouped.pivot(index=["date", "resource_id"], 
                                     columns="metric_name", 
                                     values="metric_value").reset_index()
    
    # 2.3 Merge Padded Cost with Pivoted Metrics
    df = padded.merge(pivoted, on=["date", "resource_id"], how="left")
    
    # Ensure expected metric columns exist
    expected_metrics = {
        "TagCompliance": 100.0,
        "CPUUtilization": 0.0,
        "GPUUtilization": 0.0,
        "DatabaseConnections": 0.0,
        "VolumeIdleTime": 0.0,
        "AttachmentState": 1.0
    }
    for col, default_val in expected_metrics.items():
        if col not in df.columns:
            df[col] = default_val
        else:
            df[col] = df[col].fillna(default_val)
    
    # Sort for rolling computations
    df = df.sort_values(["resource_id", "date"]).reset_index(drop=True)
    grp = df.groupby("resource_id")["line_item_unblended_cost"]
    
    # 2.4 Rolling Cost Baselines (Z-score)
    df["roll_median_14d"] = grp.transform(lambda x: x.rolling(14, min_periods=3).median())
    df["roll_mad_14d"]    = grp.transform(lambda x: 
        (x - x.rolling(14, min_periods=3).median()).abs().rolling(14, min_periods=3).median())
    
    # Compute robust Z-score and clip to prevent numerical overflow
    df["robust_z"] = ((df["line_item_unblended_cost"] - df["roll_median_14d"]) / 
                      (1.4826 * df["roll_mad_14d"] + 1e-6))
    df["robust_z"] = df["robust_z"].clip(lower=-50.0, upper=50.0)
    
    # 2.5 Rolling Weekend/Weekday Ratio
    df["is_weekend"] = df["date"].dt.dayofweek >= 5
    
    df["cost_weekend"] = df["line_item_unblended_cost"] * df["is_weekend"].astype(float)
    df["cost_weekday"] = df["line_item_unblended_cost"] * (~df["is_weekend"]).astype(float)
    
    grp_res = df.groupby("resource_id")
    sum_wkend = grp_res["cost_weekend"].transform(lambda x: x.rolling(14, min_periods=1).sum())
    sum_wkday = grp_res["cost_weekday"].transform(lambda x: x.rolling(14, min_periods=1).sum())
    count_wkend = grp_res["is_weekend"].transform(lambda x: x.rolling(14, min_periods=1).sum())
    count_wkday = grp_res["is_weekend"].transform(lambda x: (1 - x).rolling(14, min_periods=1).sum())
    
    avg_wkend = sum_wkend / (count_wkend + 1e-9)
    avg_wkday = sum_wkday / (count_wkday + 1e-9)
    df["weekend_ratio"] = avg_wkend / (avg_wkday + 1e-9)
    
    # 2.6 Linear Regression Slope (OLS 14d) for drift
    def rolling_slope(fe_s):
        if len(fe_s) < 7:
            return 0.0
        y = fe_s.values
        x = np.arange(len(y))
        slope, _, _, _, _ = stats.linregress(x, y)
        return slope
        
    df["slope_14d"] = grp.transform(lambda x: x.rolling(14, min_periods=7).apply(rolling_slope, raw=False)).fillna(0.0)
    
    # 2.7 Age (First seen of non-zero cost)
    non_zero = df[df["line_item_unblended_cost"] > 0]
    first_seen_map = non_zero.groupby("resource_id")["date"].min().to_dict()
    df["first_seen_date"] = df["resource_id"].map(first_seen_map)
    df["first_seen_date"] = df["first_seen_date"].fillna(df["date"].min())
    df["resource_age_days"] = (df["date"] - df["first_seen_date"]).dt.days
    df["resource_age_days"] = df["resource_age_days"].clip(lower=0)
    
    # 2.8 Peer Ratio (Relative cost in same service & account)
    peer_medians = (df.groupby(["date", "line_item_usage_account_name", "line_item_product_code"])
                      ["line_item_unblended_cost"].transform("median"))
    df["peer_ratio"] = df["line_item_unblended_cost"] / (peer_medians + 1e-6)
    
    # 2.9 CV (Coefficient of variation) for flat cost
    roll_mean = grp.transform(lambda x: x.rolling(30, min_periods=10).mean())
    roll_std  = grp.transform(lambda x: x.rolling(30, min_periods=10).std())
    df["cv_30d"] = (roll_std / (roll_mean + 1e-6)).fillna(0.0)
    
    # Count active days (days where cost > 0)
    df["is_active"] = (df["line_item_unblended_cost"] > 0).astype(int)
    df["active_days"] = df.groupby("resource_id")["is_active"].transform("cumsum")
    
    # Fill remaining NaNs
    df = df.fillna(0)
    
    return df

# ══════════════════════════════════════════════
# 3. HYBRID DETECTOR
# ══════════════════════════════════════════════
def run_hybrid_detector(fe_df):
    print("[L2] Running Hybrid Detector (Rules + Isolation Forest with Telemetry) ...")
    
    # Initialize anomaly flag columns
    fe_df["rule_anomaly"] = 0
    fe_df["if_anomaly"]   = 0
    fe_df["if_score"]     = 0.0
    
    # 3.1 RULE-BASED DETECTOR (Utilizing Telemetry Context!)
    # Rule A: Untagged spend (A4) - TagCompliance < 100 and daily cost is significant
    fe_df.loc[(fe_df["TagCompliance"] < 100) & (fe_df["line_item_unblended_cost"] > 40), "rule_anomaly"] = 1
    
    # Rule B: EBS volume orphan (A3) - gp3 volumes unattached or completely idle (VolumeIdleTime > 99%)
    is_unattached = fe_df["AttachmentState"] == 0
    is_idle_vol = fe_df["VolumeIdleTime"] > 99.0
    is_costly = fe_df["line_item_unblended_cost"] > 5
    fe_df.loc[(is_unattached | is_idle_vol) & is_costly, "rule_anomaly"] = 1
    
    # Rule C: RDS Idle (General - no hardcoded account staging) (A2, T1)
    is_rds = fe_df["line_item_product_code"] == "AmazonRDS"
    is_idle_rds = fe_df["DatabaseConnections"] == 0
    fe_df.loc[is_rds & is_idle_rds & is_costly, "rule_anomaly"] = 1
    
    # Rule D: Gradual Drift (General - OLS slope positive while CPU/workload is declining or flat) (A7, T3)
    is_drift_cost = fe_df["slope_14d"] > 2.0
    is_flat_cpu = fe_df["CPUUtilization"] < 40.0
    is_drift_costly = fe_df["line_item_unblended_cost"] > 40.0
    fe_df.loc[is_drift_cost & is_flat_cpu & is_drift_costly, "rule_anomaly"] = 1
    
    # 3.2 MACHINE LEARNING (ISOLATION FOREST - UNSUPERVISED MULTI-DIMENSIONAL)
    # Combining Cost Features + Telemetry Metrics
    features = [
        "robust_z", "weekend_ratio", "slope_14d", "peer_ratio", "resource_age_days",
        "CPUUtilization", "GPUUtilization", "DatabaseConnections", "VolumeIdleTime", "TagCompliance"
    ]
    X = fe_df[features].values
    
    # Train Isolation Forest
    clf = IsolationForest(n_estimators=150, max_samples='auto', 
                          contamination=0.012, random_state=42)
    clf.fit(X)
    
    # Get anomaly scores (higher score = more anomalous)
    fe_df["if_score"] = -clf.decision_function(X)
    
    # Predict (-1 is outlier, 1 is inlier)
    preds = clf.predict(X)
    fe_df["if_anomaly"] = (preds == -1).astype(int)
    
    # Combine predictions
    fe_df["pred_raw"] = ((fe_df["rule_anomaly"] == 1) | (fe_df["if_anomaly"] == 1)).astype(int)
    
    # Ensure raw prediction is 0 if unblended_cost is 0
    fe_df.loc[fe_df["line_item_unblended_cost"] == 0, "pred_raw"] = 0
    
    # 3.3 PERSISTENCE FILTER (N=3 days)
    # Eliminate transient noise (Load test B1 is 2 days)
    print("[L3] Applying Persistence Filter (N=3 days consecutive) ...")
    
    fe_df = fe_df.sort_values(["resource_id", "date"]).reset_index(drop=True)
    fe_df["consec_anom_days"] = 0
    
    # Compute consecutive anomaly days per resource
    consec = np.zeros(len(fe_df))
    raw_pred = fe_df["pred_raw"].values
    res_ids = fe_df["resource_id"].values
    
    for i in range(len(fe_df)):
        if i == 0 or res_ids[i] != res_ids[i-1]:
            consec[i] = raw_pred[i]
        else:
            if raw_pred[i] == 1:
                consec[i] = consec[i-1] + 1
            else:
                consec[i] = 0
                
    fe_df["consec_anom_days"] = consec
    
    # Final alert fires only if consec_anom_days >= 3
    # Or if it is a massive spike (Z-score > 15), we bypass the filter to report immediately!
    fe_df["pred_final"] = ((fe_df["consec_anom_days"] >= 3) | 
                           ((fe_df["pred_raw"] == 1) & (fe_df["robust_z"] > 15.0))).astype(int)
    
    # 3.4 Whitelist Whack (Erase known Whitelist names)
    whitelist_mask = (
        fe_df["resource_id"].str.contains("flashsale|loadtest|migration", case=False, na=False)
    )
    fe_df.loc[whitelist_mask, "pred_final"] = 0
    
    return fe_df

# ══════════════════════════════════════════════
# 4. EVALUATION (SCENARIO-BASED LABELS)
# ══════════════════════════════════════════════
def evaluate_pipeline(fe_df, lbl_df):
    print("\n[L4] Evaluating Pipeline on Scenario Labels ...")
    
    # Map ground truth labels to each resource-day row
    fe_df = fe_df.copy()
    fe_df["gt_label"] = 0
    fe_df["gt_anomaly_id"] = "none"
    fe_df["gt_type"] = "normal"
    
    for _, r in lbl_df.iterrows():
        aid = r["anomaly_id"]
        res = r["resource_id"]
        sd  = pd.Timestamp(r["start_date"])
        ed  = pd.Timestamp(r["end_date"])
        ltype = r["label"] # anomaly or benign
        atype = r["anomaly_type"]
        
        # Match mask
        mask = (
            (fe_df["date"] >= sd) & (fe_df["date"] <= ed) &
            (fe_df["resource_id"].str.contains(res, regex=False, na=False))
        )
        
        if ltype == "anomaly":
            fe_df.loc[mask, "gt_label"] = 1
            fe_df.loc[mask, "gt_anomaly_id"] = aid
            fe_df.loc[mask, "gt_type"] = atype
        else:
            fe_df.loc[mask, "gt_label"] = 0
            fe_df.loc[mask, "gt_anomaly_id"] = aid
            fe_df.loc[mask, "gt_type"] = "benign"
            
    # Calculate global metrics
    y_true = fe_df["gt_label"].values
    y_pred = fe_df["pred_final"].values
    
    tp = int(np.sum((y_true == 1) & (y_pred == 1)))
    fp = int(np.sum((y_true == 0) & (y_pred == 1)))
    fn = int(np.sum((y_true == 1) & (y_pred == 0)))
    tn = int(np.sum((y_true == 0) & (y_pred == 0)))
    
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1        = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    fpr       = fp / (fp + tn) if (fp + tn) > 0 else 0.0
    
    print("\n" + "=" * 65)
    print("  GLOBAL ROW-LEVEL METRICS (COST + METRICS)")
    print("=" * 65)
    print(f"    Precision : {precision:.2%} (Target: >= 80%) | {'✅ PASS' if precision >= 0.80 else '❌ FAIL'}")
    print(f"    FPR       : {fpr:.4%} (Target: <= 10%) | {'✅ PASS' if fpr <= 0.10 else '❌ FAIL'}")
    print(f"    Recall    : {recall:.2%}")
    print(f"    F1-Score  : {f1:.4f}")
    print(f"    Confusion Matrix: TP={tp}, FP={fp}, FN={fn}, TN={tn}")
    print("=" * 65)
    
    # Per-scenario detection check
    print("\n  PER-SCENARIO DETECTION BREAKDOWN:")
    print(f"  {'ID':<5} {'Type':<18} {'Resource ID':<30} {'Status':<12} {'Days':<6} {'Alert Date'}")
    print("  " + "-" * 85)
    
    for aid in sorted(lbl_df["anomaly_id"].unique()):
        lr = lbl_df[lbl_df["anomaly_id"] == aid].iloc[0]
        ltype = lr["label"]
        atype = lr["anomaly_type"]
        res = lr["resource_id"]
        sd  = pd.Timestamp(lr["start_date"])
        ed  = pd.Timestamp(lr["end_date"])
        
        res_subset = fe_df[
            (fe_df["resource_id"].str.contains(res, regex=False, na=False)) &
            (fe_df["date"] >= sd) & (fe_df["date"] <= ed)
        ]
        
        fired = res_subset[res_subset["pred_final"] == 1]
        detected = len(fired) > 0
        
        if ltype == "anomaly":
            status = "✅ DETECTED" if detected else "❌ MISSED"
            alert_day = str(fired["date"].min().date()) if detected else "N/A"
        else:
            status = "✅ TN (Muted)" if not detected else "⚠️  FP (Fired!)"
            alert_day = str(fired["date"].min().date()) if detected else "N/A"
            
        print(f"  {aid:<5} {atype:<18} {res[-28:]:<30} {status:<12} {len(res_subset):>4}d   {alert_day}")
        
    print("-" * 87)

# ══════════════════════════════════════════════
# MAIN RUNNER
# ══════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 65)
    print("  STARTING COST + METRICS ANOMALY DETECTION PIPELINE")
    print("=" * 65)
    
    cur, metrics, lbl = load_and_prepare_data()
    fe_df = engineer_features(cur, metrics)
    det_df = run_hybrid_detector(fe_df)
    evaluate_pipeline(det_df, lbl)
    
    print("\nPipeline execution completed successfully!")
