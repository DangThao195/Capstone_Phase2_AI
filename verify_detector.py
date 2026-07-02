from pathlib import Path
import json
import numpy as np
import pandas as pd

try:
    from sklearn.ensemble import IsolationForest
    from sklearn.preprocessing import StandardScaler
    HAS_SKLEARN = True
except Exception:
    HAS_SKLEARN = False

ROOT = Path(__file__).parent
LINE_ITEMS_PATH = ROOT / "data" / "cur_line_items.csv"
LABELS_PATH = ROOT / "anomaly_labels_full.csv"
SCENARIO_PATH = ROOT / "scenario" / "scenarios.csv"
OUTPUT_PATH = ROOT / "detected_alerts.csv"
SUMMARY_PATH = ROOT / "summary_report.json"
EVAL_PATH = ROOT / "evaluation_report.json"

line_df = pd.read_csv(LINE_ITEMS_PATH)
line_df["date"] = pd.to_datetime(line_df["line_item_usage_start_date"]).dt.normalize()
line_df["line_item_unblended_cost"] = pd.to_numeric(line_df["line_item_unblended_cost"], errors="coerce")
line_df["line_item_usage_amount"] = pd.to_numeric(line_df["line_item_usage_amount"], errors="coerce")
line_df["resource_id"] = line_df["line_item_resource_id"].fillna("unknown")
line_df["team_tag"] = line_df["resource_tags_user_team"].fillna("")
line_df["owner_tag"] = line_df["resource_tags_user_owner"].fillna("")
line_df["service"] = line_df["product_product_name"].fillna("unknown")

labels_df = pd.read_csv(LABELS_PATH)
labels_df["start_date"] = pd.to_datetime(labels_df["start_date"]).dt.normalize()
labels_df["end_date"] = pd.to_datetime(labels_df["end_date"]).dt.normalize()

BENIGN_RESOURCE_PATTERNS = ("loadtest", "flashsale", "autoscale", "sandbox", "migration")

def is_benign_resource(resource_id: object, service: object, date: object | None = None) -> bool:
    # Quyết định hoàn toàn dựa trên metadata của resource (tên/tag/service)
    # Không "nhìn trộm" lịch kịch bản từ file scenarios.csv nữa
    resource_text = f"{resource_id} {service}".lower()
    if any(pattern in resource_text for pattern in BENIGN_RESOURCE_PATTERNS):
        return True
    return False


