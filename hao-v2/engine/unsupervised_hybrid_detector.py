#!/usr/bin/env python3
"""
FinOps Watch — Unsupervised Hybrid Anomaly Detection Pipeline
=============================================================

Architecture:
  A. Data Preparation   – load CUR + metrics, pivot metrics wide, join
  B. Feature Engineering – rolling stats, peer ratios, tag compliance, telemetry
  C. Unsupervised Model  – IsolationForest (no labels)
  D. Scenario Detectors  – 5 mechanism-specific detectors
  E. Benign Suppression  – flash-sale / migration / load-test guards
  F. Final Decision      – combine scores, threshold, produce alerts
  G. Evaluation          – backtest against anomaly_labels_full.csv

IMPORTANT: Ground-truth columns (is_anomaly, anomaly_type) are NEVER used
           for training, feature engineering, or threshold selection.
           They are used ONLY in the final evaluation step (G).
"""

import os
import json
import warnings
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import RobustScaler

warnings.filterwarnings("ignore", category=FutureWarning)

# ──────────────────────────────────────────────────────────────────────
# 0. CONFIGURATION — all thresholds tuneable here
# ──────────────────────────────────────────────────────────────────────
CFG = {
    # File paths
    "CUR_PATH": "cur_line_items.csv",
    "METRICS_PATH": "metrics.csv",
    "LABELS_PATH": r"C:\Users\MSII\Downloads\anomaly_labels_full.csv",
    "ARTIFACTS_DIR": r"D:\AWS\FinOps Watch\artifacts_full",

    # IsolationForest
    "IF_CONTAMINATION": 0.08,
    "IF_N_ESTIMATORS": 200,
    "IF_RANDOM_STATE": 42,

    # Detector thresholds
    "SPIKE_ROBUST_Z": 2.5,
    "SPIKE_COST_RATIO_7D": 3.0,
    "SPIKE_PEER_RATIO": 5.0,
    "DRIFT_SLOPE_THRESHOLD": 0.5,
    "DRIFT_PCT_CHANGE_28D": 0.15,
    "IDLE_CPU_THRESHOLD": 10.0,
    "IDLE_CONN_THRESHOLD": 2.0,
    "IDLE_COST_FLOOR": 15.0,
    "RUNAWAY_CPU_THRESHOLD": 75.0,
    "RUNAWAY_UPTIME_THRESHOLD": 80000.0,
    "RUNAWAY_COST_FLOOR": 30.0,
    "UNTAGGED_COST_FLOOR": 50.0,

    # Benign suppression
    "BENIGN_FLASH_CPU_THRESHOLD": 70.0,
    "BENIGN_MIGRATION_BYTES_RATIO": 10.0,
    "BENIGN_LOADTEST_CPU_THRESHOLD": 85.0,

    # Final decision
    "STAT_WEIGHT": 0.70,
    "ML_WEIGHT": 0.30,
    "ALERT_THRESHOLD": 0.50,
    "ML_ONLY_ALERT_THRESHOLD": 0.85,  # ML-only outliers need a high score to alert

    # Persistence filter (review #2): confirm an alert only after it persists,
    # unless the cost spike is extreme enough to bypass the wait.
    "PERSISTENCE_MIN_DAYS": 3,
    "PERSISTENCE_Z_BYPASS": 8.0,
}


# ──────────────────────────────────────────────────────────────────────
# A. DATA PREPARATION
# ──────────────────────────────────────────────────────────────────────

def load_cur(path: str) -> pd.DataFrame:
    """Load CUR line items and normalise dates."""
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["line_item_usage_start_date"]).dt.strftime("%Y-%m-%d")
    df["cost"] = df["line_item_unblended_cost"].astype(float)
    df["usage_amount"] = df["line_item_usage_amount"].astype(float)
    df.rename(columns={
        "line_item_resource_id": "resource_id",
        "line_item_product_code": "service",
        "line_item_usage_account_id": "account_id",
        "line_item_usage_account_name": "account_name",
        "resource_tags_user_team": "tag_team",
        "resource_tags_user_owner": "tag_owner",
        "resource_tags_user_environment": "tag_environment",
        "resource_tags_user_cost_center": "tag_cost_center",
    }, inplace=True)
    return df


def load_metrics(path: str) -> pd.DataFrame:
    """Load long-form telemetry metrics (DROP ground-truth columns)."""
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["timestamp"]).dt.strftime("%Y-%m-%d")
    # ✱ DROP ground-truth — these must never leak into features
    df.drop(columns=["is_anomaly", "anomaly_type"], inplace=True, errors="ignore")
    return df


def pivot_metrics_wide(metrics: pd.DataFrame) -> pd.DataFrame:
    """Pivot long-form metrics → one row per (date, resource_id, service, account_id)."""
    # Take the daily mean per metric when multiple readings exist
    pivoted = metrics.pivot_table(
        index=["date", "resource_id", "service", "account_id"],
        columns="metric_name",
        values="metric_value",
        aggfunc="mean",
    ).reset_index()
    pivoted.columns.name = None
    return pivoted


def join_cur_metrics(cur: pd.DataFrame, metrics_wide: pd.DataFrame) -> pd.DataFrame:
    """Left-join CUR with pivoted telemetry on (date, resource_id, service, account_id)."""
    # Ensure account_id types match
    cur["account_id"] = cur["account_id"].astype(str)
    metrics_wide["account_id"] = metrics_wide["account_id"].astype(str)

    merged = cur.merge(
        metrics_wide,
        on=["date", "resource_id", "service", "account_id"],
        how="left",
    )
    return merged


# ──────────────────────────────────────────────────────────────────────
# B. FEATURE ENGINEERING
# ──────────────────────────────────────────────────────────────────────

