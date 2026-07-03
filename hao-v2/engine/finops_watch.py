"""
finops_watch.py — FinOps Watch Hybrid Anomaly Detection Module
==============================================================
Unsupervised, identity-free AWS cost anomaly detection.

Two detection grains:
  PANEL  (account × service × day)  → sudden_spike, gradual_drift
  RESOURCE (resource × day)         → runaway_usage, idle_resource, untagged_spend

Identity rule: account_id / resource_id / service_name NEVER enter any ML model.
Only behavioural dynamics + continuous frequency priors are used.

Compatible with both train data (data/) and test data (hao-v2/data-test/).
"""

from __future__ import annotations
import warnings
warnings.filterwarnings("ignore")

import os
import numpy as np
import pandas as pd
from pathlib import Path
from statsmodels.tsa.seasonal import STL
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import MinMaxScaler

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS  (all thresholds live here — no magic numbers in logic)
# ─────────────────────────────────────────────────────────────────────────────
CONSTANTS = dict(
    # Panel-level spike
    SPIKE_RATIO            = 1.6,    # spike_ratio = today / pre_baseline >= this
    SPIKE_MIN_COST         = 50.0,   # $/day minimum absolute cost to consider
    SPIKE_SUSTAINED_DAYS   = 5,      # consecutive days rule fires → suppresses B1/B2/B3 (2-4d)
    ROBUST_Z_HARD          = 3.5,    # |robust_z| above this → statistical flag
    PANEL_MIN_HISTORY      = 14,     # warm-up days before cold-start guard lifts

    # Panel-level drift
    DRIFT_RATIO_THRESH     = 1.15,   # mean_7d / mean_28d above this
    DRIFT_SUSTAINED_DAYS   = 14,     # consecutive days drift fires

    # Resource-level runaway
    RUNAWAY_WEEKEND_RATIO  = 0.92,   # weekend/weekday cost ratio (healthy ≈ 0.7–0.85)
    RUNAWAY_MIN_COST       = 50.0,   # $/day
    RUNAWAY_MIN_DAYS       = 3,      # consecutive days
    RUNAWAY_NEW_RESOURCE_DAYS = 7,   # resource_age_days <= this = "new"

    # Resource-level idle
    IDLE_MIN_COST          = 5.0,    # $/day (resource costs something)
    IDLE_CPU_PCT           = 5.0,    # CPUUtilization < this → idle signal
    IDLE_DB_CONN           = 2.0,    # DatabaseConnections < this → idle signal
    IDLE_VOL_IDLE          = 80.0,   # VolumeIdleTime > this → idle signal
    IDLE_GPU_PCT           = 5.0,    # GPUUtilization < this → idle signal
    IDLE_SUSTAINED_DAYS    = 14,     # consecutive idle days to fire alert

    # Resource-level untagged
    UNTAGGED_MIN_COST      = 30.0,   # $/day minimum
    UNTAGGED_MIN_DAYS      = 3,      # consecutive days (chronic, not one-off)

    # IsolationForest
    IF_CONTAMINATION_SPIKE = 0.02,
    IF_CONTAMINATION_DRIFT = 0.03,
    IF_RANDOM_STATE        = 42,

    # Score fusion
    IF_PROMOTE_THRESHOLD   = 0.65,   # IF score >= this promotes MEDIUM → HIGH
    PERSISTENCE_N          = 2,      # consecutive soft-alert days → alert fires
)

# Compute service code(s) considered "compute" for runaway detection.
# Exposed as module-level var so the generalization test can override it.
EC2_CODE = "AmazonEC2"


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1 — Data Ingestion & Validation
# ─────────────────────────────────────────────────────────────────────────────