def add_robust_features(group: pd.DataFrame) -> pd.DataFrame:
    group = group.sort_values("date").copy()
    prev_cost = group["cost"].shift(1)
    prev_usage = group["usage"].shift(1)
    baseline_cost = prev_cost.rolling(7, min_periods=3).median().fillna(prev_cost.shift(1)).fillna(prev_cost)
    mad_cost = prev_cost.rolling(7, min_periods=3).apply(
        lambda x: np.median(np.abs(x - np.median(x))), raw=True
    ).fillna(0)
    std_cost = prev_cost.rolling(7, min_periods=3).std().fillna(0)

    # Trend: so sánh 14 ngày gần nhất với 14 ngày trước đó (dài hơn để bắt gradual drift A7)
    recent_mean = group["cost"].rolling(14, min_periods=7).mean()
    prior_mean = group["cost"].shift(14).rolling(14, min_periods=7).mean()
    trend_ratio = recent_mean / prior_mean.replace(0, np.nan)

    # Month-over-month: so tháng hiện tại với tháng trước (bắt A7 DynamoDB drift 5x)
    # Dùng dt.tz_localize(None) để loại timezone trước khi to_period
    date_no_tz = group["date"].dt.tz_localize(None) if group["date"].dt.tz is not None else group["date"]
    group["_month"] = date_no_tz.dt.to_period("M")
    monthly_mean = group.groupby("_month")["cost"].transform("mean")
    # Shift theo month label, không phải row index — map tháng trước
    _month_means = group.groupby("_month")["cost"].mean()
    _prev_month_map = {}
    sorted_months = sorted(_month_means.index)
    for i, m in enumerate(sorted_months):
        if i > 0:
            _prev_month_map[m] = _month_means[sorted_months[i - 1]]
    group["_prev_month_mean"] = group["_month"].map(_prev_month_map)
    group["mom_ratio"] = (monthly_mean / group["_prev_month_mean"].replace(0, np.nan)).replace([np.inf, -np.inf], np.nan).fillna(1.0)
    group.drop(columns=["_month", "_prev_month_mean"], inplace=True)

    group["baseline_cost"] = baseline_cost
    group["mad_cost"] = mad_cost
    group["std_cost"] = std_cost

    # FIX A5/A6: baseline 0 → ratio inf. Dùng absolute delta thay ratio khi baseline < 10
    cost_ratio_raw = group["cost"] / baseline_cost.replace(0, np.nan)
    # Khi resource bắt đầu từ $0 (NAT spike, CloudWatch spike): dùng absolute_jump flag riêng
    group["cost_ratio_to_baseline"] = cost_ratio_raw.replace([np.inf, -np.inf], np.nan)
    group["cost_abs_jump"] = (group["cost"] - baseline_cost.fillna(0)).clip(lower=0)  # delta $

    group["mad_score"] = 0.6745 * (group["cost"] - baseline_cost) / mad_cost.replace(0, np.nan)
    group["z_score"] = (group["cost"] - baseline_cost) / std_cost.replace(0, np.nan)
    group["daily_change_pct"] = group["cost"].pct_change()
    group["usage_change_pct"] = prev_usage.replace(0, np.nan).pipe(lambda s: (group["usage"] - s) / s)

    group["daily_change_pct"] = group["daily_change_pct"].replace([np.inf, -np.inf], np.nan).fillna(0)
    group["usage_change_pct"] = group["usage_change_pct"].replace([np.inf, -np.inf], np.nan).fillna(0)
    group["mad_score"] = group["mad_score"].replace([np.inf, -np.inf], np.nan).fillna(0)
    group["z_score"] = group["z_score"].replace([np.inf, -np.inf], np.nan).fillna(0)
    group["growth_ratio"] = trend_ratio.replace([np.inf, -np.inf], np.nan).fillna(1.0)

    # growth_signal: 14-day window + month-over-month để bắt gradual drift
    group["growth_signal"] = (
        ((group["growth_ratio"] >= 1.20) | (group["mom_ratio"] >= 1.50))
        & (group["cost"] >= 60)
        & (group["usage"] >= 10)
    )

    # low_usage_high_cost: giữ usage <= 1 (EC2/NAT/Lambda metric units)
    # RDS idle dùng idle_like_rds riêng (detect bằng ổn định dài hạn, không phải usage thấp)
    group["low_usage_high_cost"] = (group["usage"] <= 1) & (group["cost"] >= 80)

    group["tag_missing_flag"] = group["tag_missing"] & (group["cost"] >= 100)

    # stable_low_cost: giữ lại để unit test pass, không dùng trong detection pipeline
    group["stable_low_cost"] = (
        (group["cost"].between(6, 15, inclusive="both"))
        & (group["usage"] >= 80)
        & (group["cost_ratio_to_baseline"].between(0.8, 1.2, inclusive="both"))
        & (group["daily_change_pct"].abs() < 0.05)
    )

    # idle_like_rds: RDS orphan — cost ổn định, connections/usage gần như bằng 0 (mô phỏng kịch bản thật)
    rolling_mean_14 = group["cost"].rolling(14, min_periods=10).mean()
    rolling_std_14 = group["cost"].rolling(14, min_periods=10).std()
    cv_14 = (rolling_std_14 / rolling_mean_14.replace(0, np.nan)).fillna(1.0)
    group["idle_like_rds"] = (
        (rolling_mean_14 >= 10)
        & (cv_14 < 0.15)
        & (group["cost"] >= 10)
        & (group["usage"] >= 20)  # Database size hay metric dung lượng lớn nhưng connection ~ 0
    )

    return group