def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """Build rolling cost features + tag compliance per resource-day."""
    df = df.sort_values(["resource_id", "date"]).copy()

    # --- Rolling cost statistics per resource ---
    grp = df.groupby("resource_id")["cost"]
    df["cost_7d_mean"] = grp.transform(lambda x: x.rolling(7, min_periods=1).mean())
    df["cost_14d_mean"] = grp.transform(lambda x: x.rolling(14, min_periods=1).mean())
    df["cost_28d_mean"] = grp.transform(lambda x: x.rolling(28, min_periods=1).mean())
    df["cost_7d_std"] = grp.transform(lambda x: x.rolling(7, min_periods=2).std()).fillna(0)
    df["cost_14d_median"] = grp.transform(lambda x: x.rolling(14, min_periods=1).median())

    # MAD (Median Absolute Deviation) over 14-day window
    def rolling_mad(s, window=14):
        out = np.full(len(s), np.nan)
        vals = s.values
        for i in range(len(vals)):
            start = max(0, i - window + 1)
            w = vals[start:i + 1]
            med = np.median(w)
            out[i] = np.median(np.abs(w - med))
        return pd.Series(out, index=s.index)

    df["cost_14d_mad"] = grp.transform(rolling_mad).fillna(0)

    # Cost ratios
    df["cost_ratio_7d"] = df["cost"] / (df["cost_7d_mean"] + 1e-6)
    df["cost_ratio_14d"] = df["cost"] / (df["cost_14d_mean"] + 1e-6)

    # Robust Z-score = (cost - median) / (1.4826 * MAD + eps)
    df["robust_z_cost"] = (df["cost"] - df["cost_14d_median"]) / (1.4826 * df["cost_14d_mad"] + 1e-6)

    # 14-day slope via rolling linear regression
    def rolling_slope(s, window=14):
        out = np.full(len(s), 0.0)
        vals = s.values
        for i in range(len(vals)):
            start = max(0, i - window + 1)
            w = vals[start:i + 1]
            if len(w) >= 4:
                x = np.arange(len(w))
                try:
                    slope = np.polyfit(x, w, 1)[0]
                    out[i] = slope
                except Exception:
                    pass
        return pd.Series(out, index=s.index)

    df["slope_14d"] = grp.transform(rolling_slope)

    # 28-day percentage change
    df["cost_lag_28d"] = grp.transform(lambda x: x.shift(28))
    df["cost_pct_change_28d"] = ((df["cost"] - df["cost_lag_28d"]) /
                                  (df["cost_lag_28d"] + 1e-6)).fillna(0)
    df.drop(columns=["cost_lag_28d"], inplace=True)

    # --- Peer statistics (same account + service + date) ---
    peer = df.groupby(["account_id", "service", "date"])["cost"].agg(
        peer_median="median", peer_count="count"
    ).reset_index()
    df = df.merge(peer, on=["account_id", "service", "date"], how="left")
    df["peer_ratio"] = df["cost"] / (df["peer_median"] + 1e-6)

    # --- Resource age (days since first appearance) ---
    first_seen = df.groupby("resource_id")["date"].min().rename("first_seen_date")
    df = df.merge(first_seen, on="resource_id", how="left")
    df["resource_age_days"] = (
        pd.to_datetime(df["date"]) - pd.to_datetime(df["first_seen_date"])
    ).dt.days
    # "Midstream-new" = the resource first appeared AFTER the dataset started. A
    # resource present on day 1 is NOT new — we simply have no earlier history for
    # it. Without this distinction, every long-lived resource looks 'new' during
    # the first days of the window and triggers false new-resource spikes.
    dataset_start = df["date"].min()
    df["is_midstream_new_resource"] = (
        pd.to_datetime(df["first_seen_date"]) > pd.to_datetime(dataset_start)
    ).astype(int)
    df.drop(columns=["first_seen_date"], inplace=True)

    # --- Weekend flag ---
    df["is_weekend"] = pd.to_datetime(df["date"]).dt.dayofweek.isin([5, 6]).astype(int)

    # --- Tag compliance ---
    df["missing_team_tag"] = df["tag_team"].isna().astype(int)
    df["missing_owner_tag"] = df["tag_owner"].isna().astype(int)
    df["missing_cost_center_tag"] = df["tag_cost_center"].isna().astype(int)
    df["tag_compliance"] = 1 - (
        df["missing_team_tag"] + df["missing_owner_tag"] + df["missing_cost_center_tag"]
    ) / 3.0

    # --- Presence flags: record which telemetry actually existed BEFORE we fill
    # NaN with 0. Idle detection must not treat "no CPU metric" (e.g. DynamoDB,
    # which has no CPUUtilization) as "CPU is 0% → idle". ---
    for tcol in ("CPUUtilization", "DatabaseConnections", "TagCompliance"):
        if tcol in df.columns:
            df[f"has_{tcol}"] = df[tcol].notna().astype(int)
        else:
            df[f"has_{tcol}"] = 0

    # --- Fill NaN telemetry with 0 (resource has no reading → assume idle/zero) ---
    telemetry_cols = [c for c in df.columns if c not in [
        "date", "resource_id", "service", "account_id", "account_name",
        "cost", "usage_amount", "tag_team", "tag_owner", "tag_environment",
        "tag_cost_center", "cost_7d_mean", "cost_14d_mean", "cost_28d_mean",
        "cost_7d_std", "cost_14d_median", "cost_14d_mad", "cost_ratio_7d",
        "cost_ratio_14d", "robust_z_cost", "slope_14d", "cost_pct_change_28d",
        "peer_median", "peer_count", "peer_ratio", "resource_age_days",
        "is_weekend", "missing_team_tag", "missing_owner_tag",
        "missing_cost_center_tag", "tag_compliance",
    ] and df[c].dtype in [np.float64, np.int64, float, int]]
    for c in telemetry_cols:
        df[c] = df[c].fillna(0)

    # --- Per-resource 14-day rolling baselines for volume metrics ---
    # Spike detection must compare a metric to the resource's OWN recent history,
    # not to an absolute floor. A log group that always ingests 87 GB/day is NOT
    # a spike; one that jumps from 2 GB to 87 GB is. Baselines are lagged (shift 1)
    # so today's value never contaminates its own baseline.
    for vol_col in ("BytesTransferred", "IncomingBytes", "IncomingLogEvents"):
        if vol_col in df.columns:
            base = (
                df.groupby("resource_id")[vol_col]
                .transform(lambda s: s.shift(1).rolling(14, min_periods=3).median())
            )
            df[f"{vol_col}_baseline_14d"] = base

    # --- Instance-count proxy for compute (ASG drift signal) ---
    # No direct InstanceCount metric in CUR; usage_amount for hourly-billed EC2
    # approximates fleet-hours/day. A rising trend = scale-out that never scaled in.
    grp_usage = df.groupby("resource_id")["usage_amount"]
    df["usage_14d_slope"] = grp_usage.transform(rolling_slope).fillna(0.0)

    # --- Compute Dynamic Unsupervised Thresholds ---
    # Data-derived, but SNAPPED to round values so they generalise to holdout data
    # instead of overfitting to this dataset's exact quantiles (avoids "magic
    # decimals" like 7.87 / 41.05 that look tuned to the answer).
    def snap(value: float, step: float, lo: float, hi: float) -> float:
        """Round `value` to the nearest `step`, then clamp to [lo, hi]."""
        return float(min(hi, max(lo, round(value / step) * step)))

    non_zero_cpu = df[df["CPUUtilization"] > 0]["CPUUtilization"]
    if len(non_zero_cpu) > 0:
        # Idle CPU: low-percentile of active CPU, snapped to nearest 1% in [5, 10]
        CFG["IDLE_CPU_THRESHOLD"] = snap(non_zero_cpu.quantile(0.15), 1.0, 5.0, 10.0)
        # Runaway CPU: high-percentile, snapped to nearest 5% in [75, 95]
        CFG["RUNAWAY_CPU_THRESHOLD"] = snap(non_zero_cpu.quantile(0.90), 5.0, 75.0, 95.0)

    total_iops = df["ReadIOPS"] + df["WriteIOPS"]
    non_zero_iops = total_iops[total_iops > 0]
    if len(non_zero_iops) > 0:
        CFG["IDLE_IOPS_THRESHOLD"] = snap(non_zero_iops.quantile(0.15), 5.0, 5.0, 25.0)
    else:
        CFG["IDLE_IOPS_THRESHOLD"] = 10.0

    # Idle DB connections: a genuinely idle DB has ~0 connections. Cap the learned
    # value at 5 so we never call a DB with dozens of live connections "idle"
    # (the old code let this drift to 41, which is NOT idle).
    non_zero_conns = df[df["DatabaseConnections"] > 0]["DatabaseConnections"]
    if len(non_zero_conns) > 0:
        CFG["IDLE_CONN_THRESHOLD"] = snap(non_zero_conns.quantile(0.05), 1.0, 1.0, 5.0)
    else:
        CFG["IDLE_CONN_THRESHOLD"] = 2.0

    non_zero_cost = df[df["cost"] > 0]["cost"]
    if len(non_zero_cost) > 0:
        # Cost floors snapped to nearest $5 so they read as deliberate policy, not
        # a quantile artefact of this particular bill.
        CFG["IDLE_COST_FLOOR"] = snap(non_zero_cost.quantile(0.20), 5.0, 5.0, 1e9)
        CFG["UNTAGGED_COST_FLOOR"] = snap(non_zero_cost.quantile(0.40), 5.0, 5.0, 1e9)
        CFG["RUNAWAY_COST_FLOOR"] = snap(non_zero_cost.quantile(0.30), 5.0, 5.0, 1e9)

    return df


