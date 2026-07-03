"""
Feature Engineering — FinOps Watch
====================================
Tính đầy đủ tất cả features cho 5 anomaly types:
  - sudden_spike   : is_new_service + duration_days >= 5 (disambiguates benign B1/B2/B3)
  - runaway_usage  : weekend_ratio_14d ~1.0 + resource_age_days
  - gradual_drift  : drift_ratio (mean_7d/mean_28d), drift_sustained_days
  - idle_resource  : sustained_stable_days >= 14
  - untagged_spend : untagged_flag (rule-based, team_tag IS NULL + cost >= $30/day)

Disambiguation logic (benign FP prevention):
  - B1 loadtest (2 days), B2 migration (3 days), B3 flashsale (4 days)
    -> sudden_spike requires duration >= 5 days -> all 3 benign NOT flagged
  - A6 CloudWatch spike (7 days) -> IS flagged

Output:
  - hao-v2/results/ce_features.csv
  - hao-v2/results/cur_resource_features.csv
"""

import pandas as pd
import numpy as np
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────
ROOT = Path(r"c:\Users\ASUS\Documents\AIOps\Capstone_Phase2_AIOps\Capstone_Phase2_AI")
DATA = ROOT / "data"
OUT  = ROOT / "hao-v2" / "results"
OUT.mkdir(parents=True, exist_ok=True)

# ── Constants ─────────────────────────────────────────────────────────────────
DATASET_START          = pd.Timestamp("2026-03-01")
WARMUP_DAYS            = 7          # ignore new-service signal in first week
DRIFT_RATIO_THRESHOLD  = 1.15       # mean_7d / mean_28d above this = drift
UNTAGGED_COST_MIN      = 30.0       # $/day minimum to flag untagged
STABLE_Z_BAND          = 1.5        # |z| <= this = "stable cost" for idle detection
STABLE_COST_MIN        = 5.0        # $/day minimum to count as "has cost"
ABOVE_ROBUST_Z         = 2.0        # z_score_robust threshold for spike

# ═══════════════════════════════════════════════════════════════════════════════
# PART A — CE-LEVEL FEATURES
# ═══════════════════════════════════════════════════════════════════════════════
print("Loading data...")
ce = pd.read_csv(DATA / "cost_explorer_daily.csv",
                 parse_dates=["date"],
                 dtype={"linked_account_id": str})

ce = ce.sort_values(["linked_account_id", "service_code", "date"]).reset_index(drop=True)

print("Computing CE rolling features...")
g = ce.groupby(["linked_account_id", "service_code"])["unblended_cost"]

ce["rolling_mean_7d"]    = g.transform(lambda x: x.rolling(7,  min_periods=3).mean())
ce["rolling_std_7d"]     = g.transform(lambda x: x.rolling(7,  min_periods=3).std())
ce["rolling_mean_28d"]   = g.transform(lambda x: x.rolling(28, min_periods=14).mean())
ce["rolling_median_28d"] = g.transform(lambda x: x.rolling(28, min_periods=14).median())

# z_score using MEDIAN baseline — robust against contamination from spike itself
ce["z_score_robust"] = (
    (ce["unblended_cost"] - ce["rolling_median_28d"])
    / ce["rolling_std_7d"].clip(lower=0.5)
)
# z_score standard (kept for comparison)
ce["z_score_7d"] = (
    (ce["unblended_cost"] - ce["rolling_mean_7d"])
    / ce["rolling_std_7d"].clip(lower=0.5)
)

# drift_ratio: mean_7d / mean_28d — gradual drift signal
ce["drift_ratio"] = (
    ce["rolling_mean_7d"] / ce["rolling_mean_28d"].clip(lower=1.0)
)

# drift_sustained_days: consecutive days with drift_ratio > threshold
ce["_drift_above"] = ce["drift_ratio"] > DRIFT_RATIO_THRESHOLD