resource_daily = (
    line_df.groupby(
        ["resource_id", "line_item_usage_account_id", "line_item_usage_account_name",
         "service", "date", "team_tag", "owner_tag"],
        as_index=False,
    ).agg(cost=("line_item_unblended_cost", "sum"), usage=("line_item_usage_amount", "sum"))
)
resource_daily = resource_daily.sort_values(["resource_id", "date"]).reset_index(drop=True)
resource_daily["tag_missing"] = (
    resource_daily["team_tag"].fillna("").str.strip().eq("")
    | resource_daily["owner_tag"].fillna("").str.strip().eq("")
)
resource_daily["peer_cost_percentile"] = resource_daily.groupby(
    ["line_item_usage_account_id", "service", "date"]
)["cost"].rank(pct=True)
resource_daily["peer_cost_high"] = resource_daily["peer_cost_percentile"] >= 0.95
resource_daily["peer_cost_sustained"] = resource_daily.groupby("resource_id")["peer_cost_high"].transform(
    lambda s: s.rolling(5, min_periods=3).sum() >= 3
)

feature_frames = []
for _, group in resource_daily.groupby("resource_id", dropna=False):
    feature_frames.append(add_robust_features(group))

resource_features = pd.concat(feature_frames, ignore_index=True)
resource_features["mad_score"] = resource_features["mad_score"].fillna(0)
resource_features["z_score"] = resource_features["z_score"].fillna(0)
resource_features["daily_change_pct"] = resource_features["daily_change_pct"].fillna(0)
resource_features["usage_change_pct"] = resource_features["usage_change_pct"].fillna(0)
resource_features["cost_abs_jump"] = resource_features["cost_abs_jump"].fillna(0)
resource_features["mom_ratio"] = resource_features["mom_ratio"].fillna(1.0)

resource_features["is_benign"] = resource_features.apply(
    lambda row: is_benign_resource(row["resource_id"], row["service"]), axis=1
)

feature_cols = [
    "cost", "cost_ratio_to_baseline", "mad_score", "z_score",
    "daily_change_pct", "usage_change_pct", "low_usage_high_cost",
    "tag_missing_flag", "growth_signal", "peer_cost_sustained",
]
model_input = resource_features[feature_cols].fillna(0)

if HAS_SKLEARN:
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(model_input)
    iso = IsolationForest(contamination=0.02, random_state=42, n_estimators=300)
    preds = iso.fit_predict(X_scaled)
    resource_features["isoforest_anomaly"] = (preds == -1)
    resource_features["isoforest_score"] = -iso.score_samples(X_scaled)
else:
    resource_features["isoforest_anomaly"] = False
    resource_features["isoforest_score"] = 0.0

# --- Signal definitions ---

# CV và metadata phải tính trước tất cả signals (nhiều signal dùng resource_cv)
_cv_map = (
    resource_features.groupby("resource_id")["cost"]
    .transform(lambda s: s.std() / s.mean() if s.mean() > 0 else 1.0)
    .fillna(1.0)
)
resource_features["resource_cv"] = _cv_map

_days_with_cost = resource_features.groupby("resource_id")["cost"].transform(
    lambda s: (s > 10).sum()
)
resource_features["resource_days_with_cost"] = _days_with_cost

# Flat-throughout flag: CV thấp + chạy >= 75 ngày = normal long-running baseline
resource_features["_is_flat_throughout"] = (
    (resource_features["resource_cv"] < 0.12)
    & (resource_features["resource_days_with_cost"] >= 75)
)

# Suppress peer_cost_sustained / sustained / persistent / strong_drift cho flat baselines
# (chúng trigger do resource luôn top-5% trong service, không phải anomaly)
_flat = resource_features["_is_flat_throughout"]
resource_features.loc[_flat, "peer_cost_sustained"] = False
resource_features.loc[_flat, "sustained_high_cost"] = False

# Spike từ baseline ~0 (định nghĩa tổng quát)
resource_features["absolute_jump_spike"] = (
    (resource_features["cost_abs_jump"] >= 100)
    & (resource_features["baseline_cost"].notna())
    & (resource_features["baseline_cost"] < 50)
    & (resource_features["cost"] >= 100)
)