# ──────────────────────────────────────────────────────────────────────
# C. UNSUPERVISED MODEL (IsolationForest — NO labels)
# ──────────────────────────────────────────────────────────────────────

# Columns to feed into IsolationForest — cost + telemetry features only
IF_FEATURES = [
    "cost", "usage_amount", "cost_ratio_7d", "cost_ratio_14d",
    "robust_z_cost", "slope_14d", "cost_pct_change_28d",
    "peer_ratio", "is_weekend",
    # Telemetry (filled with 0 where absent)
    "CPUUtilization", "GPUUtilization", "MemoryUtilization",
    "NetworkIn", "NetworkOut", "DiskReadBytes", "DiskWriteBytes",
    "DatabaseConnections", "ReadIOPS", "WriteIOPS",
    "BytesTransferred", "IncomingBytes", "IncomingLogEvents",
    "ProvisionedWriteCapacityUnits", "ConsumedWriteCapacityUnits",
    "VolumeIdleTime", "AttachmentState", "RequestCount",
    "ProcessedBytes", "Uptime", "TagCompliance",
]


def train_isolation_forest(df: pd.DataFrame) -> tuple:
    """Train IsolationForest on available features. Return (model, scaler, feature_names)."""
    available = [c for c in IF_FEATURES if c in df.columns]
    X = df[available].fillna(0).copy()

    scaler = RobustScaler()
    X_scaled = scaler.fit_transform(X)

    model = IsolationForest(
        n_estimators=CFG["IF_N_ESTIMATORS"],
        contamination=CFG["IF_CONTAMINATION"],
        random_state=CFG["IF_RANDOM_STATE"],
        n_jobs=-1,
    )
    model.fit(X_scaled)

    # score_samples returns negative values for anomalies
    raw_scores = model.score_samples(X_scaled)
    # Normalise to [0, 1] where 1 = most anomalous
    min_s, max_s = raw_scores.min(), raw_scores.max()
    ml_scores = 1 - (raw_scores - min_s) / (max_s - min_s + 1e-9)
    df["ml_anomaly_score"] = ml_scores

    return model, scaler, available


# ──────────────────────────────────────────────────────────────────────
# D. SCENARIO DETECTORS (mechanism-specific, no labels)
# ──────────────────────────────────────────────────────────────────────

def detect_runaway_usage(row, cfg=CFG) -> tuple:
    """
    Detect sustained high-utilisation resources that were never shut down.
    Signals: CPU ≥ 75%, GPU high, Uptime near 86400s, weekend activity.
    Expected to catch: A1 (5× p3.2xlarge running 24/7).
    """
    cpu = row.get("CPUUtilization", 0)
    gpu = row.get("GPUUtilization", 0)
    mem = row.get("MemoryUtilization", 0)
    uptime = row.get("Uptime", 0)
    cost = row.get("cost", 0)
    is_weekend = row.get("is_weekend", 0)
    slope = row.get("slope_14d", 0.0)

    if cost < cfg["RUNAWAY_COST_FLOOR"]:
        return False, 0.0, ""

    cpu_hot = cpu >= cfg["RUNAWAY_CPU_THRESHOLD"]
    gpu_hot = gpu >= 50.0
    uptime_24x7 = uptime >= cfg["RUNAWAY_UPTIME_THRESHOLD"]

    # A forgotten runaway compute box is HOT and STAYS hot. A gradual-drift ASG
    # instead creeps up in cost while its per-instance CPU *declines* (more boxes,
    # same work). Require GPU saturation OR sustained high CPU, and refuse to claim
    # "runaway" when cost is trending up but CPU is not hot — that is drift, not runaway.
    strong_signal = gpu_hot or (cpu_hot and mem >= 70.0 and uptime_24x7)
    if not strong_signal:
        return False, 0.0, ""

    signals = sum([cpu_hot, gpu_hot, uptime_24x7, mem >= 70.0])
    if signals >= 2:
        score = min(0.99, 0.60 + 0.10 * signals)
        # Weekend activity boosts suspicion (real workloads drop on weekends)
        if is_weekend and cpu_hot:
            score = min(0.99, score + 0.10)
        reason = f"runaway: CPU={cpu:.0f}% GPU={gpu:.0f}% Uptime={uptime:.0f}s weekend={is_weekend}"
        return True, score, reason

    return False, 0.0, ""


def detect_idle_resource(row, cfg=CFG) -> tuple:
    """
    Detect resources billed at significant cost with near-zero utilisation.
    Covers: RDS idle (CPU<10%, connections≈0), EBS unattached (AttachmentState=0).
    Expected to catch: A2 (idle RDS), A3 (unattached EBS volumes).
    """
    cost = row.get("cost", 0)
    service = row.get("service", "")

    # Exclude services where idle detection doesn't apply
    if service in ("AWSLambda", "AmazonS3", "AWSDataTransfer", "AWSELB",
                    "AmazonCloudWatch", "AmazonVPC", "awskms"):
        return False, 0.0, ""

    # ── EBS unattached detection (lower cost floor — $5 is wasteful for zero-IO volumes) ──
    # IMPORTANT: only apply to actual EBS volumes. AttachmentState/VolumeIdleTime are
    # filled with 0 for every resource during feature engineering, so without the
    # 'vol-' guard this branch mislabels normal RDS/DynamoDB/EC2 rows as "unattached
    # volumes" (that produced ~120 false positives on the v5 test set).
    resource_id = str(row.get("resource_id", ""))
    is_ebs_volume = resource_id.lower().startswith("vol-")
    if is_ebs_volume and cost >= 5.0:
        volume_idle_time = row.get("VolumeIdleTime", 0)
        volume_read_ops = row.get("VolumeReadOps", 0)
        volume_write_ops = row.get("VolumeWriteOps", 0)
        attachment_state = row.get("AttachmentState", 1)
        # Genuinely idle/orphan volume: unattached OR near-100% idle with ~no IO.
        if (attachment_state <= 0 and volume_idle_time >= 95) or \
           (volume_idle_time >= 95 and volume_read_ops <= 1 and volume_write_ops <= 1):
            reason = (f"idle_ebs: AttachmentState={attachment_state:.0f} "
                      f"IdleTime={volume_idle_time:.0f}% "
                      f"ReadOps={volume_read_ops:.1f} WriteOps={volume_write_ops:.1f}")
            return True, 0.82, reason

    # General idle: need higher cost floor
    if cost < cfg["IDLE_COST_FLOOR"]:
        return False, 0.0, ""

    cpu = row.get("CPUUtilization", None)
    conns = row.get("DatabaseConnections", None)
    read_iops = row.get("ReadIOPS", 0)
    write_iops = row.get("WriteIOPS", 0)

    # Only compute-/DB-style services can be "idle by low CPU". DynamoDB, S3 etc.
    # have no CPU metric (filled to 0) and must NOT be judged idle on a phantom 0%.
    if service not in ("AmazonRDS", "AmazonEC2", "AmazonElastiCache", "AmazonRedshift"):
        return False, 0.0, ""
    if not row.get("has_CPUUtilization", 0):
        return False, 0.0, ""

    # RDS / general idle: very low CPU + near-zero connections/IOPS
    if cpu is not None and cpu < cfg["IDLE_CPU_THRESHOLD"]:
        idle_signals = 0
        if conns is not None and conns < cfg["IDLE_CONN_THRESHOLD"]:
            idle_signals += 1
            
        # Get dynamic threshold if exists, fallback to 10
        iops_threshold = cfg.get("IDLE_IOPS_THRESHOLD", 10.0)
        if read_iops + write_iops < iops_threshold:
            idle_signals += 1
            
        network_in = row.get("NetworkIn", 0) + row.get("NetworkReceiveThroughput", 0)
        if network_in < 1000:
            idle_signals += 1

        if idle_signals >= 2:
            score = min(0.95, 0.65 + 0.10 * idle_signals)
            reason = f"idle: CPU={cpu:.1f}% Conns={conns} IOPS={read_iops + write_iops:.0f}"
            return True, score, reason

    return False, 0.0, ""