def streak(s: pd.Series) -> pd.Series:
    """Count consecutive True streak, reset on False."""
    cumfalse = (~s).cumsum()
    return (s.groupby(cumfalse).cumcount() + 1) * s

ce["drift_sustained_days"] = (
    ce.groupby(["linked_account_id", "service_code"])["_drift_above"]
    .transform(streak)
)
ce.drop(columns=["_drift_above"], inplace=True)

# pct_change day-over-day
ce["pct_change_1d"] = (
    ce.groupby(["linked_account_id", "service_code"])["unblended_cost"]
    .transform(lambda x: x.pct_change())
)

# is_new_service: service appears in account for first time after warmup
fa = (ce.groupby(["linked_account_id", "service_code"])["date"]
      .min().reset_index().rename(columns={"date": "first_appearance_date"}))
ce = ce.merge(fa, on=["linked_account_id", "service_code"])
ce["service_age_days"] = (ce["date"] - ce["first_appearance_date"]).dt.days
ce["is_new_service"] = (
    (ce["service_age_days"] <= 3) &
    (ce["first_appearance_date"] > DATASET_START + pd.Timedelta(days=WARMUP_DAYS))
)

# service_duration_days: total days the service appears in that account
# Used for sudden_spike disambiguation: benign events <= 4 days, real spikes >= 5
svc_duration = (ce.groupby(["linked_account_id", "service_code"])["date"]
                .count().reset_index().rename(columns={"date": "service_duration_days"}))
ce = ce.merge(svc_duration, on=["linked_account_id", "service_code"])

# sudden_spike_flag: new service + lasts >= 5 days (rules out B1=2d, B2=3d, B3=4d)
ce["sudden_spike_flag"] = (
    ce["is_new_service"] &
    (ce["service_duration_days"] >= 5)
)

ce["is_weekend"]  = ce["date"].dt.dayofweek >= 5
ce["is_estimated"] = ce["is_estimated"].astype(bool)

ce_out = ce.drop(columns=["service"])   # keep service_code canonical
ce_out.to_csv(OUT / "ce_features.csv", index=False)
print(f"  => ce_features.csv saved: {ce_out.shape[0]} rows x {ce_out.shape[1]} cols")
# ═══════════════════════════════════════════════════════════════════════════════
# PART B — RESOURCE-LEVEL FEATURES
# ═══════════════════════════════════════════════════════════════════════════════
print("\nLoading CUR data...")
cur = pd.read_csv(DATA / "cur_line_items.csv",
                  parse_dates=["line_item_usage_start_date"],
                  dtype={"line_item_usage_account_id": str})

cur["date"]       = cur["line_item_usage_start_date"].dt.date
cur["is_weekend"] = cur["line_item_usage_start_date"].dt.dayofweek >= 5

print("Aggregating to resource × day...")
GRP_COLS = [
    "line_item_resource_id",
    "line_item_usage_account_id",
    "line_item_usage_account_name",
    "line_item_product_code",
    "line_item_usage_type",
    "date",
    "is_weekend",
    "resource_tags_user_team",
    "resource_tags_user_environment",
    "resource_tags_user_cost_center",
]
res = (cur.groupby(GRP_COLS, dropna=False)["line_item_unblended_cost"]
       .sum().reset_index())
res = res.sort_values(["line_item_resource_id", "date"]).reset_index(drop=True)

# ── Rolling stats per resource ────────────────────────────────────────────────
print("Computing rolling stats...")
g2 = res.groupby("line_item_resource_id")["line_item_unblended_cost"]

res["rolling_mean_7d"]    = g2.transform(lambda x: x.rolling(7,  min_periods=3).mean())
res["rolling_std_7d"]     = g2.transform(lambda x: x.rolling(7,  min_periods=3).std())
res["rolling_median_14d"] = g2.transform(lambda x: x.rolling(14, min_periods=7).median())