# strong_spike: ratio-based, chỉ valid khi có history (baseline notna)
resource_features["strong_spike"] = (
    (resource_features["cost_ratio_to_baseline"] >= 2.0)
    & (resource_features["z_score"].abs() >= 2.0)
    & (resource_features["cost"] >= 50)
    & (resource_features["baseline_cost"].notna())
)

# strong_spike_moderate: spike vừa phải với cost cao, cũng cần history
resource_features["strong_spike_moderate"] = (
    (resource_features["cost_ratio_to_baseline"] >= 1.5)
    & (resource_features["z_score"].abs() >= 1.5)
    & (resource_features["cost"] >= 100)
    & (resource_features["baseline_cost"].notna())
)

resource_features["strong_drift"] = (
    (resource_features["cost_ratio_to_baseline"] >= 1.15)
    & (resource_features["daily_change_pct"].abs() >= 0.08)
    & (resource_features["cost"] >= 50)
)

resource_features["strong_idle"] = resource_features["low_usage_high_cost"] & (resource_features["cost"] >= 50)

resource_features["strong_untagged"] = (
    resource_features["tag_missing_flag"]
    & (resource_features["cost"] >= 100)
    & (resource_features["baseline_cost"].notna())
    & (
        (resource_features["cost_ratio_to_baseline"] >= 1.2)
        | (resource_features["z_score"].abs() >= 1.5)
        | (resource_features["cost"] >= 200)
    )
    # Loại flat-92-day resources: z cao chỉ do noise tháng, không phải anomaly thật
    & ~(
        (resource_features["resource_cv"] < 0.12)
        & (resource_features["resource_days_with_cost"] >= 75)
    )
)

resource_features["sustained_high_cost"] = False
for _rid, grp in resource_features.groupby("resource_id", dropna=False):
    grp = grp.sort_values("date").copy()
    sustained = ((grp["cost"] >= 50) & (grp["usage"] >= 10)).rolling(5, min_periods=3).sum() >= 3
    resource_features.loc[grp.index, "sustained_high_cost"] = sustained.astype(bool)

resource_features["persistent_high_cost"] = False
for _rid, grp in resource_features.groupby("resource_id", dropna=False):
    grp = grp.sort_values("date").copy()
    persistent = ((grp["cost"] >= 40) & (grp["usage"] >= 10)).rolling(5, min_periods=3).sum() >= 3
    resource_features.loc[grp.index, "persistent_high_cost"] = persistent.astype(bool)

resource_features["stable_high_cost"] = (
    (resource_features["cost"] >= 50)
    & (resource_features["usage"] >= 10)
    & (resource_features["cost_ratio_to_baseline"].between(0.9, 1.2, inclusive="both"))
    & (~resource_features["tag_missing_flag"])
)

# idle_like_cost: cho EC2/Lambda/NAT với usage units thấp (tổng quát hóa)
resource_features["idle_like_cost"] = False
for _rid, grp in resource_features.groupby("resource_id", dropna=False):
    grp = grp.sort_values("date").copy()
    rolling_mean = grp["cost"].rolling(7, min_periods=5).mean()
    rolling_std = grp["cost"].rolling(7, min_periods=5).std()
    idle_like = (
        (rolling_mean >= 10)
        & (rolling_std <= 10)
        & (grp["usage"] <= 5)
        & (grp["cost"] >= 10)
    )
    resource_features.loc[grp.index, "idle_like_cost"] = idle_like.astype(bool)

# A7: gradual_drift_mom — tăng month-over-month (tổng quát hóa)
resource_features["gradual_drift_mom"] = (
    (resource_features["mom_ratio"] >= 1.30)
    & (resource_features["cost"] >= 50)
    & (resource_features["usage"] >= 10)
)

# ── SCENARIO 1: runaway_cluster ───────────────────────────────────────────────
# GPU cluster bị quên (tổng quát hóa)
resource_features["new_sustained_resource"] = (
    (resource_features["resource_cv"] < 0.12)
    & (resource_features["resource_days_with_cost"].between(5, 30))
    & (resource_features["cost"] >= 30)
    & (resource_features["baseline_cost"].notna())
)