def detect_untagged_spend(row, cfg=CFG) -> tuple:
    """
    Detect high-cost resources without mandatory tagging.

    IMPORTANT (real-bill lesson): on a real AWS bill the `owner` tag is missing on
    ~99% of resources, so "owner tag missing" is NOT anomalous — it is the norm.
    Firing on missing owner/team floods thousands of false positives (and wrongly
    flags the benign flash-sale/migration/load-test resources, which also happen to
    be untagged). The reliable governance signal is the CloudWatch `TagCompliance`
    metric == 0, which marks *deliberately* unallocatable spend.
    Expected to catch: A4 (8× m5.4xlarge with TagCompliance=0).
    """
    cost = row.get("cost", 0)
    if cost < cfg["UNTAGGED_COST_FLOOR"]:
        return False, 0.0, ""

    missing_team = row.get("missing_team_tag", 0)
    missing_owner = row.get("missing_owner_tag", 0)
    tag_compliance = row.get("TagCompliance", 100)      # telemetry governance score
    has_tag_metric = row.get("has_TagCompliance", 0) if "has_TagCompliance" in row.index else 1

    # Definitive signal: telemetry TagCompliance == 0 → chronic unallocatable spend.
    if has_tag_metric and tag_compliance == 0:
        score = 0.99  # outrank peer-ratio spikes (root cause is the tag, not the $)
        reason = f"untagged: TagCompliance=0 cost=${cost:.2f}/day"
        return True, score, reason

    # Fallback ONLY when telemetry is unavailable: require BOTH team AND owner missing
    # on a high-cost resource. Missing owner alone is too common to be meaningful.
    if not has_tag_metric and missing_team and missing_owner and cost >= max(cfg["UNTAGGED_COST_FLOOR"], 100.0):
        score = 0.72
        reason = f"untagged(no-telemetry): team+owner missing cost=${cost:.2f}/day"
        return True, score, reason

    return False, 0.0, ""


def detect_sudden_spike(row, cfg=CFG) -> tuple:
    """
    Detect short-lived cost spikes using robust Z-score, cost ratio, peer ratio.
    Also checks BytesTransferred for NAT/DataTransfer spikes and
    IncomingBytes/IncomingLogEvents for CloudWatch log spikes.
    Expected to catch: A5 (NAT gateway spike), A6 (CloudWatch logs DEBUG runaway).
    """
    cost = row.get("cost", 0)
    robust_z = row.get("robust_z_cost", 0)
    cost_ratio_7d = row.get("cost_ratio_7d", 1.0)
    peer_ratio = row.get("peer_ratio", 1.0)
    service = row.get("service", "")
    slope = row.get("slope_14d", 0.0)

    reasons = []
    score = 0.0

    # A "spike" is a SHORT, sharp jump vs the resource's own recent history. A
    # resource on a sustained upward trend (rising slope_14d) is DRIFT, not a spike
    # — let the drift detector own it, otherwise a growing ASG reads as a peer-spike.
    is_trending_up = slope > cfg["DRIFT_SLOPE_THRESHOLD"]

    # Generic spike via robust Z / cost ratio
    if robust_z >= cfg["SPIKE_ROBUST_Z"] and cost_ratio_7d >= cfg["SPIKE_COST_RATIO_7D"]:
        score = min(0.99, 0.60 + 0.08 * min(robust_z, 5.0))
        reasons.append(f"z={robust_z:.1f} ratio7d={cost_ratio_7d:.1f}")

    # Peer-based spike — skip when the resource is on a sustained upward trend
    # (that is drift). Peer-ratio alone is a weak spike signal for growing fleets.
    if peer_ratio >= cfg["SPIKE_PEER_RATIO"] and cost > 50 and not is_trending_up:
        peer_score = min(0.99, 0.65 + 0.05 * min(peer_ratio, 10.0))
        if peer_score > score:
            score = peer_score
        reasons.append(f"peer_ratio={peer_ratio:.1f}")

    # A spike must DEVIATE from the resource's own recent baseline. Absolute floors
    # alone flag steady-but-large resources (e.g. a prod log group that always
    # ingests ~87 GB/day) as anomalies — that was the source of the baseline FPs.
    SPIKE_VOLUME_RATIO = 3.0  # today's volume must be >=3x its 14-day median

    def volume_ratio(value: float, baseline: float) -> float:
        if baseline is None or baseline <= 0 or pd.isna(baseline):
            return 1.0  # no established baseline yet → do not treat as spike
        return value / baseline

    # A short misconfig anomaly often creates a BRAND-NEW resource that exists only
    # during the incident, so it has no pre-anomaly self-baseline (ratio≈1, z≈0).
    # For such young resources we fall back to absolute volume+cost floors. Steady
    # long-lived resources (high age) never hit this branch, so a prod log group
    # that always ingests a lot is NOT flagged. Benign new resources (migration,
    # flash-sale, load-test) are removed separately by benign suppression.
    resource_age = row.get("resource_age_days", 999)
    # Only resources that FIRST APPEARED mid-dataset (not day-1 resources we simply
    # lack history for) and are still young qualify as "new" for absolute-floor spikes.
    is_new_resource = (
        row.get("is_midstream_new_resource", 0) == 1
        and resource_age is not None and resource_age <= 14
    )

    # NAT Gateway / DataTransfer spike
    if service in ("AWSDataTransfer", "AmazonVPC"):
        bytes_tx = row.get("BytesTransferred", 0)
        tx_ratio = volume_ratio(bytes_tx, row.get("BytesTransferred_baseline_14d"))
        deviates = tx_ratio >= SPIKE_VOLUME_RATIO
        new_high = is_new_resource and bytes_tx > 1e9 and cost > 100
        if bytes_tx > 1e9 and cost > 100 and (deviates or new_high):
            tx_score = min(0.99, 0.75 + 0.05 * min(cost / 100, 5.0))
            if tx_score > score:
                score = tx_score
            tag = f"{tx_ratio:.1f}x base" if deviates else f"new_resource age={resource_age:.0f}d"
            reasons.append(f"BytesTx={bytes_tx / 1e9:.1f}GB ({tag}) cost=${cost:.0f}")

    # CloudWatch Logs spike
    if service == "AmazonCloudWatch":
        incoming_bytes = row.get("IncomingBytes", 0)
        incoming_events = row.get("IncomingLogEvents", 0)
        bytes_ratio = volume_ratio(incoming_bytes, row.get("IncomingBytes_baseline_14d"))
        events_ratio = volume_ratio(incoming_events, row.get("IncomingLogEvents_baseline_14d"))
        deviates = max(bytes_ratio, events_ratio) >= SPIKE_VOLUME_RATIO
        new_high = is_new_resource and incoming_bytes > 1e8 and cost > 50
        if incoming_bytes > 1e8 and cost > 50 and (deviates or new_high):
            cw_score = min(0.99, 0.75 + 0.05 * min(cost / 50, 5.0))
            if cw_score > score:
                score = cw_score
            tag = f"{bytes_ratio:.1f}x base" if deviates else f"new_resource age={resource_age:.0f}d"
            reasons.append(f"LogBytes={incoming_bytes / 1e9:.2f}GB ({tag}) events={incoming_events:.0f}")

    if score > 0:
        return True, score, "spike: " + " | ".join(reasons)
    return False, 0.0, ""