def load_sources(data_dir: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load CE daily, CUR line items, and metrics from *data_dir*.

    Returns
    -------
    ce      : Cost Explorer daily aggregate
    cur     : CUR line items (resource level)
    metrics : CloudWatch-style operational metrics (pivoted later)
    """
    p = Path(data_dir)

    ce = pd.read_csv(p / "cost_explorer_daily.csv",
                     dtype={"linked_account_id": str})
    ce["date"] = pd.to_datetime(ce["date"], utc=False).dt.tz_localize(None).dt.normalize()
    ce["unblended_cost"] = pd.to_numeric(ce["unblended_cost"], errors="coerce").fillna(0.0)
    ce["is_estimated"] = ce["is_estimated"].astype(str).str.lower() == "true"

    cur = pd.read_csv(p / "cur_line_items.csv",
                      dtype={"line_item_usage_account_id": str})
    cur["line_item_usage_start_date"] = (
        pd.to_datetime(cur["line_item_usage_start_date"], utc=True)
        .dt.tz_localize(None).dt.normalize()
    )
    cur["line_item_unblended_cost"] = pd.to_numeric(
        cur["line_item_unblended_cost"], errors="coerce").fillna(0.0)

    metrics = pd.read_csv(p / "metrics.csv",
                          dtype={"account_id": str})
    metrics["timestamp"] = pd.to_datetime(metrics["timestamp"], utc=True).dt.tz_localize(None)
    metrics["metric_value"] = pd.to_numeric(metrics["metric_value"], errors="coerce")

    # basic schema assertions
    assert {"linked_account_name", "service_code", "date", "unblended_cost"} <= set(ce.columns)
    assert {"line_item_resource_id", "line_item_unblended_cost"} <= set(cur.columns)

    return ce, cur, metrics


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2 — Feature Engineering: CE Panel Level
# ─────────────────────────────────────────────────────────────────────────────

def build_panel(ce: pd.DataFrame) -> pd.DataFrame:
    """Create a zero-filled calendar panel: every (account, service, day) tuple.

    Filling gaps with 0 ensures rolling windows don't silently skip days.
    estimated rows are kept but flagged; the rule/model layers respect the flag.
    """
    # aggregate duplicates (same account+service+date in different regions)
    agg = (ce.groupby(["linked_account_name", "service_code", "date"], as_index=False)
           .agg(cost=("unblended_cost", "sum"),
                is_estimated=("is_estimated", "any")))

    all_days = pd.date_range(agg.date.min(), agg.date.max(), freq="D")
    keys = agg[["linked_account_name", "service_code"]].drop_duplicates()
    idx = pd.MultiIndex.from_frame(keys)
    full_idx = pd.MultiIndex.from_product(
        [keys["linked_account_name"].unique(),
         keys["service_code"].unique(),
         all_days],
        names=["linked_account_name", "service_code", "date"])
    panel = (agg.set_index(["linked_account_name", "service_code", "date"])
             .reindex(full_idx, fill_value=0)
             .reset_index())
    # drop (account, service) combos that never had any real cost (artifact of cross-product)
    has_cost = agg.groupby(["linked_account_name", "service_code"])["cost"].sum()
    panel = panel.merge(has_cost.rename("_total").reset_index(),
                        on=["linked_account_name", "service_code"])
    panel = panel[panel["_total"] > 0].drop(columns=["_total"]).reset_index(drop=True)
    panel["is_estimated"] = panel["is_estimated"].fillna(False)
    return panel


def _streak(s: pd.Series) -> pd.Series:
    """Count consecutive True streak within a boolean Series, reset on False.

    Returns integer Series: 0 for False, 1/2/3... for consecutive True runs.
    Uses a simple loop to avoid the off-by-one issue with groupby-cumcount.
    """
    s = s.fillna(False).astype(bool)
    out = np.zeros(len(s), dtype=int)
    run = 0
    for i, v in enumerate(s):
        run = (run + 1) if v else 0
        out[i] = run
    return pd.Series(out, index=s.index)


def _consecutive_true(arr: np.ndarray) -> np.ndarray:
    """Vectorised consecutive-True counter (for resource grain)."""
    out = np.zeros(len(arr), dtype=int)
    run = 0
    for i, v in enumerate(arr):
        run = (run + 1) if v else 0
        out[i] = run
    return out


def _fit_stl(series: pd.Series, period: int = 7) -> tuple[np.ndarray, np.ndarray]:
    """Fit STL and return (trend, residual). Returns zeros on failure."""
    vals = series.values.astype(float)
    if np.all(vals == 0) or len(vals) < period * 2:
        return np.zeros(len(vals)), np.zeros(len(vals))
    try:
        res = STL(vals, period=period, robust=True).fit()
        return res.trend, res.resid
    except Exception:
        return np.zeros(len(vals)), np.zeros(len(vals))


def engineer_panel_features(panel: pd.DataFrame, cur: pd.DataFrame) -> pd.DataFrame:
    """Compute all panel-level features.

    All features are purely behavioural — no account_id / service_name in ML inputs.
    Continuous frequency priors (account_freq, service_freq) are the only
    identity-derived signals, and they are real-valued, not categorical integers.
    """
    C = CONSTANTS
    pf = panel.sort_values(["linked_account_name", "service_code", "date"]).copy()
    grp = pf.groupby(["linked_account_name", "service_code"])["cost"]

    # ── rolling baselines ────────────────────────────────────────────────────
    pf["rolling_mean_7d"]    = grp.transform(lambda x: x.rolling(7,  min_periods=3).mean())
    pf["rolling_std_7d"]     = grp.transform(lambda x: x.rolling(7,  min_periods=3).std())
    pf["rolling_mean_28d"]   = grp.transform(lambda x: x.rolling(28, min_periods=7).mean())
    pf["rolling_median_28d"] = grp.transform(lambda x: x.rolling(28, min_periods=7).median())

    # ── temporal features ────────────────────────────────────────────────────
    pf["lag_1"] = grp.transform(lambda x: x.shift(1))
    pf["lag_7"] = grp.transform(lambda x: x.shift(7))
    pf["ewma_7"] = grp.transform(lambda x: x.ewm(span=7, adjust=False).mean())

    # ── robust Z-score (MAD-based, less contaminated by spikes) ─────────────
    pf["robust_z_score"] = (
        (pf["cost"] - pf["rolling_median_28d"])
        / pf["rolling_std_7d"].clip(lower=0.5)
    )

    # ── spike ratio: today vs a PRE-SPIKE baseline (avoid self-contamination)
    # Use median of last 28d but exclude last 7d (the potential spike window)
    pf["pre_spike_baseline"] = grp.transform(
        lambda x: x.shift(7).rolling(21, min_periods=7).median())
    pf["spike_ratio"] = pf["cost"] / pf["pre_spike_baseline"].clip(lower=1.0)

    # ── drift ────────────────────────────────────────────────────────────────
    pf["drift_ratio"] = (
        pf["rolling_mean_7d"] / pf["rolling_mean_28d"].clip(lower=1.0)
    )
    pf["_drift_above"] = pf["drift_ratio"] > C["DRIFT_RATIO_THRESH"]
    pf["drift_sustained_days"] = (
        pf.groupby(["linked_account_name", "service_code"])["_drift_above"]
        .transform(_streak)
    )

    # ── CUSUM (positive deviations only — cost drop is not an anomaly) ───────
    cusum_parts = []
    for _, g in pf.groupby(["linked_account_name", "service_code"]):
        dev = g["cost"].sub(g["rolling_mean_28d"]).clip(lower=0).fillna(0.0)
        cusum_parts.append(pd.Series(dev.cumsum().values, index=g.index))
    pf["cusum_pos"] = pd.concat(cusum_parts).reindex(pf.index).fillna(0.0)

    # ── rate of change & pct rank ────────────────────────────────────────────
    pf["rate_of_change"] = grp.transform(lambda x: x.pct_change())

    def _rolling_pct_rank(series: pd.Series, window: int, min_p: int) -> pd.Series:
        """Causal percentile rank: position of last value in its rolling window."""
        result = np.full(len(series), np.nan)
        vals = series.values
        for i in range(len(vals)):
            lo = max(0, i - window + 1)
            w = vals[lo: i + 1]
            if len(w) >= min_p and not np.isnan(w).all():
                result[i] = float(pd.Series(w).rank(pct=True).iloc[-1])
        return pd.Series(result, index=series.index)

    rank7_parts, rank30_parts = [], []
    for _, g in pf.groupby(["linked_account_name", "service_code"]):
        rank7_parts.append(_rolling_pct_rank(g["cost"], 7, 3))
        rank30_parts.append(_rolling_pct_rank(g["cost"], 30, 10))
    pf["pct_rank_7d"]  = pd.concat(rank7_parts).reindex(pf.index).fillna(0.5)
    pf["pct_rank_30d"] = pd.concat(rank30_parts).reindex(pf.index).fillna(0.5)

    # ── account-level cost share (contextual, not identity) ──────────────────
    daily_acct = pf.groupby(["linked_account_name", "date"])["cost"].sum().rename("acct_daily_total")
    pf = pf.merge(daily_acct.reset_index(), on=["linked_account_name", "date"])
    pf["cost_share_of_account"] = pf["cost"] / pf["acct_daily_total"].clip(lower=1.0)

    # ── history length (cold-start guard) ────────────────────────────────────
    pf["group_history_days"] = (
        pf.groupby(["linked_account_name", "service_code"])["date"]
        .transform(lambda x: (x - x.min()).dt.days)
    )

    # ── STL trend & residual ─────────────────────────────────────────────────
    stl_trend, stl_resid = [], []
    for (acc, svc), g in pf.groupby(["linked_account_name", "service_code"]):
        tr, re = _fit_stl(g["cost"])
        stl_trend.append(pd.Series(tr, index=g.index))
        stl_resid.append(pd.Series(re, index=g.index))
    pf["stl_trend"]    = pd.concat(stl_trend).reindex(pf.index).fillna(0.0)
    pf["stl_residual"] = pd.concat(stl_resid).reindex(pf.index).fillna(0.0)

    # ── continuous frequency priors (identity-free ML-safe proxy) ────────────
    pf["account_freq"] = pf.groupby("linked_account_name")["date"].transform("count") / len(pf)
    pf["service_freq"] = pf.groupby("service_code")["date"].transform("count") / len(pf)

    # ── calendar ─────────────────────────────────────────────────────────────
    pf["is_weekend"] = pf["date"].dt.dayofweek >= 5
    pf["day_of_week"] = pf["date"].dt.dayofweek

    # ── CUR: max resource share (top resource dominance in this service today) ─
    cur_daily = (
        cur.groupby(["line_item_usage_account_name", "line_item_product_code",
                     cur["line_item_usage_start_date"].dt.normalize()],
                    as_index=False)["line_item_unblended_cost"].sum()
        .rename(columns={"line_item_usage_account_name": "linked_account_name",
                         "line_item_product_code": "service_code",
                         "line_item_usage_start_date": "date",
                         "line_item_unblended_cost": "svc_total_cur"})
    )
    cur_res_max = (
        cur.groupby(["line_item_usage_account_name", "line_item_product_code",
                     cur["line_item_usage_start_date"].dt.normalize(),
                     "line_item_resource_id"], as_index=False)["line_item_unblended_cost"]
        .sum()
        .rename(columns={"line_item_usage_account_name": "linked_account_name",
                         "line_item_product_code": "service_code",
                         "line_item_usage_start_date": "date"})
        .groupby(["linked_account_name", "service_code", "date"], as_index=False)
        ["line_item_unblended_cost"].max()
        .rename(columns={"line_item_unblended_cost": "max_res_cost"})
    )
    cur_share = cur_daily.merge(cur_res_max, on=["linked_account_name", "service_code", "date"])
    cur_share["max_resource_share"] = cur_share["max_res_cost"] / cur_share["svc_total_cur"].clip(lower=1.0)
    pf = pf.merge(cur_share[["linked_account_name", "service_code", "date", "max_resource_share"]],
                  on=["linked_account_name", "service_code", "date"], how="left")
    pf["max_resource_share"] = pf["max_resource_share"].fillna(0.5)

    pf.drop(columns=["_drift_above"], inplace=True, errors="ignore")
    return pf.reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3 — Feature Engineering: CUR Resource Level
# ─────────────────────────────────────────────────────────────────────────────

def build_resource_metric_daily(metrics: pd.DataFrame) -> pd.DataFrame:
    """Pivot metrics to (resource_id, date, metric_name) → wide format."""
    m = metrics.copy()
    m["date"] = m["timestamp"].dt.normalize()
    daily = (m.groupby(["resource_id", "service", "account_id", "date", "metric_name"],
                       as_index=False)["metric_value"].mean())
    wide = daily.pivot_table(index=["resource_id", "service", "account_id", "date"],
                             columns="metric_name", values="metric_value",
                             aggfunc="mean").reset_index()
    wide.columns.name = None
    return wide


def engineer_resource_features(cur: pd.DataFrame, rmet: pd.DataFrame) -> pd.DataFrame:
    """Compute per-resource daily features.

    Key behavioural signals:
      - weekend_ratio_14d : weekend cost / weekday cost (runaway GPU ≈ 1.0)
      - resource_age_days  : days since first appearance in billing
      - is_new_resource    : True for first RUNAWAY_NEW_RESOURCE_DAYS after appearance
      - sustained_stable_days : consecutive days with flat cost (idle signal)
      - untagged_flag / team : tag information for untagged rule
    """
    C = CONSTANTS

    # aggregate to resource × day
    GRP = ["line_item_resource_id", "line_item_usage_account_id",
           "line_item_usage_account_name", "line_item_product_code",
           "line_item_usage_type"]
    cur_d = cur.copy()
    cur_d["date"] = cur_d["line_item_usage_start_date"].dt.normalize()
    cur_d["is_weekend"] = cur_d["date"].dt.dayofweek >= 5
    cur_d["team"] = cur_d["resource_tags_user_team"]

    res = (cur_d.groupby(GRP + ["date", "is_weekend", "team"], dropna=False,
                         as_index=False)["line_item_unblended_cost"]
           .sum()
           .rename(columns={"line_item_resource_id": "resource_id",
                             "line_item_usage_account_id": "account_id",
                             "line_item_usage_account_name": "account_name",
                             "line_item_product_code": "service_code",
                             "line_item_usage_type": "usage_type",
                             "line_item_unblended_cost": "cost"}))
    res = res.sort_values(["resource_id", "date"]).reset_index(drop=True)

    # ── rolling stats ────────────────────────────────────────────────────────
    g = res.groupby("resource_id")["cost"]
    res["rolling_mean_7d"]    = g.transform(lambda x: x.rolling(7, min_periods=3).mean())
    res["rolling_std_7d"]     = g.transform(lambda x: x.rolling(7, min_periods=3).std())
    res["rolling_median_14d"] = g.transform(lambda x: x.rolling(14, min_periods=5).median())
    res["z_score_7d"]  = (res["cost"] - res["rolling_mean_7d"]) / res["rolling_std_7d"].clip(lower=0.1)
    res["z_score_robust"] = (res["cost"] - res["rolling_median_14d"]) / res["rolling_std_7d"].clip(lower=0.1)
    res["pct_change_1d"] = g.transform(lambda x: x.pct_change())

    # ── resource age ─────────────────────────────────────────────────────────
    first_seen = (res.groupby("resource_id")["date"].min()
                  .reset_index().rename(columns={"date": "first_seen_date"}))
    res = res.merge(first_seen, on="resource_id")
    res["resource_age_days"] = (res["date"] - res["first_seen_date"]).dt.days
    res["is_new_resource"] = res["resource_age_days"] <= C["RUNAWAY_NEW_RESOURCE_DAYS"]

    # ── weekend_ratio_14d (vectorised) ───────────────────────────────────────
    rs = res.copy()
    rs["_cost_wd"] = np.where(~rs["is_weekend"], rs["cost"], np.nan)
    rs["_cost_we"] = np.where(rs["is_weekend"],  rs["cost"], np.nan)
    g3 = rs.groupby("resource_id")
    rs["_roll_wd"] = g3["_cost_wd"].transform(lambda x: x.rolling(14, min_periods=3).mean())
    rs["_roll_we"] = g3["_cost_we"].transform(lambda x: x.rolling(14, min_periods=2).mean())
    rs["weekend_ratio_14d"] = rs["_roll_we"] / rs["_roll_wd"].clip(lower=0.01)
    rs.drop(columns=["_cost_wd", "_cost_we", "_roll_wd", "_roll_we"], inplace=True)
    res = rs.reset_index(drop=True)

    # ── sustained_stable_days (idle signal — cost flat, not zero) ────────────
    res["_stable"] = (
        (res["cost"] > C["IDLE_MIN_COST"]) &
        (res["z_score_7d"].abs() <= 1.5)
    ).fillna(False)
    res["sustained_stable_days"] = (
        res.groupby("resource_id")["_stable"].transform(_streak)
    )

    # ── untagged flag ─────────────────────────────────────────────────────────
    res["untagged_flag"] = (
        (res["team"].isna() | (res["team"].astype(str).str.strip() == "")) &
        (res["cost"] >= C["UNTAGGED_MIN_COST"])
    )

    # ── join operational metrics ─────────────────────────────────────────────
    metric_cols = ["CPUUtilization", "DatabaseConnections",
                   "VolumeIdleTime", "GPUUtilization", "MemoryUtilization"]
    if len(rmet) > 0:
        rmet_join = rmet.rename(columns={"service": "service_code",
                                         "account_id": "account_id"}).copy()
        keep = ["resource_id", "date"] + [c for c in metric_cols if c in rmet_join.columns]
        res = res.merge(rmet_join[keep], on=["resource_id", "date"], how="left")
    for mc in metric_cols:
        if mc not in res.columns:
            res[mc] = np.nan

    res.drop(columns=["_stable"], inplace=True, errors="ignore")
    return res.reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5 — Rule Engine (deterministic, zero ML)
# ─────────────────────────────────────────────────────────────────────────────

def apply_panel_rules(pf: pd.DataFrame) -> pd.DataFrame:
    """Apply rule-based flags on the CE panel dataframe.

    Rules:
      rule_spike : spike_ratio >= SPIKE_RATIO AND cost >= SPIKE_MIN_COST
                   sustained >= SPIKE_SUSTAINED_DAYS consecutive days
                   AND history >= PANEL_MIN_HISTORY  (cold-start guard)
                   NOTE: ≥5 days is what suppresses B(3d), planned-flash(4d), etc.

      rule_drift : drift_ratio > DRIFT_RATIO_THRESH
                   sustained >= DRIFT_SUSTAINED_DAYS consecutive days
    """
    C = CONSTANTS
    pf = pf.copy()

    # ── spike candidate (single day) ─────────────────────────────────────────
    pf["_spike_cand"] = (
        (pf["spike_ratio"] >= C["SPIKE_RATIO"]) &
        (pf["cost"] >= C["SPIKE_MIN_COST"]) &
        (pf["group_history_days"] >= C["PANEL_MIN_HISTORY"])  # cold-start guard
    ).fillna(False)

    pf["_spike_run"] = (
        pf.groupby(["linked_account_name", "service_code"])["_spike_cand"]
        .transform(_streak)
    )
    # NOTE: ≥5 sustained days is what prevents B(benign 3-day campaign) from firing
    pf["rule_spike"] = (pf["_spike_run"] >= C["SPIKE_SUSTAINED_DAYS"]).astype(int)

    # ── drift ─────────────────────────────────────────────────────────────────
    pf["_drift_cand"] = (pf["drift_ratio"] > C["DRIFT_RATIO_THRESH"]).fillna(False)
    pf["_drift_run"] = (
        pf.groupby(["linked_account_name", "service_code"])["_drift_cand"]
        .transform(_streak)
    )
    pf["rule_drift"] = (pf["_drift_run"] >= C["DRIFT_SUSTAINED_DAYS"]).astype(int)

    pf.drop(columns=["_spike_cand", "_spike_run", "_drift_cand", "_drift_run"],
            inplace=True, errors="ignore")
    return pf


def detect_resource_alerts(rf: pd.DataFrame) -> pd.DataFrame:
    """Apply resource-grain rules: runaway, idle, untagged.

    All thresholds are behaviour-based; the only reference to a service type
    is EC2_CODE (module-level, overridable for generalization test).
    """
    C = CONSTANTS
    out = []

    for rid, g in rf.groupby("resource_id"):
        g = g.sort_values("date").copy()

        # ── idle_resource ─────────────────────────────────────────────────────
        has_util = g[["CPUUtilization", "DatabaseConnections",
                       "VolumeIdleTime", "GPUUtilization"]].notna().any(axis=1).values
        idle_day = (
            (g["cost"] > C["IDLE_MIN_COST"]) &
            (
                (g["CPUUtilization"] < C["IDLE_CPU_PCT"]) |
                (g["DatabaseConnections"] < C["IDLE_DB_CONN"]) |
                (g["VolumeIdleTime"] > C["IDLE_VOL_IDLE"]) |
                (g["GPUUtilization"] < C["IDLE_GPU_PCT"])
            )
        ).fillna(False).values & has_util

        g["idle_run"] = _consecutive_true(idle_day)
        g["idle_alert"] = (g["idle_run"] >= C["IDLE_SUSTAINED_DAYS"]).astype(int)

        # ── runaway_usage (compute resource, no weekend dip, sustained) ────────
        # NOTE: We don't gate on is_new_resource here because a runaway GPU
        # cluster keeps running beyond day 7. Instead we use:
        #   - service is compute (EC2_CODE)
        #   - cost is material
        #   - weekend_ratio >= RUNAWAY_WEEKEND_RATIO (running full weekends)
        #   - resource first appeared in the dataset within RUNAWAY_NEW_RESOURCE_DAYS
        #     of the ANOMALY window (i.e. resource was spawned recently overall)
        is_compute = (g["service_code"] == EC2_CODE).values
        # resource_age_days from first billing record — if resource started recently
        # AND maintains high weekend ratio the whole time, it is runaway
        first_age = g["resource_age_days"].iloc[0] if len(g) > 0 else 999
        resource_is_new_overall = (first_age <= C["RUNAWAY_NEW_RESOURCE_DAYS"])
        run_day = (
            resource_is_new_overall &
            is_compute &
            (g["weekend_ratio_14d"].fillna(0.0) > C["RUNAWAY_WEEKEND_RATIO"]) &
            (g["cost"] > C["RUNAWAY_MIN_COST"])
        )
        run_day = np.asarray(pd.Series(run_day).fillna(False))
        g["run_run"] = _consecutive_true(run_day)
        g["runaway_alert"] = (g["run_run"] >= C["RUNAWAY_MIN_DAYS"]).astype(int)

        # ── untagged_spend ────────────────────────────────────────────────────
        untag_day = (
            (g["team"].isna() | (g["team"].astype(str).str.strip() == "")) &
            (g["cost"] >= C["UNTAGGED_MIN_COST"])
        ).fillna(False).values
        g["untag_run"] = _consecutive_true(untag_day)
        # NOTE: ≥3 days = chronic (not a one-off mis-tag)
        g["untagged_alert"] = (g["untag_run"] >= C["UNTAGGED_MIN_DAYS"]).astype(int)

        # ── aggregate ─────────────────────────────────────────────────────────
        g["res_pred_type"] = np.where(
            g["untagged_alert"] == 1, "untagged_spend",
            np.where(g["runaway_alert"] == 1, "runaway_usage",
                     np.where(g["idle_alert"] == 1, "idle_resource", "none")))
        g["res_alert"] = (
            (g["runaway_alert"] == 1) | (g["idle_alert"] == 1) | (g["untagged_alert"] == 1)
        ).astype(int)
        g["res_conf"] = np.where(
            g["untagged_alert"] == 1, "HIGH",
            np.where((g["runaway_alert"] == 1) & (g["weekend_ratio_14d"] > 0.98), "HIGH",
                     np.where(g["idle_alert"] == 1, "HIGH",
                              np.where(g["res_alert"] == 1, "MEDIUM", "NONE"))))
        out.append(g)

    return pd.concat(out, ignore_index=True) if out else rf.copy()


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 8 — Isolation Forest Ensemble (per anomaly type)
# ─────────────────────────────────────────────────────────────────────────────

_IF_FEATURE_SETS = {
    "spike": ["spike_ratio", "robust_z_score", "stl_residual",
              "pct_rank_30d", "rate_of_change", "max_resource_share"],
    "drift": ["drift_ratio", "drift_sustained_days", "cusum_pos",
              "stl_trend", "pct_rank_30d", "account_freq", "service_freq"],
}


def train_isolation_forests(pf: pd.DataFrame) -> dict:
    """Train one IsolationForest per anomaly type on behavioural features only.

    Returns dict: {type_name: (fitted_model, feature_col_list)}
    No identity features (account/service/resource names) are included.
    """
    C = CONSTANTS
    models = {}
    for typ, cols in _IF_FEATURE_SETS.items():
        # only keep cols that actually exist (some may be missing in small datasets)
        cols_ok = [c for c in cols if c in pf.columns]
        X = pf[cols_ok].replace([np.inf, -np.inf], np.nan).fillna(0.0).values
        cont = C["IF_CONTAMINATION_SPIKE"] if typ == "spike" else C["IF_CONTAMINATION_DRIFT"]
        model = IsolationForest(
            contamination=cont,
            n_estimators=200,
            max_features=min(len(cols_ok), 4),   # limit depth → better generalization
            random_state=C["IF_RANDOM_STATE"],
            n_jobs=-1,
        ).fit(X)
        models[typ] = (model, cols_ok)
    return models


def score_isolation_forests(pf: pd.DataFrame, models: dict) -> pd.DataFrame:
    """Add IF anomaly scores to the panel dataframe.

    score range: [0, 1] where higher = more anomalous
    (sklearn returns negative scores; we negate and normalise to [0,1])
    """
    pf = pf.copy()
    for typ, (model, cols) in models.items():
        X = pf[cols].replace([np.inf, -np.inf], np.nan).fillna(0.0).values
        raw = -model.score_samples(X)       # higher = more anomalous
        mn, mx = raw.min(), raw.max()
        pf[f"if_{typ}"] = (raw - mn) / (mx - mn + 1e-9)
    return pf


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 9 — Score Fusion & Hybrid Decision
# ─────────────────────────────────────────────────────────────────────────────

def fuse(pf: pd.DataFrame, res_alerts: pd.DataFrame
         ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Merge panel and resource alerts into a unified fired-alert table.

    Confidence tiers:
      HIGH   — rule fires  AND  (IF score >= IF_PROMOTE_THRESHOLD  OR  rule is deterministic)
      MEDIUM — rule fires  but  IF score below threshold
      LOW    — only IF fires (no rule confirmation)

    Benign suppression is implicit in the rules (spike ≥5d, drift ≥14d,
    runaway+weekend_ratio, untagged ≥3d). No post-hoc suppression layer needed.
    """
    C = CONSTANTS
    pf = pf.copy()

    # ── panel alert tiers ─────────────────────────────────────────────────────
    pf["panel_alert"] = ((pf["rule_spike"] == 1) | (pf["rule_drift"] == 1)).astype(int)
    pf["pred_type"] = np.where(pf["rule_spike"] == 1, "sudden_spike",
                        np.where(pf["rule_drift"] == 1, "gradual_drift", "none"))

    # IF promotes MEDIUM → HIGH
    spike_if_ok = pf.get("if_spike", pd.Series(0.0, index=pf.index)) >= C["IF_PROMOTE_THRESHOLD"]
    drift_if_ok = pf.get("if_drift", pd.Series(0.0, index=pf.index)) >= C["IF_PROMOTE_THRESHOLD"]

    pf["confidence"] = np.where(
        (pf["rule_spike"] == 1) & spike_if_ok, "HIGH",
        np.where(pf["rule_spike"] == 1, "MEDIUM",
            np.where((pf["rule_drift"] == 1) & drift_if_ok, "HIGH",
                np.where(pf["rule_drift"] == 1, "MEDIUM", "NONE"))))
    pf["alert_fired"] = pf["panel_alert"]

    # ── persistence filter on panel (N consecutive days) ─────────────────────
    N = C["PERSISTENCE_N"]
    # Rules with multi-day sustain requirement (spike≥5d, drift≥14d) are already
    # persistent by definition — don't double-gate them.
    # We apply persistence only to add a secondary noise-filter for borderline cases.
    pf["_soft"] = pf["panel_alert"].astype(bool)
    pf["_persist_run"] = (
        pf.groupby(["linked_account_name", "service_code"])["_soft"]
        .transform(_streak)
    )
    # For spike/drift rules, override: if rule fires it counts as persisted already
    pf["alert_fired"] = (
        (pf["_persist_run"] >= N) | (pf["rule_spike"] == 1) | (pf["rule_drift"] == 1)
    ).astype(int) * pf["panel_alert"]

    panel_fired = pf[pf["alert_fired"] == 1].copy()

    # ── merge resource-grain alerts ───────────────────────────────────────────
    res_panel = res_alerts[res_alerts["res_alert"] == 1].copy()
    res_panel = res_panel.rename(columns={
        "account_name": "linked_account_name",
        "service_code": "service_code",
        "res_pred_type": "pred_type",
        "res_conf": "confidence",
    })
    res_panel["alert_fired"] = 1

    # build unified fired table
    panel_cols = ["linked_account_name", "service_code", "date",
                  "cost", "pred_type", "confidence", "alert_fired"]
    res_cols   = ["linked_account_name", "service_code", "date",
                  "cost", "pred_type", "confidence", "alert_fired", "resource_id"]
    p_out = panel_fired[panel_cols].copy() if len(panel_fired) > 0 else pd.DataFrame(columns=panel_cols)
    r_out = res_panel[[c for c in res_cols if c in res_panel.columns]].copy()

    fired_all = pd.concat([p_out, r_out], ignore_index=True)
    if "resource_id" not in fired_all.columns:
        fired_all["resource_id"] = np.nan
    fired_all = fired_all.sort_values("date").reset_index(drop=True)

    # also return full panel with alert_fired column
    pf_full = pf.copy()
    pf_full.drop(columns=["_soft", "_persist_run"], inplace=True, errors="ignore")

    return fired_all, pf_full, res_alerts


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 11 — Evaluation: Event-Level Backtest
# ─────────────────────────────────────────────────────────────────────────────

def evaluate_events(fired_all: pd.DataFrame,
                    labels: pd.DataFrame,
                    grace_days: int = 2
                    ) -> tuple[pd.DataFrame, dict]:
    """Event-level evaluation against ground-truth labels.

    Scoring is per UNIQUE anomaly_id (not per row/day).
    For multi-resource anomalies (e.g. A1 with 5 GPU instances), the group is
    detected if ANY resource in the group fires an alert.

    Parameters
    ----------
    fired_all  : output of fuse(), all alert-fired rows
    labels     : anomaly_labels_full.csv loaded as DataFrame
    grace_days : extra days beyond end_date still counted as TP

    Returns
    -------
    res_df  : per-event detection result
    metrics : dict with precision, recall, f1, fpr
    """
    labels = labels.copy()
    labels["start_date"] = pd.to_datetime(labels["start_date"])
    labels["end_date"]   = pd.to_datetime(labels["end_date"])

    rows = []
    for aid, grp in labels.groupby("anomaly_id"):
        lbl   = grp["label"].iloc[0]
        atype = grp["anomaly_type"].iloc[0]
        acc   = grp["linked_account_name"].iloc[0]
        svc   = grp["service"].iloc[0]           # service code in labels
        rids  = set(grp["resource_id"].dropna().unique())
        start = grp["start_date"].min()
        end   = grp["end_date"].max()
        window_end = end + pd.Timedelta(days=grace_days)

        # match fired alerts:
        # Strategy: use resource_id match when the label specifies resources AND
        # there are hits. Fall back to (account, service) panel match so that
        # panel-level alerts (no resource_id attached) can still satisfy the event.
        # This prevents A4-on-same-panel from polluting B1's benign evaluation,
        # while still allowing panel alerts to satisfy events without resource_id.
        res_id_col = fired_all.get("resource_id", pd.Series(dtype=str))

        # Base time/window filter
        time_mask = (
            (fired_all["date"] >= start) &
            (fired_all["date"] <= window_end)
        )

        if len(rids) > 0:
            # For events with specific resource_ids:
            # 1st choice: resource-grain alert on exact resource_id
            rid_mask = time_mask & res_id_col.isin(rids)
            hits = fired_all[rid_mask]
            # 2nd choice: panel alert (no resource_id) on same account+service
            if len(hits) == 0:
                panel_mask = (
                    time_mask &
                    res_id_col.isna() &
                    (fired_all["linked_account_name"] == acc) &
                    (fired_all["service_code"] == svc)
                )
                hits = fired_all[panel_mask]
        else:
            # No specific resources: match on account+service (panel alerts)
            mask = (
                time_mask &
                (fired_all["linked_account_name"] == acc) &
                (fired_all["service_code"] == svc)
            )
            hits = fired_all[mask]
        detected = len(hits) > 0
        first_alert = hits["date"].min() if detected else pd.NaT
        delay = (first_alert - start).days if detected else np.nan
        conf  = hits["confidence"].iloc[0] if detected else "NONE"
        pred  = hits["pred_type"].iloc[0] if detected else "none"

        rows.append(dict(anomaly_id=aid, label=lbl, anomaly_type=atype,
                         detected=detected, first_alert=first_alert,
                         delay=delay, confidence=conf, pred_type=pred))

    res_df = pd.DataFrame(rows)

    anomalies = res_df[res_df["label"] == "anomaly"]
    benigns   = res_df[res_df["label"] == "benign"]

    TP = int(anomalies["detected"].sum())
    FN = int((~anomalies["detected"]).sum())
    FP = int(benigns["detected"].sum())       # benign fired = FP
    TN = int((~benigns["detected"]).sum())

    precision = TP / (TP + FP) if (TP + FP) > 0 else 0.0
    recall    = TP / (TP + FN) if (TP + FN) > 0 else 0.0
    f1        = (2 * precision * recall / (precision + recall)
                 if (precision + recall) > 0 else 0.0)
    fpr       = FP / (FP + TN) if (FP + TN) > 0 else 0.0

    metrics = dict(precision=precision, recall=recall, f1=f1, fpr=fpr,
                   TP=TP, FP=FP, FN=FN, TN=TN)
    return res_df, metrics


# ─────────────────────────────────────────────────────────────────────────────
# FinOpsWatch facade (used in Section 13 generalization test)
# ─────────────────────────────────────────────────────────────────────────────

class FinOpsWatch:
    """Convenience facade that wraps the full pipeline in a single fit_transform call."""

    def fit_transform(self, ce: pd.DataFrame, cur: pd.DataFrame,
                      metrics: pd.DataFrame) -> dict:
        panel  = build_panel(ce)
        pf     = engineer_panel_features(panel, cur)
        rmet   = build_resource_metric_daily(metrics)
        rf     = engineer_resource_features(cur, rmet)
        pf     = apply_panel_rules(pf)
        ra     = detect_resource_alerts(rf)
        models = train_isolation_forests(pf)
        pf     = score_isolation_forests(pf, models)
        fired_all, pf_full, ra = fuse(pf, ra)
        return dict(fired_all=fired_all, pf=pf_full, rf=rf, res_alerts=ra,
                    models=models)