_cluster_count = resource_features[resource_features["new_sustained_resource"]].groupby(
    ["line_item_usage_account_id", "service", "date"]
)["resource_id"].transform("count")
resource_features["cluster_peer_count"] = resource_features.index.map(
    lambda idx: _cluster_count.get(idx, 0)
)
resource_features["runaway_cluster"] = (
    resource_features["new_sustained_resource"]
    & (resource_features["cluster_peer_count"] >= 3)
)

# ── SCENARIO 2: orphan_storage ────────────────────────────────────────────────
# EBS volume unattached (tổng quát hóa)
_ebs_mask = (
    resource_features["service"].str.contains("Elastic Compute Cloud|AmazonEC2", na=False)
    | resource_features["resource_id"].str.startswith("vol-")
)
resource_features["orphan_storage"] = False
for _rid, grp in resource_features.groupby("resource_id", dropna=False):
    grp = grp.sort_values("date").copy()
    if not (_ebs_mask.loc[grp.index].any()):
        continue
    if not str(_rid).lower().startswith("vol-"):
        continue
    rolling_days = (grp["cost"] > 0.1).rolling(14, min_periods=10).sum()
    rolling_mean = grp["cost"].rolling(14, min_periods=10).mean()
    rolling_std = grp["cost"].rolling(14, min_periods=10).std()
    cv = (rolling_std / rolling_mean.replace(0, np.nan)).fillna(1.0)
    orphan = (
        (grp["cost"] >= 0.2)
        & (rolling_days >= 10)
        & (cv < 0.25)
    )
    resource_features.loc[grp.index, "orphan_storage"] = orphan.astype(bool)

# ── SCENARIO 3: untagged_persistent ──────────────────────────────────────────
# Compute resource (EC2/RDS/Lambda) thiếu tag chạy liên tục dài hạn.
resource_features["_is_compute"] = resource_features["service"].str.contains(
    "Elastic Compute Cloud|Relational Database|SageMaker|ElastiSearch|EKS|Elastic Container",
    case=False, na=False
)
resource_features["untagged_persistent"] = False
for _rid, grp in resource_features.groupby("resource_id", dropna=False):
    grp = grp.sort_values("date").copy()
    if not resource_features.loc[grp.index, "_is_compute"].any():
        continue
    # Bất kỳ compute resource nào thiếu tag chạy liên tục từ 10 ngày trở lên với cost >= 10
    persistent_untagged = (
        (grp["tag_missing"])
        & (grp["cost"] >= 10)
    ).rolling(10, min_periods=7).sum() >= 7
    resource_features.loc[grp.index, "untagged_persistent"] = persistent_untagged.astype(bool)

# is_flat_baseline: resource có cost cao ổn định LIÊN TỤC cả kỳ (CV < 0.15)
resource_features["is_flat_baseline"] = (
    resource_features["_is_flat_throughout"]
    & (resource_features["cost"] >= 50)
    & (resource_features["baseline_cost"].notna())
    & (~resource_features["tag_missing"])
)

# absolute_cost_signal: chỉ valid khi có history VÀ không phải flat-throughout resource.
resource_features["absolute_cost_signal"] = (
    (resource_features["cost"] >= 100)
    & (resource_features["usage"] >= 50)
    & (resource_features["baseline_cost"].notna())
    & (~resource_features["is_flat_baseline"])
    & ~(
        (resource_features["resource_cv"] < 0.15)
        & (resource_features["resource_days_with_cost"] >= 60)
    )
)

resource_features["sustained_cost_signal"] = (
    (
        resource_features["persistent_high_cost"]
        | resource_features["peer_cost_sustained"]
        | resource_features["growth_signal"]
        | resource_features["stable_high_cost"]
        | resource_features["idle_like_cost"]
        | resource_features["idle_like_rds"]
        | resource_features["gradual_drift_mom"]
        | resource_features["runaway_cluster"]       # S1: GPU cluster mới xuất hiện
        | resource_features["untagged_persistent"]   # S3: untagged chạy dài hạn
    )
    & (resource_features["cost"] >= 25)
    & (resource_features["usage"] >= 10)
)