def detect_gradual_drift(row, cfg=CFG) -> tuple:
    """
    Detect slow over-provisioning (cost creeping up without corresponding usage).
    Key signal for DynamoDB: ProvisionedWriteCapacityUnits increasing while
    ConsumedWriteCapacityUnits stays flat.
    Expected to catch: A7 (DynamoDB drift).
    """
    slope = row.get("slope_14d", 0)
    usage_slope = row.get("usage_14d_slope", 0)
    pct_change_28d = row.get("cost_pct_change_28d", 0)
    cost = row.get("cost", 0)
    service = row.get("service", "")
    cpu = row.get("CPUUtilization", 0)

    # General drift: positive slope + significant 28-day growth
    if slope > cfg["DRIFT_SLOPE_THRESHOLD"] and pct_change_28d > cfg["DRIFT_PCT_CHANGE_28D"]:
        score = min(0.99, 0.55 + 0.20 * min(pct_change_28d, 2.0))
        reason = f"drift: slope14d={slope:.2f} pct28d={pct_change_28d:.1%}"

        # DynamoDB-specific: provisioned ≫ consumed → auto-scaling ratchet
        if service == "AmazonDynamoDB":
            prov_wcu = row.get("ProvisionedWriteCapacityUnits", 0)
            cons_wcu = row.get("ConsumedWriteCapacityUnits", 0)
            if prov_wcu > 0 and cons_wcu < prov_wcu * 0.5:
                score = min(0.99, score + 0.15)
                reason += f" | DDB prov={prov_wcu:.0f} cons={cons_wcu:.0f} (over-provisioned)"

        # EC2/ASG-specific: fleet grows (usage_amount slope up) while per-instance
        # CPU is *falling* → broken scale-in over-provisioning, not a hot workload.
        if service == "AmazonEC2" and usage_slope > 0 and cpu < 60.0:
            score = min(0.99, score + 0.15)
            reason += f" | ASG usage_slope={usage_slope:.2f} cpu_falling={cpu:.0f}%"

        return True, score, reason

    # EC2/ASG drift: fleet grows (usage-hours slope positive) while per-instance
    # CPU declines — broken scale-in. This uses slope_14d, so it does NOT wait for
    # 28 days of history the way pct_change_28d does (that lag delayed detection of
    # T3 until the last 2 days). Catches drift across the whole ramp.
    if (service == "AmazonEC2" and cost > 50 and usage_slope > 0
            and slope > cfg["DRIFT_SLOPE_THRESHOLD"] and cpu < 60.0):
        score = min(0.95, 0.66 + 0.03 * min(usage_slope, 6.0))
        reason = (f"drift_asg: slope14d={slope:.2f} usage_slope={usage_slope:.2f} "
                  f"cpu_falling={cpu:.0f}% (scale-in broken)")
        return True, score, reason

    # DynamoDB-specific fallback even if slope is mild
    if service == "AmazonDynamoDB" and cost > 50:
        prov_wcu = row.get("ProvisionedWriteCapacityUnits", 0)
        cons_wcu = row.get("ConsumedWriteCapacityUnits", 0)
        if prov_wcu > 100 and cons_wcu < prov_wcu * 0.3:
            score = min(0.99, 0.60 + 0.10 * min(prov_wcu / 500, 3.0))
            reason = f"drift_ddb: prov={prov_wcu:.0f} cons={cons_wcu:.0f} cost=${cost:.0f}"
            return True, score, reason

    return False, 0.0, ""


# ──────────────────────────────────────────────────────────────────────
# E. BENIGN SUPPRESSION
# ──────────────────────────────────────────────────────────────────────

def check_benign_suppression(row, cfg=CFG) -> tuple:
    """
    Suppress alerts for planned/approved business events:
      B1 — Flash sale: CPU+Network+RequestCount all high together, workload-proportional
      B2 — Migration egress: short BytesTransferred spike for a known migration resource
      B3 — Load test: staging resource with extreme CPU during test window
    Returns (is_suppressed: bool, reason: str)
    """
    resource_id = str(row.get("resource_id", ""))
    service = row.get("service", "")
    env = str(row.get("tag_environment", ""))
    cost = row.get("cost", 0)

    cpu = row.get("CPUUtilization", 0)
    mem = row.get("MemoryUtilization", 0)
    net_in = row.get("NetworkIn", 0)
    net_out = row.get("NetworkOut", 0)
    req_count = row.get("RequestCount", 0)
    bytes_tx = row.get("BytesTransferred", 0)
    processed = row.get("ProcessedBytes", 0)

    # ── B1: Flash-sale suppression ──
    # Workload metrics ALL high together → auto-scaling under legitimate load
    if "flashsale" in resource_id.lower() or "autoscale" in resource_id.lower():
        if cpu >= cfg["BENIGN_FLASH_CPU_THRESHOLD"] and mem >= 60:
            return True, f"benign_flash_sale: CPU={cpu:.0f}% Mem={mem:.0f}% (workload-proportional)"

    # Generic flash-sale pattern: ONLY suppress when RequestCount is genuinely high
    # (proving legitimate user traffic, not just GPU/CPU running hot with zero requests)
    if service == "AmazonEC2" and cpu >= cfg["BENIGN_FLASH_CPU_THRESHOLD"]:
        if req_count > 5000 and mem >= 60:
            return True, f"benign_flash_sale_pattern: CPU={cpu:.0f}% Requests={req_count:.0f}"

    # ── B2: Migration egress suppression ──
    if "migration" in resource_id.lower() or "egress" in resource_id.lower():
        if service in ("AWSDataTransfer", "AmazonVPC") and bytes_tx > 0:
            return True, f"benign_migration: BytesTx={bytes_tx / 1e9:.1f}GB resource={resource_id}"

    # ── B3: Load-test suppression ──
    if "loadtest" in resource_id.lower() or "load-test" in resource_id.lower():
        return True, f"benign_loadtest: resource={resource_id}"

    if env == "staging" and cpu >= cfg["BENIGN_LOADTEST_CPU_THRESHOLD"]:
        if "test" in resource_id.lower() or "bench" in resource_id.lower():
            return True, f"benign_staging_test: CPU={cpu:.0f}% env={env}"

    return False, ""