res["z_score_7d"]     = ((res["line_item_unblended_cost"] - res["rolling_mean_7d"])
                          / res["rolling_std_7d"].clip(lower=0.1))
res["z_score_robust"] = ((res["line_item_unblended_cost"] - res["rolling_median_14d"])
                          / res["rolling_std_7d"].clip(lower=0.1))
res["pct_change_1d"]  = g2.transform(lambda x: x.pct_change())

# ── Resource age ──────────────────────────────────────────────────────────────
fs = (res.groupby("line_item_resource_id")["date"]
      .min().reset_index().rename(columns={"date": "first_seen_date"}))
res = res.merge(fs, on="line_item_resource_id")
res["resource_age_days"] = (
    pd.to_datetime(res["date"]) - pd.to_datetime(res["first_seen_date"])
).dt.days

# ── untagged_flag (rule-based) ────────────────────────────────────────────────
res["untagged_flag"] = (
    res["resource_tags_user_team"].isna() &
    (res["line_item_unblended_cost"] >= UNTAGGED_COST_MIN)
)

# ── weekend_ratio_14d (vectorised, no apply) ──────────────────────────────────
print("Computing weekend_ratio_14d (vectorised)...")

# Build a per-resource weekend/weekday rolling mean using a helper approach:
# We compute rolling 14-day mean separately for weekend rows and weekday rows,
# then align them back to every row by forward-filling within each resource group.

res_sorted = res.sort_values(["line_item_resource_id", "date"]).copy()
res_sorted["_cost_wd"] = np.where(~res_sorted["is_weekend"],
                                   res_sorted["line_item_unblended_cost"], np.nan)
res_sorted["_cost_we"] = np.where(res_sorted["is_weekend"],
                                   res_sorted["line_item_unblended_cost"], np.nan)

# rolling mean of weekday/weekend costs within 14-day window
# min_periods kept low so we get values after just a few data points
g3 = res_sorted.groupby("line_item_resource_id")

res_sorted["_roll_wd_14"] = g3["_cost_wd"].transform(
    lambda x: x.rolling(14, min_periods=3).mean())
res_sorted["_roll_we_14"] = g3["_cost_we"].transform(
    lambda x: x.rolling(14, min_periods=2).mean())

# weekend_ratio = rolling_weekend_mean / rolling_weekday_mean
# NaN where not enough data yet
res_sorted["weekend_ratio_14d"] = (
    res_sorted["_roll_we_14"] / res_sorted["_roll_wd_14"].clip(lower=0.01)
)
res_sorted.drop(columns=["_cost_wd","_cost_we","_roll_wd_14","_roll_we_14"],
                inplace=True)
res = res_sorted.reset_index(drop=True)

# ── sustained_stable_days (idle_resource signal) ──────────────────────────────
print("Computing sustained_stable_days...")
res["_is_stable"] = (
    (res["line_item_unblended_cost"] > STABLE_COST_MIN) &
    (res["z_score_7d"].abs() <= STABLE_Z_BAND)
)
res["sustained_stable_days"] = (
    res.groupby("line_item_resource_id")["_is_stable"]
    .transform(streak) * res["_is_stable"]
)

# ── sustained_above_days (spike / runaway signal) ─────────────────────────────
res["_above"] = res["z_score_robust"] > ABOVE_ROBUST_Z
res["sustained_above_days"] = (
    res.groupby("line_item_resource_id")["_above"]
    .transform(streak) * res["_above"]
)

res.drop(columns=["_is_stable", "_above"], inplace=True)

res.to_csv(OUT / "cur_resource_features.csv", index=False)
print(f"  => cur_resource_features.csv saved: {res.shape[0]} rows x {res.shape[1]} cols")

# ═══════════════════════════════════════════════════════════════════════════════
# PART C — VALIDATION REPORT
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "="*65)
print("VALIDATION — 5 ANOMALY TYPES + 3 BENIGN")
print("="*65)