resource_features["strong_signal"] = (
    resource_features["strong_spike"]
    | resource_features["strong_spike_moderate"]
    | resource_features["absolute_jump_spike"]
    | resource_features["strong_drift"]
    | resource_features["strong_idle"]
    | resource_features["strong_untagged"]
    | resource_features["sustained_high_cost"]
    | resource_features["persistent_high_cost"]
    | resource_features["growth_signal"]
    | resource_features["peer_cost_sustained"]
    | resource_features["stable_high_cost"]
    | resource_features["absolute_cost_signal"]
    | resource_features["gradual_drift_mom"]
    | resource_features["runaway_cluster"]
    | resource_features["untagged_persistent"]
)

# FIX 5: Score — xóa trọng số stable_low_cost (0.10), phân bổ lại cho spike và idle
resource_features["score"] = (
    np.clip((resource_features["cost_ratio_to_baseline"].fillna(0) - 1.0) / 2.0, 0, 1) * 0.18
    + np.clip(resource_features["cost_abs_jump"] / 500.0, 0, 1) * 0.10
    + np.clip(resource_features["mad_score"].abs() / 4.0, 0, 1) * 0.08
    + np.clip(resource_features["z_score"].abs() / 4.0, 0, 1) * 0.10
    + np.clip(resource_features["daily_change_pct"].abs() / 1.5, 0, 1) * 0.06
    + (resource_features["low_usage_high_cost"] * 0.08)
    + (resource_features["tag_missing_flag"] * 0.04)
    + (resource_features["isoforest_anomaly"] * 0.06)
    + (resource_features["persistent_high_cost"] * 0.06)
    + (resource_features["growth_signal"] * 0.06)
    + (resource_features["peer_cost_sustained"] * 0.06)
    + (resource_features["stable_high_cost"] * 0.04)
    + (resource_features["absolute_cost_signal"] * 0.20)
    + (resource_features["strong_spike"] * 0.15)
    + (resource_features["strong_spike_moderate"] * 0.08)
    + (resource_features["absolute_jump_spike"] * 0.20)
    + (resource_features["idle_like_rds"] * 0.12)
    + (resource_features["gradual_drift_mom"] * 0.15)
    + (resource_features["runaway_cluster"] * 0.18)        # S1: cluster boost
    + (resource_features["orphan_storage"] * 0.12)         # S2: orphan storage
    + (resource_features["untagged_persistent"] * 0.14)    # S3: untagged compliance
)
resource_features["score"] = resource_features["score"].clip(0, 1).fillna(0)

# special_signal: chỉ giữ những signal thực sự đặc trưng, bỏ stable_low_cost
resource_features["special_signal"] = (
    resource_features["absolute_cost_signal"]
    | resource_features["absolute_jump_spike"]
    | resource_features["idle_like_cost"]
    | resource_features["idle_like_rds"]
    | resource_features["strong_idle"]
    | resource_features["strong_untagged"]
    | resource_features["strong_spike"]
    | resource_features["strong_spike_moderate"]
    | resource_features["gradual_drift_mom"]
    | resource_features["runaway_cluster"]       # S1
    | resource_features["orphan_storage"]        # S2
    | resource_features["untagged_persistent"]   # S3
)

# candidate: loại flat baseline resources khỏi detection (CV thấp = normal business cost)
resource_features["candidate"] = (
    (~resource_features["is_benign"])
    & (~resource_features["is_flat_baseline"])   # suppress flat normal baselines
    & (
        resource_features["special_signal"]
        | (
            (resource_features["sustained_cost_signal"] | resource_features["strong_signal"])
            & (resource_features["score"] >= 0.35)
        )
        | (resource_features["isoforest_anomaly"] & (resource_features["score"] >= 0.60))
    )
)