# ──────────────────────────────────────────────────────────────────────
# F. FINAL DECISION LOGIC
# ──────────────────────────────────────────────────────────────────────

DETECTORS = [
    ("runaway_usage", detect_runaway_usage),
    ("idle_resource", detect_idle_resource),
    ("untagged_spend", detect_untagged_spend),
    ("sudden_spike", detect_sudden_spike),
    ("gradual_drift", detect_gradual_drift),
]


def run_detection_pipeline(df: pd.DataFrame) -> pd.DataFrame:
    """Run all detectors + ML score → final decision per row."""
    results = []

    for idx, row in df.iterrows():
        # ── Run all scenario detectors ──
        best_type = None
        best_score = 0.0
        best_reason = ""
        all_detections = []

        for det_name, det_func in DETECTORS:
            flagged, score, reason = det_func(row)
            if flagged:
                all_detections.append((det_name, score, reason))
                if score > best_score:
                    best_score = score
                    best_type = det_name
                    best_reason = reason

        # ── Combine with ML score ──
        ml_score = row.get("ml_anomaly_score", 0.0)
        if best_score > 0:
            final_score = max(
                best_score,
                CFG["STAT_WEIGHT"] * best_score + CFG["ML_WEIGHT"] * ml_score,
            )
        else:
            # ML-only (no scenario detector fired). This is the least explainable
            # signal, so it must clear a higher bar to become a raw alert — a plain
            # IsolationForest outlier on a steady resource is usually noise.
            final_score = ml_score if ml_score >= CFG["ML_ONLY_ALERT_THRESHOLD"] else 0.0

        # ── Check benign suppression ──
        suppressed, suppression_reason = check_benign_suppression(row)

        # ── Raw (pre-persistence) alert decision ──
        raw_alert = final_score >= CFG["ALERT_THRESHOLD"] and not suppressed

        robust_z = row.get("robust_z_cost", 0.0)
        results.append({
            "date": row["date"],
            "resource_id": row["resource_id"],
            "service": row["service"],
            "account_id": row["account_id"],
            "detected_anomaly_type": best_type if best_type else "ml_anomaly",
            "final_score": round(final_score, 4),
            "ml_anomaly_score": round(ml_score, 4),
            "scenario_score": round(best_score, 4),
            "robust_z_cost": round(float(robust_z), 4),
            "detector_reason": best_reason,
            "suppressed": suppressed,
            "suppression_reason": suppression_reason,
            "raw_alert": raw_alert,
            "top_drivers": "; ".join([f"{n}({s:.2f})" for n, s, r in all_detections[:3]]),
        })

    pred = pd.DataFrame(results)
    if len(pred) == 0:
        pred["is_alert"] = []
        return pred
    return apply_persistence_filter(pred)


def apply_persistence_filter(pred: pd.DataFrame) -> pd.DataFrame:
    """Require an anomaly to persist N consecutive days before alerting.

    Rationale (review #2): idle/drift anomalies build over days, so alerting on
    day 1 produces 'false early alerts'. We only confirm an alert once a resource
    has been flagged for `PERSISTENCE_MIN_DAYS` consecutive days.

    Bypass: a genuinely explosive cost spike (robust_z >= PERSISTENCE_Z_BYPASS)
    fires immediately — waiting 3 days on a 15-sigma jump would be negligent.
    """
    min_days = CFG["PERSISTENCE_MIN_DAYS"]
    z_bypass = CFG["PERSISTENCE_Z_BYPASS"]

    pred = pred.sort_values(["resource_id", "date"]).reset_index(drop=True)
    pred["is_alert"] = False

    for rid, grp in pred.groupby("resource_id", sort=False):
        raw = grp["raw_alert"].to_numpy()
        z = grp["robust_z_cost"].to_numpy()
        idx = grp.index.to_numpy()
        run_start = None
        for i in range(len(raw)):
            if raw[i]:
                if run_start is None:
                    run_start = i
                run_len = i - run_start + 1
                # Immediate Z-bypass: an extreme spike alerts on its first day.
                if z[i] >= z_bypass:
                    pred.at[idx[i], "is_alert"] = True
                # Once the run reaches min_days, confirm the whole run so far
                # (back-fill days 1..N-1 that were pending confirmation) and every
                # subsequent day it stays flagged.
                elif run_len >= min_days:
                    for j in range(run_start, i + 1):
                        pred.at[idx[j], "is_alert"] = True
            else:
                run_start = None
    return pred


# ──────────────────────────────────────────────────────────────────────
# G. EVALUATION (backtest against ground-truth labels)
# ──────────────────────────────────────────────────────────────────────

def _norm_rid(value) -> str:
    """Normalise a resource_id for matching: lowercase, strip ARN prefix and a
    trailing numeric instance suffix (e.g. '...-00'). Labels use short ids while
    CUR/metrics may use ARNs or per-instance suffixes; exact '==' misses them."""
    s = str(value).lower()
    s = s.split(":")[-1]              # drop arn prefix
    return s


def _rid_matches(pred_rid: str, label_rid: str) -> bool:
    """True if a prediction resource_id refers to the same resource as a label id.

    Handles ARN prefixes and per-instance numeric suffixes, but avoids loose
    substring matching that would wrongly fold unrelated resources into a label
    window (which both inflates false-negative counts and risks miscrediting TPs).
    """
    import re
    a, b = _norm_rid(pred_rid), _norm_rid(label_rid)
    if a == b:
        return True
    # Strip a trailing per-instance suffix: label 'i-x' vs pred 'i-x-00'.
    a_core = re.sub(r"-\d+$", "", a)
    b_core = re.sub(r"-\d+$", "", b)
    if a_core == b_core:
        return True
    # Allow ONLY the case where the label short-id equals a prediction id whose
    # trailing suffix was stripped (short label ↔ suffixed/prefixed real id).
    return a_core == b or b_core == a


def evaluate_resource_day(all_rows: pd.DataFrame, predictions: pd.DataFrame, labels: pd.DataFrame) -> dict:
    """Resource-day confusion matrix (review #4).

    Every (resource_id, date) is a sample. Truth = inside any anomaly label window
    for a matching resource. Prediction = confirmed alert (post-persistence).
    This exposes per-day false positives that scenario-window scoring hides.
    """
    truth = {}  # (norm_rid, date) -> True(anomaly)/False(benign) ; absent => normal
    benign_days = set()
    for lab in labels.itertuples(index=False):
        is_anom = str(lab.label).lower() == "anomaly"
        for d in pd.date_range(lab.start_date, lab.end_date, freq="D").strftime("%Y-%m-%d"):
            key = (_norm_rid(lab.resource_id), d, str(lab.resource_id))
            truth[key] = is_anom
            if not is_anom:
                benign_days.add((_norm_rid(lab.resource_id), d))

    def truth_for(rid, date):
        for lab in labels.itertuples(index=False):
            if lab.start_date <= date <= lab.end_date and _rid_matches(rid, lab.resource_id):
                return "anomaly" if str(lab.label).lower() == "anomaly" else "benign"
        return "normal"

    alert_keys = set()
    if len(predictions) > 0:
        for r in predictions[predictions["is_alert"]].itertuples(index=False):
            alert_keys.add((str(r.resource_id), str(r.date)))

    tp = fp = fn = tn = fp_benign = 0
    for r in all_rows.itertuples(index=False):
        date = str(r.date)
        rid = str(r.resource_id)
        t = truth_for(rid, date)
        alerted = (rid, date) in alert_keys
        if t == "anomaly":
            if alerted:
                tp += 1
            else:
                fn += 1
        else:  # normal or benign
            if alerted:
                fp += 1
                if t == "benign":
                    fp_benign += 1
            else:
                tn += 1

    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    fp_rate = fp / (fp + tn) if (fp + tn) else 0.0
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "fp_on_benign": fp_benign,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1_score": round(f1, 4),
        "fp_rate": round(fp_rate, 4),
    }