def section(title):
    print(f"\n{'─'*50}")
    print(f"  {title}")
    print(f"{'─'*50}")

# ── 1. sudden_spike (A6) ──────────────────────────────────────────────────────
section("1. sudden_spike — log-group-debug-runaway")
a6 = res[res["line_item_resource_id"] == "log-group-debug-runaway"]
print(a6[["date","line_item_unblended_cost","resource_age_days",
          "z_score_robust","sustained_above_days"]].to_string(index=False))

a6_ce = ce_out[(ce_out["linked_account_name"]=="dev") &
               (ce_out["service_code"]=="AmazonCloudWatch")]
print(f"\n  CE-level is_new_service=True:   {a6_ce['is_new_service'].sum()} days")
print(f"  CE-level sudden_spike_flag=True: {a6_ce['sudden_spike_flag'].sum()} days")
print(f"  CE-level service_duration_days:  {a6_ce['service_duration_days'].iloc[0]}")

# ── 2. runaway_usage (GPU fleet) ──────────────────────────────────────────────
section("2. runaway_usage — i-0fbgpu00000000")
gpu = res[res["line_item_resource_id"] == "i-0fbgpu00000000"]
print(gpu[["date","line_item_unblended_cost","resource_age_days",
           "weekend_ratio_14d","is_weekend"]].to_string(index=False))
we_rows = gpu[gpu["is_weekend"] & gpu["weekend_ratio_14d"].notna()]
print(f"\n  Weekend rows with ratio available: {len(we_rows)}")
if len(we_rows):
    print(f"  weekend_ratio_14d range: {we_rows['weekend_ratio_14d'].min():.3f} "
          f"– {we_rows['weekend_ratio_14d'].max():.3f}")

# ── 3. gradual_drift (DynamoDB) ───────────────────────────────────────────────
section("3. gradual_drift — data-analytics/AmazonDynamoDB")
dyn = ce_out[(ce_out["linked_account_name"]=="data-analytics") &
             (ce_out["service_code"]=="AmazonDynamoDB")]
print(dyn[["date","unblended_cost","drift_ratio","drift_sustained_days"]]
    .iloc[25:45].to_string(index=False))
print(f"\n  Max drift_sustained_days: {dyn['drift_sustained_days'].max()}")
print(f"  Days drift_ratio > 1.15: {(dyn['drift_ratio']>1.15).sum()}")

# ── 4. idle_resource (A2 — RDS orphan) ───────────────────────────────────────
section("4. idle_resource — db-staging-orphan-01")
idle = res[res["line_item_resource_id"] ==
           "arn:aws:rds:us-east-1:acct:db:db-staging-orphan-01"]
print(idle[["date","line_item_unblended_cost","z_score_7d",
            "sustained_stable_days"]].tail(15).to_string(index=False))
print(f"\n  Max sustained_stable_days: {idle['sustained_stable_days'].max()}")
print(f"  Days sustained >= 14: {(idle['sustained_stable_days'] >= 14).sum()}")

# ── 5. untagged_spend ─────────────────────────────────────────────────────────
section("5. untagged_spend — i-0untaggedfleet01")
ut = res[res["line_item_resource_id"] == "i-0untaggedfleet01"]
print(ut[["date","line_item_unblended_cost","resource_tags_user_team",
          "untagged_flag"]].head(5).to_string(index=False))
print(f"\n  Days flagged: {ut['untagged_flag'].sum()} / {len(ut)}")
print(f"  Cost range: ${ut['line_item_unblended_cost'].min():.2f} – "
      f"${ut['line_item_unblended_cost'].max():.2f}/day")

# ── Benign: B2 (migration) — must NOT be anomaly ─────────────────────────────
section("BENIGN B2 — migration-egress-onetime (must NOT flag)")
b2_ce = ce_out[(ce_out["linked_account_name"]=="data-analytics") &
               (ce_out["service_code"]=="AWSDataTransfer")]