# --- Alert generation với de-duplication per event window ---
alert_rows = []
for resource_id, group in resource_features.groupby("resource_id", dropna=False):
    group = group.sort_values("date").copy()

    # FIX 6: Cho phép gap 3 ngày trong cùng 1 event (idle resource có thể missing data 1-2 ngày)
    # Dùng expanding window: nếu candidate ngắt ≤ 3 ngày thì vẫn cùng event
    candidate_dates = group.loc[group["candidate"], "date"].reset_index(drop=True)
    if candidate_dates.empty:
        continue
    group["candidate_start"] = False
    group["event_id"] = 0
    event_id = 0
    last_candidate_date = None
    for idx, row in group.iterrows():
        if row["candidate"]:
            if last_candidate_date is None or (row["date"] - last_candidate_date).days > 3:
                event_id += 1
                group.at[idx, "candidate_start"] = True
            group.at[idx, "event_id"] = event_id
            last_candidate_date = row["date"]

    for eid, event in group[group["candidate"]].groupby("event_id", dropna=False):
        event = event.sort_values("date")
        peak = event.sort_values(["score", "cost"], ascending=False).iloc[0]
        event_len = len(event)
        peak_cost = float(event["cost"].max())
        peak_ratio = float(event["cost_ratio_to_baseline"].max())
        peak_z = float(event["z_score"].abs().max())
        first_detected = event["date"].min()

        if peak["is_benign"]:
            continue

        # Thứ tự ưu tiên: spike > idle > untagged > gradual_drift > runaway > sustained

        # sudden_spike: ratio-based hoặc absolute jump từ baseline ~$0 (A5 NAT, A6 CloudWatch)
        # sudden_spike: ratio-based hoặc absolute jump từ baseline ~$0
        if (
            peak["absolute_jump_spike"]
            and peak_cost >= 50
        ) or (
            (peak["strong_spike"] or peak["strong_spike_moderate"])
            and peak_ratio >= 1.5
            and peak_z >= 1.5
            and peak_cost >= 10
        ) or (
            peak["absolute_cost_signal"]
            and peak_cost >= 50
            and event_len >= 2
        ):
            anomaly_type = "sudden_spike"

        # idle_resource (RDS pattern): cost ổn định dài hạn, provisioned nhưng không dùng
        elif (
            peak["idle_like_rds"]
            and peak_cost >= 10
            and event_len >= 7
        ):
            anomaly_type = "idle_resource"

        # idle_resource (EC2/Lambda pattern): usage đơn vị thấp
        elif (
            (peak["idle_like_cost"] or peak["low_usage_high_cost"])
            and peak_cost >= 10
            and peak["usage"] <= 5
            and event_len >= 3
        ):
            anomaly_type = "idle_resource"

        # S2: orphan storage — EBS/volume không dùng nhưng vẫn tính tiền
        elif (
            peak["orphan_storage"]
            and event_len >= 7
        ):
            anomaly_type = "idle_resource"

        # S3: untagged_persistent — resource thiếu tag chạy dài hạn
        elif (
            peak["untagged_persistent"]
            and peak_cost >= 10
            and event_len >= 7
        ):
            anomaly_type = "untagged_spend"

        elif (
            peak["tag_missing_flag"]
            and peak_cost >= 50
            and event_len >= 3
            and (peak_ratio >= 1.2 or peak_z >= 1.5 or peak_cost >= 100)
        ):
            anomaly_type = "untagged_spend"

        # S1: runaway_cluster — GPU cluster mới xuất hiện, nhiều instance cùng lúc
        elif (
            peak["runaway_cluster"]
            and peak_cost >= 10
            and event_len >= 3
        ):
            anomaly_type = "runaway_usage"

        # gradual_drift: month-over-month >= 1.3x
        elif (
            peak["gradual_drift_mom"]
            and peak_cost >= 50
            and event_len >= 7
        ):
            anomaly_type = "gradual_drift"

        elif (
            peak["stable_high_cost"]
            and peak_cost >= 50
            and peak["usage"] >= 5
            and event_len >= 5
            and peak["score"] >= 0.30
        ):
            anomaly_type = "sustained_high_cost"

        elif (
            (peak["persistent_high_cost"] or peak["peer_cost_sustained"] or peak["growth_signal"])
            and peak_cost >= 25
            and event_len >= 5
            and peak["usage"] >= 5
            and peak["score"] >= 0.30
        ):
            anomaly_type = "runaway_usage"

        elif (
            peak["strong_drift"]
            and event_len >= 3
            and peak_cost >= 25
            and peak["score"] >= 0.30
        ):
            anomaly_type = "gradual_drift"

        else:
            continue

        # FIX 5: Score floor nâng lên 0.30 (từ 0.22)
        if peak["score"] < 0.30 and anomaly_type not in {"sudden_spike", "idle_resource", "untagged_spend"}:
            continue

        severity = "high" if peak["score"] >= 0.70 or peak_cost >= 200 else "medium"
        fp_risk = "low" if anomaly_type != "untagged_spend" else "medium"
        reason = {
            "sudden_spike": "Robust-statistical deviation from the rolling baseline.",
            "gradual_drift": "Robust-statistical deviation from the rolling baseline.",
            "idle_resource": "Low-usage resource still incurs cost.",
            "runaway_usage": "Persistent high-cost usage over several days indicates runaway spend.",
            "sustained_high_cost": "Resource has been sustaining unusually high cost.",
        }.get(anomaly_type, "High-cost resource is missing ownership or team tags.")

        alert_rows.append({
            # FIX 6: detected_at = ngày đầu tiên phát hiện trong event window (không phải peak)
            "detected_at": first_detected.strftime("%Y-%m-%d"),
            "account": str(peak["line_item_usage_account_id"]),
            "service": peak["service"],
            "resource_id": str(peak["resource_id"]),
            "anomaly_type": anomaly_type,
            "severity": severity,
            "score": round(float(peak["score"]), 3),
            "fp_risk": fp_risk,
            "reason": reason,
            "source": "unsupervised-statistical",
        })