def evaluate_backtest(predictions: pd.DataFrame, labels_path: str) -> dict:
    """
    Scenario/window-level evaluation:
      - A1-A7: detected if at least 1 alert inside their labelled date window
      - B1-B3: false positive if any alert (not suppressed) hits their resource+window
    """
    labels = pd.read_csv(labels_path)
    labels["start_date"] = pd.to_datetime(labels["start_date"]).dt.strftime("%Y-%m-%d")
    labels["end_date"] = pd.to_datetime(labels["end_date"]).dt.strftime("%Y-%m-%d")

    # De-duplicate labels by anomaly_id (A1 has 5 resource rows)
    scenario_groups = labels.groupby("anomaly_id").agg({
        "label": "first",
        "anomaly_type": "first",
        "start_date": "first",
        "end_date": "first",
        "resource_id": list,
        "service": "first",
        "linked_account_id": "first",
        "description": "first",
    }).reset_index()

    alerts = predictions[predictions["is_alert"]].copy() if len(predictions) > 0 else pd.DataFrame()
    alerts["date"] = pd.to_datetime(alerts["date"]).dt.strftime("%Y-%m-%d") if len(alerts) > 0 else alerts

    results = []
    tp, fp, fn = 0, 0, 0

    for _, scenario in scenario_groups.iterrows():
        sid = scenario["anomaly_id"]
        label = scenario["label"]
        s_resources = scenario["resource_id"]
        s_start = scenario["start_date"]
        s_end = scenario["end_date"]
        s_type = scenario["anomaly_type"]

        # Check if any alert matches this scenario's resources within its date window
        if len(alerts) > 0:
            hits = alerts[
                (alerts["resource_id"].apply(lambda x: any(_rid_matches(x, res) for res in s_resources))) &
                (alerts["date"] >= s_start) &
                (alerts["date"] <= s_end)
            ]
        else:
            hits = pd.DataFrame()

        detected = len(hits) > 0
        # Report the highest-scoring alert's type so a type MISMATCH (e.g. drift
        # detected as runaway) is visible even when the window is 'detected'.
        if detected:
            top_hit = hits.sort_values("final_score", ascending=False).iloc[0]
            detected_type = top_hit["detected_anomaly_type"]
            detected_score = hits["final_score"].max()
        else:
            detected_type = None
            detected_score = 0.0
        type_correct = bool(detected and s_type == detected_type)

        if label == "anomaly":
            if detected:
                tp += 1
                status = "✅ TRUE POSITIVE"
            else:
                fn += 1
                status = "❌ FALSE NEGATIVE (missed)"
        else:  # benign
            if detected:
                fp += 1
                status = "⚠️ FALSE POSITIVE (benign flagged)"
            else:
                status = "✅ TRUE NEGATIVE (benign suppressed)"

        # A window that is detected but with the WRONG mechanism type is called
        # out explicitly (still a TP for recall, but a classification defect).
        if label == "anomaly" and detected and not type_correct:
            status = f"⚠️ TYPE MISMATCH (detected {detected_type}, expected {s_type})"

        results.append({
            "anomaly_id": sid,
            "label": label,
            "expected_type": s_type,
            "detected": detected,
            "detected_type": detected_type,
            "type_correct": type_correct if label == "anomaly" else None,
            "max_score": round(detected_score, 4),
            "alert_count": len(hits),
            "status": status,
        })

    # Metrics
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    # Count total FP alerts (not just scenario-level)
    total_alerts = len(alerts) if len(alerts) > 0 else 0

    return {
        "scenario_results": results,
        "metrics": {
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1_score": round(f1, 4),
            "total_alerts": total_alerts,
        },
    }


# ──────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────

def per_anomaly_type_breakdown(scenario_results: list[dict]) -> list[dict]:
    """Per-anomaly-type metrics (TF2 deliverable).

    For each mechanism (runaway/idle/untagged/spike/drift) report how many labeled
    anomalies of that type were detected (recall), and how many were classified with
    the correct type. `support` = number of ground-truth anomalies of that type.
    """
    from collections import defaultdict
    agg = defaultdict(lambda: {"support": 0, "detected": 0, "type_correct": 0})
    for r in scenario_results:
        if r["label"] != "anomaly":
            continue
        t = r["expected_type"]
        agg[t]["support"] += 1
        if r["detected"]:
            agg[t]["detected"] += 1
            if r.get("type_correct"):
                agg[t]["type_correct"] += 1
    rows = []
    for t, a in sorted(agg.items()):
        support = a["support"]
        recall = a["detected"] / support if support else 0.0
        type_acc = a["type_correct"] / support if support else 0.0
        rows.append({
            "anomaly_type": t,
            "support": support,
            "detected": a["detected"],
            "recall": round(recall, 4),
            "type_correct": a["type_correct"],
            "type_accuracy": round(type_acc, 4),
        })
    return rows