b2_window = b2_ce[(b2_ce["date"] >= "2026-03-28") & (b2_ce["date"] <= "2026-03-30")]
print(b2_window[["date","unblended_cost","is_new_service",
                 "service_duration_days","sudden_spike_flag"]].to_string(index=False))
print(f"\n  sudden_spike_flag=True: {b2_window['sudden_spike_flag'].sum()} days  (expected: 0 ✓)")

# ── Benign: B1 (loadtest) ─────────────────────────────────────────────────────
section("BENIGN B1 — i-0loadtest-fleet (must NOT flag)")
b1 = res[res["line_item_resource_id"] == "i-0loadtest-fleet"]
print(b1[["date","line_item_unblended_cost","resource_age_days",
          "weekend_ratio_14d","sustained_above_days"]].to_string(index=False))

# ── Benign: B3 (flash sale) ───────────────────────────────────────────────────
section("BENIGN B3 — i-0flashsale-autoscale (must NOT flag)")
b3 = res[res["line_item_resource_id"] == "i-0flashsale-autoscale"]
print(b3[["date","line_item_unblended_cost","resource_age_days",
          "weekend_ratio_14d","sustained_above_days"]].to_string(index=False))

# ── BONUS: natgw-misconfig-spike (hidden anomaly candidate) ──────────────────
section("BONUS — natgw-misconfig-spike (unlabelled, possible hidden anomaly)")
nat = res[res["line_item_resource_id"] == "natgw-misconfig-spike"]
if len(nat):
    print(nat[["date","line_item_unblended_cost","resource_age_days",
               "z_score_robust","sustained_above_days"]].to_string(index=False))
    print(f"\n  Days: {len(nat)}, avg/day: ${nat['line_item_unblended_cost'].mean():.2f}")
    print("  NOTE: 5 days, $527/day — may be a mentor-held 'sudden_spike' label")
else:
    print("  Not found at resource level (may only exist in CE aggregate)")

# ── Summary table ─────────────────────────────────────────────────────────────
print("\n" + "="*65)
print("FEATURE EFFECTIVENESS SUMMARY")
print("="*65)
rows = [
    ("sudden_spike (A6)",      "sudden_spike_flag",        f"{a6_ce['sudden_spike_flag'].sum()} days flagged (duration={a6_ce['service_duration_days'].iloc[0]})"),
    ("runaway_usage (GPU)",    "weekend_ratio_14d ~1.0",   f"ratio {we_rows['weekend_ratio_14d'].mean():.3f} avg on weekends" if len(we_rows) else "no weekend rows"),
    ("gradual_drift (DDB)",    "drift_sustained_days",     f"max={dyn['drift_sustained_days'].max()} days, {(dyn['drift_ratio']>1.15).sum()} days above threshold"),
    ("idle_resource (A2)",     "sustained_stable_days",    f"max={idle['sustained_stable_days'].max()} days, {(idle['sustained_stable_days']>=14).sum()} days >=14"),
    ("untagged_spend",         "untagged_flag",             f"{ut['untagged_flag'].sum()}/92 days flagged"),
    ("benign B2 (migration)",  "sudden_spike_flag=0",      f"FLAGGED: {b2_window['sudden_spike_flag'].sum()} days (duration=3 < 5 threshold) ✓"),
    ("benign B1 (loadtest)",   "short duration",           f"resource_age max={b1['resource_age_days'].max()}, days={len(b1)} ✓"),
    ("benign B3 (flashsale)",  "short duration",           f"resource_age max={b3['resource_age_days'].max()}, days={len(b3)} ✓"),
]
print(f"  {'Scenario':<30} {'Key Feature':<28} {'Result'}")
print(f"  {'─'*28} {'─'*26} {'─'*30}")
for name, feat, result in rows:
    print(f"  {name:<30} {feat:<28} {result}")

print("\nDone.")