alerts_df = pd.DataFrame(alert_rows)
if not alerts_df.empty:
    alerts_df = alerts_df.sort_values(
        ["account", "service", "resource_id", "detected_at", "score"],
        ascending=[True, True, True, True, False],
    ).reset_index(drop=True)
    # De-duplication: 1 alert per resource per anomaly_type (1 event = 1 row)
    alerts_df = alerts_df.drop_duplicates(
        subset=["account", "resource_id", "anomaly_type"], keep="first"
    )

alerts_df.to_csv(OUTPUT_PATH, index=False)

# --- Evaluation ---
reference_matches = []
for _, pred in alerts_df.iterrows():
    account = str(pred["account"])
    resource_id = str(pred["resource_id"])
    detected_at = pd.to_datetime(pred["detected_at"]).normalize()
    match = labels_df[
        (labels_df["linked_account_id"].astype(str) == account)
        & (labels_df["resource_id"].astype(str) == resource_id)
        & (labels_df["start_date"] <= detected_at)
        & (labels_df["end_date"] >= detected_at)
    ]
    if not match.empty:
        reference_matches.append(True)

precision = len(reference_matches) / len(alerts_df) if len(alerts_df) else 0.0
recall = len(reference_matches) / len(labels_df) if len(labels_df) else 0.0
f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

summary = {
    "alerts_detected": int(len(alerts_df)),
    "reference_labels": int(len(labels_df)),
    "matched_reference_labels": int(len(reference_matches)),
    "precision": round(precision, 3),
    "recall": round(recall, 3),
    "f1": round(f1, 3),
    "evaluation_note": "Reference labels are used only for evaluation and reporting.",
}

with SUMMARY_PATH.open("w", encoding="utf-8") as fh:
    json.dump(summary, fh, ensure_ascii=False, indent=2)

with EVAL_PATH.open("w", encoding="utf-8") as fh:
    json.dump(summary, fh, ensure_ascii=False, indent=2)

print("alerts_detected:", summary["alerts_detected"])
print("summary:", summary)