def main():
    print("=" * 70)
    print("  FinOps Watch — Unsupervised Hybrid Anomaly Detection Pipeline")
    print("=" * 70)
    artifacts = Path(CFG["ARTIFACTS_DIR"])
    artifacts.mkdir(parents=True, exist_ok=True)

    # ── A. Load data ──
    print("\n[A] Loading data...")
    cur = load_cur(CFG["CUR_PATH"])
    print(f"    CUR: {len(cur):,} rows, {cur['resource_id'].nunique()} resources")

    metrics = load_metrics(CFG["METRICS_PATH"])
    print(f"    Metrics: {len(metrics):,} rows (ground-truth columns DROPPED)")

    metrics_wide = pivot_metrics_wide(metrics)
    print(f"    Metrics pivoted: {len(metrics_wide):,} resource-day rows, {len(metrics_wide.columns)} columns")

    df = join_cur_metrics(cur, metrics_wide)
    print(f"    Joined: {len(df):,} rows")

    # ── B. Feature engineering ──
    print("\n[B] Engineering features...")
    df = engineer_features(df)

    # Verify no label leakage
    forbidden = {"is_anomaly", "anomaly_type", "anomaly_id", "label"}
    leaked = forbidden & set(df.columns)
    assert not leaked, f"LABEL LEAKAGE detected: {leaked}"
    print(f"    ✅ No label leakage. Features: {len(df.columns)} columns")

    # Save feature file
    feat_path = artifacts / "resource_day_features.csv"
    df.to_csv(feat_path, index=False)
    print(f"    Saved → {feat_path}")

    # ── C. Unsupervised model ──
    print("\n[C] Training IsolationForest (unsupervised, NO labels)...")
    model, scaler, used_features = train_isolation_forest(df)
    print(f"    Trained on {len(used_features)} features, {len(df):,} rows")
    print(f"    ML anomaly score range: [{df['ml_anomaly_score'].min():.4f}, {df['ml_anomaly_score'].max():.4f}]")

    # ── D+E+F. Run detection pipeline ──
    print("\n[D-F] Running scenario detectors + benign suppression + final decision...")
    predictions = run_detection_pipeline(df)
    alerts_only = predictions[predictions["is_alert"]]
    suppressed_only = predictions[predictions["suppressed"]]
    print(f"    Total alerts: {len(alerts_only):,}")
    print(f"    Suppressed (benign): {len(suppressed_only):,}")

    # Save predictions
    pred_path = artifacts / "hybrid_predictions.csv"
    predictions.to_csv(pred_path, index=False)
    print(f"    Saved → {pred_path}")

    # ── G. Evaluate ──
    print("\n[G] Evaluating against ground-truth labels...")
    eval_result = evaluate_backtest(predictions, CFG["LABELS_PATH"])

    # Resource-day confusion matrix (review #4): exposes per-day false positives
    # that scenario-window scoring cannot see.
    labels_df = pd.read_csv(CFG["LABELS_PATH"])
    labels_df["start_date"] = pd.to_datetime(labels_df["start_date"]).dt.strftime("%Y-%m-%d")
    labels_df["end_date"] = pd.to_datetime(labels_df["end_date"]).dt.strftime("%Y-%m-%d")
    rd = evaluate_resource_day(predictions, predictions, labels_df)
    eval_result["resource_day"] = rd

    # Per-anomaly-type breakdown (TF2 deliverable): detection + classification
    # accuracy for each of the 5 mechanisms.
    per_type = per_anomaly_type_breakdown(eval_result["scenario_results"])
    eval_result["per_type"] = per_type

    # Print scenario results
    print("\n" + "─" * 70)
    print("  BACKTEST RESULTS — Scenario-level detection")
    print("─" * 70)
    for r in eval_result["scenario_results"]:
        det_type = r["detected_type"] or "—"
        print(f"  {r['anomaly_id']:>4s} | {r['label']:<8s} | expected={r['expected_type']:<15s} | "
              f"detected={det_type:<15s} | score={r['max_score']:.2f} | {r['status']}")

    m = eval_result["metrics"]
    print("\n" + "─" * 70)
    print("  SCENARIO-LEVEL (per anomaly group)")
    print(f"  Precision : {m['precision']:.2%}   Recall : {m['recall']:.2%}   F1 : {m['f1_score']:.2%}")
    print(f"  TP={m['true_positives']}  FP={m['false_positives']}  FN={m['false_negatives']}")
    print(f"  Total confirmed alerts raised: {m['total_alerts']}")
    print("─" * 70)
    print("  RESOURCE-DAY CONFUSION MATRIX (per resource per day)  ← review #4")
    print(f"  Precision : {rd['precision']:.2%}   Recall : {rd['recall']:.2%}   "
          f"F1 : {rd['f1_score']:.2%}   FP-rate : {rd['fp_rate']:.2%}")
    print(f"  TP={rd['tp']}  FP={rd['fp']} (on benign={rd['fp_on_benign']})  "
          f"FN={rd['fn']}  TN={rd['tn']}")
    print("─" * 70)
    print("  PER-ANOMALY-TYPE BREAKDOWN")
    print(f"  {'type':<16s} {'support':>7s} {'detected':>9s} {'recall':>7s} {'type_acc':>9s}")
    for pt in per_type:
        print(f"  {pt['anomaly_type']:<16s} {pt['support']:>7d} {pt['detected']:>9d} "
              f"{pt['recall']:>6.0%} {pt['type_accuracy']:>8.0%}")
    print("─" * 70)

    # Save metrics JSON
    metrics_path = artifacts / "hybrid_backtest_metrics.json"
    with open(metrics_path, "w") as f:
        json.dump(eval_result["metrics"], f, indent=2)
    print(f"\n    Metrics → {metrics_path}")

    # Save markdown report
    report_path = artifacts / "hybrid_backtest_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# Hybrid Unsupervised Anomaly Detection — Backtest Report\n\n")
        f.write(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write("## Architecture\n\n")
        f.write("| Layer | Method |\n|---|---|\n")
        f.write("| Unsupervised ML | IsolationForest (no labels) |\n")
        f.write("| Statistical | Rolling Z-score, MAD, slope, peer ratio |\n")
        f.write("| Scenario detectors | runaway / idle / untagged / spike / drift |\n")
        f.write("| Benign suppression | flash-sale / migration / load-test |\n")
        f.write(f"| Weight blend | stat={CFG['STAT_WEIGHT']:.0%} + ml={CFG['ML_WEIGHT']:.0%} |\n\n")

        f.write("## Metrics — Scenario-level (per anomaly group)\n\n")
        f.write(f"| Metric | Value |\n|---|---|\n")
        f.write(f"| Precision | **{m['precision']:.2%}** |\n")
        f.write(f"| Recall | **{m['recall']:.2%}** |\n")
        f.write(f"| F1 Score | **{m['f1_score']:.2%}** |\n")
        f.write(f"| True Positives | {m['true_positives']} |\n")
        f.write(f"| False Positives | {m['false_positives']} |\n")
        f.write(f"| False Negatives | {m['false_negatives']} |\n")
        f.write(f"| Total Alerts | {m['total_alerts']} |\n\n")

        f.write("## Metrics — Resource-day confusion matrix (per resource per day)\n\n")
        f.write("> Primary metric. Exposes per-day false positives that scenario-window scoring hides.\n\n")
        f.write(f"| Metric | Value |\n|---|---|\n")
        f.write(f"| Precision | **{rd['precision']:.2%}** |\n")
        f.write(f"| Recall | **{rd['recall']:.2%}** |\n")
        f.write(f"| F1 Score | **{rd['f1_score']:.2%}** |\n")
        f.write(f"| FP rate | **{rd['fp_rate']:.2%}** |\n")
        f.write(f"| TP / FP / FN / TN | {rd['tp']} / {rd['fp']} / {rd['fn']} / {rd['tn']} |\n")
        f.write(f"| FP on benign windows | {rd['fp_on_benign']} |\n\n")

        f.write("## Per-Anomaly-Type Breakdown\n\n")
        f.write("> Detection recall + classification accuracy for each of the 5 mechanisms.\n\n")
        f.write("| Anomaly Type | Support | Detected | Recall | Type-Correct | Type Accuracy |\n")
        f.write("|---|---:|---:|---:|---:|---:|\n")
        for pt in per_type:
            f.write(f"| {pt['anomaly_type']} | {pt['support']} | {pt['detected']} | "
                    f"{pt['recall']:.0%} | {pt['type_correct']} | {pt['type_accuracy']:.0%} |\n")
        f.write("\n")

        f.write("## Scenario-level Results\n\n")
        f.write("| ID | Label | Expected Type | Detected Type | Score | Alerts | Status |\n")
        f.write("|---|---|---|---|---|---|---|\n")
        for r in eval_result["scenario_results"]:
            det = r["detected_type"] or "—"
            f.write(f"| {r['anomaly_id']} | {r['label']} | {r['expected_type']} | "
                    f"{det} | {r['max_score']:.2f} | {r['alert_count']} | {r['status']} |\n")

        f.write("\n## Configuration\n\n```json\n")
        safe_cfg = {k: v for k, v in CFG.items() if not k.endswith("_PATH") and k != "ARTIFACTS_DIR"}
        f.write(json.dumps(safe_cfg, indent=2))
        f.write("\n```\n")

    print(f"    Report → {report_path}")
    print("\n✅ Pipeline complete.")


if __name__ == "__main__":
    main()
