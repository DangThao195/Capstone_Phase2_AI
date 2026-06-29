from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


SEED = 20260627
OUTPUT_DIR_NAME = "cdo_like_validation_v1"
SUSPICIOUS_KEYWORDS = (
    "orphan",
    "debug",
    "migration",
    "flashsale",
    "fbgpu",
    "untagged",
    "runaway",
    "spike",
)


@dataclass
class EventSpec:
    anomaly_id: str
    label: str
    anomaly_type: str
    account_name: str
    service_code: str
    start_date: str
    end_date: str
    description: str
    cost_multipliers: list[float]
    usage_multipliers: list[float]
    blank_team: bool = False
    blank_owner: bool = False
    blank_cost_center: bool = False


def first_non_empty(values: pd.Series, default: str = "us-east-1") -> str:
    for value in values:
        if pd.notna(value) and str(value).strip():
            return str(value).strip()
    return default


def normalize_cur(cur: pd.DataFrame) -> pd.DataFrame:
    cur = cur.copy()
    cur["bill_billing_period_start_date"] = pd.to_datetime(cur["bill_billing_period_start_date"], utc=True)
    cur["line_item_usage_start_date"] = pd.to_datetime(cur["line_item_usage_start_date"], utc=True)
    cur["line_item_usage_end_date"] = pd.to_datetime(cur["line_item_usage_end_date"], utc=True)
    cur["usage_date"] = cur["line_item_usage_start_date"].dt.tz_localize(None).dt.normalize()
    return cur


def normalize_ce(ce: pd.DataFrame) -> pd.DataFrame:
    ce = ce.copy()
    ce["date"] = pd.to_datetime(ce["date"]).dt.normalize()
    ce["is_estimated"] = ce["is_estimated"].astype(str).str.lower().eq("true")
    return ce


def normalize_labels(labels: pd.DataFrame) -> pd.DataFrame:
    labels = labels.copy()
    labels["start_date"] = pd.to_datetime(labels["start_date"]).dt.normalize()
    labels["end_date"] = pd.to_datetime(labels["end_date"]).dt.normalize()
    return labels


def load_base_metrics(metrics_dir: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in sorted(metrics_dir.glob("*.csv")):
        frame = pd.read_csv(path, parse_dates=["timestamp"])
        frame["usage_date"] = frame["timestamp"].dt.tz_localize(None).dt.normalize()
        frame["metric_source"] = path.stem
        frames.append(frame)
    if not frames:
        raise RuntimeError(f"No metrics files found under {metrics_dir}")
    return pd.concat(frames, ignore_index=True)


def metric_target_file(service_code: str) -> str:
    mapping = {
        "AmazonEC2": "ec2_metrics.csv",
        "AmazonRDS": "rds_metrics.csv",
        "AmazonSageMaker": "sagemaker_metrics.csv",
        "AmazonDynamoDB": "ddb_metrics.csv",
    }
    return mapping.get(service_code, "other_services_metrics.csv")


def build_protected_resources(labels: pd.DataFrame) -> set[str]:
    return set(labels["resource_id"].astype(str))


def has_full_window(cur: pd.DataFrame, resource_id: str, start_date: str, end_date: str) -> bool:
    expected = set(pd.date_range(start_date, end_date, freq="D"))
    actual = set(cur.loc[cur["line_item_resource_id"] == resource_id, "usage_date"])
    return expected.issubset(actual)


def choose_resource(
    cur: pd.DataFrame,
    account_name: str,
    service_code: str,
    excluded_resources: set[str],
    start_date: str,
    end_date: str,
) -> str:
    mask = (
        cur["line_item_usage_account_name"].eq(account_name)
        & cur["line_item_product_code"].eq(service_code)
    )
    candidates = (
        cur.loc[mask]
        .groupby("line_item_resource_id", as_index=False)["line_item_unblended_cost"]
        .agg(["count", "sum"])
        .reset_index()
        .sort_values(["sum", "count", "line_item_resource_id"], ascending=[False, False, True])
    )
    for resource_id in candidates["line_item_resource_id"].astype(str):
        lowered = resource_id.lower()
        if resource_id in excluded_resources:
            continue
        if any(keyword in lowered for keyword in SUSPICIOUS_KEYWORDS):
            continue
        if has_full_window(cur, resource_id, start_date, end_date):
            return resource_id
    raise RuntimeError(f"No stable resource found for {account_name} / {service_code} / {start_date}..{end_date}")


def apply_event(cur: pd.DataFrame, resource_id: str, spec: EventSpec) -> int:
    dates = list(pd.date_range(spec.start_date, spec.end_date, freq="D"))
    if len(dates) != len(spec.cost_multipliers) or len(dates) != len(spec.usage_multipliers):
        raise ValueError(f"Multiplier length mismatch for {spec.anomaly_id}")

    cost_map = {date: value for date, value in zip(dates, spec.cost_multipliers)}
    usage_map = {date: value for date, value in zip(dates, spec.usage_multipliers)}
    mask = cur["line_item_resource_id"].eq(resource_id) & cur["usage_date"].isin(dates)

    cur.loc[mask, "line_item_unblended_cost"] = (
        cur.loc[mask, "line_item_unblended_cost"] * cur.loc[mask, "usage_date"].map(cost_map).astype(float)
    ).round(4)
    cur.loc[mask, "line_item_usage_amount"] = (
        cur.loc[mask, "line_item_usage_amount"] * cur.loc[mask, "usage_date"].map(usage_map).astype(float)
    ).round(4)

    if spec.blank_team:
        cur.loc[mask, "resource_tags_user_team"] = ""
    if spec.blank_owner:
        cur.loc[mask, "resource_tags_user_owner"] = ""
    if spec.blank_cost_center:
        cur.loc[mask, "resource_tags_user_cost_center"] = ""

    return int(mask.sum())


def build_generated_label(cur: pd.DataFrame, resource_id: str, spec: EventSpec) -> dict[str, Any]:
    mask = (
        cur["line_item_resource_id"].eq(resource_id)
        & cur["usage_date"].between(pd.Timestamp(spec.start_date), pd.Timestamp(spec.end_date))
    )
    rows = cur.loc[mask].copy()
    if rows.empty:
        raise RuntimeError(f"No rows found to label for {spec.anomaly_id} / {resource_id}")

    total_cost = round(float(rows["line_item_unblended_cost"].sum()), 2)
    day_count = int(rows["usage_date"].nunique())
    first_row = rows.sort_values("usage_date").iloc[0]
    return {
        "anomaly_id": spec.anomaly_id,
        "label": spec.label,
        "anomaly_type": spec.anomaly_type,
        "linked_account_id": int(first_row["line_item_usage_account_id"]),
        "linked_account_name": str(first_row["line_item_usage_account_name"]),
        "service": str(first_row["line_item_product_code"]),
        "resource_id": resource_id,
        "start_date": pd.Timestamp(spec.start_date).date().isoformat(),
        "end_date": pd.Timestamp(spec.end_date).date().isoformat(),
        "approx_daily_cost": round(total_cost / max(day_count, 1), 2),
        "approx_total_cost": total_cost,
        "description": spec.description,
    }


def build_ce_from_cur(cur: pd.DataFrame, base_ce: pd.DataFrame) -> pd.DataFrame:
    service_map = (
        base_ce.sort_values(["service_code", "date"])
        .drop_duplicates("service_code")
        .set_index("service_code")["service"]
        .to_dict()
    )
    group = (
        cur.groupby(
            ["usage_date", "line_item_usage_account_id", "line_item_usage_account_name", "line_item_product_code"],
            as_index=False,
        )
        .agg(
            unblended_cost=("line_item_unblended_cost", "sum"),
            region=("product_region_code", first_non_empty),
        )
        .rename(
            columns={
                "usage_date": "date",
                "line_item_usage_account_id": "linked_account_id",
                "line_item_usage_account_name": "linked_account_name",
                "line_item_product_code": "service_code",
            }
        )
    )
    group["service"] = group["service_code"].map(service_map).fillna(group["service_code"])
    group["region"] = group["region"].fillna("us-east-1")
    group["unblended_cost"] = group["unblended_cost"].round(2)
    group["is_estimated"] = False
    return group[
        ["date", "linked_account_id", "linked_account_name", "service", "service_code", "region", "unblended_cost", "is_estimated"]
    ].sort_values(["date", "linked_account_id", "service_code"]).reset_index(drop=True)


def label_lookup_map(labels: pd.DataFrame) -> dict[tuple[str, pd.Timestamp], str]:
    mapping: dict[tuple[str, pd.Timestamp], str] = {}
    for row in labels.itertuples(index=False):
        for date in pd.date_range(row.start_date, row.end_date, freq="D"):
            mapping[(str(row.resource_id), pd.Timestamp(date))] = str(row.label)
    return mapping


def build_metrics_from_truth(
    base_metrics: pd.DataFrame,
    base_cur: pd.DataFrame,
    cur_truth: pd.DataFrame,
    extended_labels: pd.DataFrame,
    rng: np.random.Generator,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    base_daily = (
        base_cur.groupby(["line_item_resource_id", "usage_date"], as_index=False)
        .agg(
            base_cost=("line_item_unblended_cost", "sum"),
            base_usage=("line_item_usage_amount", "sum"),
            service_code=("line_item_product_code", first_non_empty),
        )
    )
    truth_daily = (
        cur_truth.groupby(["line_item_resource_id", "usage_date"], as_index=False)
        .agg(
            truth_cost=("line_item_unblended_cost", "sum"),
            truth_usage=("line_item_usage_amount", "sum"),
            service_code_truth=("line_item_product_code", first_non_empty),
        )
    )
    metrics = base_metrics.merge(
        base_daily,
        left_on=["resource_id", "usage_date"],
        right_on=["line_item_resource_id", "usage_date"],
        how="left",
    ).merge(
        truth_daily,
        left_on=["resource_id", "usage_date"],
        right_on=["line_item_resource_id", "usage_date"],
        how="left",
    )
    metrics["service_code"] = metrics["service_code_truth"].fillna(metrics["service_code"]).fillna("unknown")
    metrics["cost_ratio"] = (metrics["truth_cost"] / metrics["base_cost"]).replace([np.inf, -np.inf], np.nan).fillna(1.0)
    metrics["usage_ratio"] = (metrics["truth_usage"] / metrics["base_usage"]).replace([np.inf, -np.inf], np.nan).fillna(1.0)

    cpu_scale = np.clip(1.0 + (metrics["usage_ratio"] - 1.0) * 0.75, 0.4, 4.5)
    mem_scale = np.clip(1.0 + (metrics["usage_ratio"] - 1.0) * 0.35, 0.6, 3.0)
    net_scale = np.clip(np.maximum(metrics["usage_ratio"], 1.0 + (metrics["cost_ratio"] - 1.0) * 0.55), 0.4, 8.0)
    disk_scale = np.clip(1.0 + (metrics["usage_ratio"] - 1.0) * 0.95, 0.4, 8.0)
    db_scale = np.clip(1.0 + (metrics["usage_ratio"] - 1.0) * 1.2, 0.4, 8.0)
    gpu_scale = np.clip(1.0 + (metrics["usage_ratio"] - 1.0) * 0.85, 0.4, 4.0)

    metrics["cpu_percent"] = (metrics["cpu_percent"] * cpu_scale).clip(upper=100.0)
    metrics["memory_mib"] = metrics["memory_mib"] * mem_scale
    metrics["network_in_bytes"] = metrics["network_in_bytes"] * net_scale
    metrics["network_out_bytes"] = metrics["network_out_bytes"] * net_scale
    metrics["disk_io_ops"] = metrics["disk_io_ops"] * disk_scale
    if "database_connections" in metrics.columns:
        metrics["database_connections"] = (pd.to_numeric(metrics["database_connections"], errors="coerce") * db_scale).round().fillna(metrics["database_connections"])
    if "gpu_utilization" in metrics.columns:
        metrics["gpu_utilization"] = (pd.to_numeric(metrics["gpu_utilization"], errors="coerce") * gpu_scale).clip(upper=100.0)
    for hour in [f"cpu_h{idx}" for idx in range(24) if f"cpu_h{idx}" in metrics.columns]:
        metrics[hour] = (metrics[hour] * cpu_scale).clip(lower=0.0, upper=100.0)

    label_map = label_lookup_map(extended_labels)
    metrics["label"] = [
        label_map.get((str(resource_id), pd.Timestamp(usage_date)), existing_label)
        for resource_id, usage_date, existing_label in zip(metrics["resource_id"], metrics["usage_date"], metrics["label"])
    ]

    logs: list[dict[str, Any]] = []

    jitter_pool = metrics.index[metrics["service_code"].isin(["AmazonCloudWatch", "AmazonEC2", "AmazonRDS", "AWSDataTransfer"])].to_numpy()
    jitter_count = min(320, len(jitter_pool))
    jitter_indices = rng.choice(jitter_pool, size=jitter_count, replace=False)
    jitter_minutes = rng.integers(5, 720, size=jitter_count)
    jitter_values = pd.to_datetime(metrics.loc[jitter_indices, "usage_date"], utc=True) + pd.to_timedelta(jitter_minutes, unit="m")
    metrics.loc[jitter_indices, "timestamp"] = jitter_values.to_numpy()
    logs.append(
        {
            "scenario_id": "DQ_METRICS_TIMESTAMP_JITTER",
            "category": "data_quality",
            "target_file": "metrics/*.csv",
            "affected_rows": int(jitter_count),
            "details": "Shifted metric timestamps within the same day to mimic non-midnight collection windows.",
        }
    )

    missing_pool = metrics.index[
        metrics["service_code"].isin(["AmazonEC2", "AmazonRDS", "AmazonSageMaker", "AmazonCloudWatch"])
        & metrics["label"].isin(["normal", "benign"])
    ].to_numpy()
    missing_count = min(180, len(missing_pool))
    missing_indices = rng.choice(missing_pool, size=missing_count, replace=False)
    metrics.loc[missing_indices, "cpu_percent"] = np.nan
    metrics.loc[missing_indices[: missing_count // 2], "network_out_bytes"] = np.nan
    logs.append(
        {
            "scenario_id": "DQ_METRICS_PARTIAL_NULLS",
            "category": "data_quality",
            "target_file": "metrics/*.csv",
            "affected_rows": int(missing_count),
            "details": "Blanked selected metric fields on a subset of rows to mimic partial telemetry outages.",
        }
    )

    drop_pool = metrics.index[
        metrics["service_code"].isin(["AWSELB", "AmazonEKS", "AmazonS3", "AmazonElastiCache"])
        & metrics["label"].eq("normal")
    ].to_numpy()
    drop_count = min(90, len(drop_pool))
    drop_indices = rng.choice(drop_pool, size=drop_count, replace=False)
    metrics = metrics.drop(index=drop_indices).reset_index(drop=True)
    logs.append(
        {
            "scenario_id": "DQ_METRICS_MISSING_ROWS",
            "category": "data_quality",
            "target_file": "metrics/*.csv",
            "affected_rows": int(drop_count),
            "details": "Dropped a small set of telemetry rows for low-risk services to mimic collector gaps.",
        }
    )

    keep_columns = [column for column in base_metrics.columns if column in metrics.columns]
    metrics = metrics[keep_columns].copy()
    metrics["timestamp"] = pd.to_datetime(metrics["timestamp"], utc=True)
    return metrics, logs


def apply_ce_quality_noise(
    ce: pd.DataFrame,
    protected_keys: set[tuple[pd.Timestamp, int, str]],
    rng: np.random.Generator,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    logs: list[dict[str, Any]] = []
    ce = ce.copy()

    estimated_mask = ce["date"] >= pd.Timestamp("2026-05-28")
    ce.loc[estimated_mask, "is_estimated"] = True
    logs.append(
        {
            "scenario_id": "DQ_CE_ESTIMATED_EXPANSION",
            "category": "data_quality",
            "target_file": "cost_explorer_daily.csv",
            "affected_rows": int(estimated_mask.sum()),
            "details": "Marked the final four days as estimated to mimic late-finalized CE data.",
        }
    )

    candidate_mask = ~ce.apply(
        lambda row: (row["date"], int(row["linked_account_id"]), str(row["service_code"])) in protected_keys,
        axis=1,
    )
    drift_candidates = ce.index[candidate_mask].to_numpy()
    drift_count = min(80, len(drift_candidates))
    drift_indices = rng.choice(drift_candidates, size=drift_count, replace=False)
    drift_factors = rng.choice([0.985, 0.992, 1.008, 1.015], size=drift_count)
    ce.loc[drift_indices, "unblended_cost"] = (ce.loc[drift_indices, "unblended_cost"].to_numpy() * drift_factors).round(2)
    logs.append(
        {
            "scenario_id": "DQ_CE_RECON_DRIFT",
            "category": "data_quality",
            "target_file": "cost_explorer_daily.csv",
            "affected_rows": int(drift_count),
            "details": "Applied small CE-only cost drift on non-protected rows.",
        }
    )

    gap_pool = ce.index[
        candidate_mask
        & ce["service_code"].isin(["AWSELB", "AmazonEKS", "AmazonElastiCache", "AmazonS3"])
        & ce["unblended_cost"].lt(250.0)
        & ce["date"].lt(pd.Timestamp("2026-05-24"))
    ].to_numpy()
    gap_count = min(14, len(gap_pool))
    gap_indices = rng.choice(gap_pool, size=gap_count, replace=False)
    ce = ce.drop(index=gap_indices).sort_values(["date", "linked_account_id", "service_code"]).reset_index(drop=True)
    logs.append(
        {
            "scenario_id": "DQ_CE_PARTIAL_GAPS",
            "category": "data_quality",
            "target_file": "cost_explorer_daily.csv",
            "affected_rows": int(gap_count),
            "details": "Dropped a small set of low-risk CE aggregates to mimic partial extraction gaps.",
        }
    )
    return ce, logs


def apply_cur_quality_noise(
    cur: pd.DataFrame,
    protected_resources: set[str],
    rng: np.random.Generator,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    logs: list[dict[str, Any]] = []
    cur = cur.copy()
    candidate_mask = ~cur["line_item_resource_id"].astype(str).isin(protected_resources)

    team_pool = cur.index[
        candidate_mask
        & cur["resource_tags_user_team"].fillna("").astype(str).str.strip().ne("")
    ].to_numpy()
    team_count = min(180, len(team_pool))
    team_indices = rng.choice(team_pool, size=team_count, replace=False)
    team_variants = np.array(["platform ", " PLATFORM", "data-eng ", "ml "])
    cur.loc[team_indices, "resource_tags_user_team"] = rng.choice(team_variants, size=team_count)
    logs.append(
        {
            "scenario_id": "DQ_CUR_TAG_DIRTINESS",
            "category": "data_quality",
            "target_file": "cur_line_items.csv",
            "affected_rows": int(team_count),
            "details": "Injected whitespace and case drift into team tags.",
        }
    )

    owner_pool = cur.index[
        candidate_mask
        & cur["resource_tags_user_owner"].fillna("").astype(str).str.strip().ne("")
    ].to_numpy()
    owner_count = min(220, len(owner_pool))
    owner_indices = rng.choice(owner_pool, size=owner_count, replace=False)
    cur.loc[owner_indices, "resource_tags_user_owner"] = ""
    logs.append(
        {
            "scenario_id": "DQ_CUR_OWNER_GAPS",
            "category": "data_quality",
            "target_file": "cur_line_items.csv",
            "affected_rows": int(owner_count),
            "details": "Blanked owner tags on a controlled subset of non-protected rows.",
        }
    )

    region_pool = cur.index[
        candidate_mask
        & cur["line_item_product_code"].isin(["AmazonCloudWatch", "AWSDataTransfer", "AmazonS3"])
    ].to_numpy()
    region_count = min(140, len(region_pool))
    region_indices = rng.choice(region_pool, size=region_count, replace=False)
    half = region_count // 2
    cur.loc[region_indices[:half], "product_region_code"] = ""
    cur.loc[region_indices[half:], "product_region_code"] = "global"
    cur.loc[region_indices, "product_instance_type"] = ""
    logs.append(
        {
            "scenario_id": "DQ_CUR_REGION_VARIANCE",
            "category": "data_quality",
            "target_file": "cur_line_items.csv",
            "affected_rows": int(region_count),
            "details": "Mixed blank and global region values on services that are often messy in practice.",
        }
    )

    gap_resource = choose_resource(
        cur,
        account_name="dev",
        service_code="AmazonEC2",
        excluded_resources=protected_resources,
        start_date="2026-05-18",
        end_date="2026-05-28",
    )
    gap_dates = pd.to_datetime(["2026-05-19", "2026-05-21", "2026-05-24", "2026-05-26", "2026-05-28"])
    gap_mask = cur["line_item_resource_id"].eq(gap_resource) & cur["usage_date"].isin(gap_dates)
    gap_count = int(gap_mask.sum())
    cur = cur.loc[~gap_mask].copy()
    logs.append(
        {
            "scenario_id": "DQ_CUR_EXPORT_GAPS",
            "category": "data_quality",
            "target_file": "cur_line_items.csv",
            "affected_rows": gap_count,
            "details": f"Removed selected daily rows for {gap_resource} to mimic a partial CUR export gap.",
        }
    )

    candidate_mask = ~cur["line_item_resource_id"].astype(str).isin(protected_resources)
    dup_pool = cur.index[
        candidate_mask
        & cur["line_item_usage_account_name"].isin(["dev", "prod-core"])
        & cur["usage_date"].between(pd.Timestamp("2026-05-10"), pd.Timestamp("2026-05-20"))
        & cur["line_item_unblended_cost"].between(1.0, 60.0)
    ].to_numpy()
    dup_count = min(42, len(dup_pool))
    dup_indices = rng.choice(dup_pool, size=dup_count, replace=False)
    duplicate_rows = cur.loc[dup_indices].copy()
    cur = pd.concat([cur, duplicate_rows], ignore_index=True)
    logs.append(
        {
            "scenario_id": "DQ_CUR_LATE_DUPLICATES",
            "category": "data_quality",
            "target_file": "cur_line_items.csv",
            "affected_rows": int(dup_count),
            "details": "Duplicated a small subset of low-cost rows to mimic non-idempotent ingestion.",
        }
    )

    cur = cur.sort_values(
        ["line_item_usage_start_date", "line_item_usage_account_id", "line_item_product_code", "line_item_resource_id"]
    ).reset_index(drop=True)
    cur["usage_date"] = cur["line_item_usage_start_date"].dt.tz_localize(None).dt.normalize()
    return cur, logs


def build_protected_ce_keys(labels: pd.DataFrame) -> set[tuple[pd.Timestamp, int, str]]:
    keys: set[tuple[pd.Timestamp, int, str]] = set()
    for row in labels.itertuples(index=False):
        for date in pd.date_range(row.start_date, row.end_date, freq="D"):
            keys.add((pd.Timestamp(date), int(row.linked_account_id), str(row.service)))
    return keys


def validate_public_labels(cur: pd.DataFrame, labels: pd.DataFrame) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for row in labels.itertuples(index=False):
        mask = (
            cur["line_item_resource_id"].eq(str(row.resource_id))
            & cur["usage_date"].between(row.start_date, row.end_date)
            & cur["line_item_product_code"].eq(str(row.service))
        )
        matched_days = int(cur.loc[mask, "usage_date"].nunique())
        results.append(
            {
                "anomaly_id": row.anomaly_id,
                "resource_id": row.resource_id,
                "matched_days": matched_days,
                "window_days": int((row.end_date - row.start_date).days + 1),
                "preserved": matched_days > 0,
            }
        )
    return results


def build_readme(manifest: dict[str, Any]) -> str:
    generated = manifest["generated_labels"]
    dq = manifest["data_quality_scenarios"]
    lines = [
        "# CDO-Like Validation Dataset v1",
        "",
        "This pack is derived from the original sandbox files:",
        "- `cost_explorer_daily.csv`",
        "- `cur_line_items.csv`",
        "- `anomaly_labels_public.csv`",
        "",
        "It keeps the same schema but makes the data harder and messier.",
        "",
        "## Files",
        "",
        "- `cost_explorer_daily.csv`: transformed CE-like aggregate feed",
        "- `cur_line_items.csv`: transformed CUR-like line items",
        "- `metrics/`: transformed telemetry pack aligned to the derived resources and labels",
        "- `anomaly_labels_public.csv`: unchanged public anchor labels copied from the base pack",
        "- `anomaly_labels_extended.csv`: public anchors plus generated validation labels",
        "- `transformation_log.csv`: row-level scenario summary",
        "- `dataset_manifest.json`: counts, seed, and checks",
        "",
        "## Generated labeled scenarios",
        "",
        "| ID | Label | Type | Service | Window | Resource |",
        "|---|---|---|---|---|---|",
    ]
    for item in generated:
        lines.append(
            f"| {item['anomaly_id']} | {item['label']} | {item['anomaly_type']} | "
            f"{item['service']} | {item['start_date']} -> {item['end_date']} | {item['resource_id']} |"
        )

    lines.extend(
        [
            "",
            "## Data quality scenarios",
            "",
            "| ID | Target | Affected rows | Details |",
            "|---|---|---:|---|",
        ]
    )
    for item in dq:
        lines.append(
            f"| {item['scenario_id']} | {item['target_file']} | {item['affected_rows']} | {item['details']} |"
        )

    lines.extend(
        [
            "",
            "## Validation checks",
            "",
            f"- Seed: `{manifest['seed']}`",
            f"- Original CE rows: `{manifest['original_counts']['ce_rows']}` -> final `{manifest['final_counts']['ce_rows']}`",
            f"- Original CUR rows: `{manifest['original_counts']['cur_rows']}` -> final `{manifest['final_counts']['cur_rows']}`",
            f"- Original metrics rows: `{manifest['original_counts']['metrics_rows']}` -> final `{manifest['final_counts']['metrics_rows']}`",
            f"- Public anchor labels preserved: `{manifest['checks']['all_public_labels_preserved']}`",
            "",
            "## Recommended use",
            "",
            "- Use this pack for adapter, data-quality, and detector robustness testing.",
            "- Treat `anomaly_labels_extended.csv` as a local validation aid, not as a replacement for CDO-confirmed labels.",
            "- `metrics/` in this pack is synthetic telemetry derived from the base metrics plus the injected scenarios and telemetry-quality noise.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    rng = np.random.default_rng(SEED)
    root = Path(__file__).resolve().parent
    output_dir = root / OUTPUT_DIR_NAME
    output_dir.mkdir(parents=True, exist_ok=True)

    base_ce = normalize_ce(pd.read_csv(root / "cost_explorer_daily.csv"))
    base_cur = normalize_cur(pd.read_csv(root / "cur_line_items.csv"))
    public_labels = normalize_labels(pd.read_csv(root / "anomaly_labels_public.csv"))
    base_metrics = load_base_metrics(root / "metrics")
    metric_schema_map = {
        path.name: pd.read_csv(path, nrows=0).columns.tolist()
        for path in sorted((root / "metrics").glob("*.csv"))
    }

    original_counts = {
        "ce_rows": int(len(base_ce)),
        "cur_rows": int(len(base_cur)),
        "metrics_rows": int(len(base_metrics)),
        "public_label_rows": int(len(public_labels)),
    }

    protected_public_resources = build_protected_resources(public_labels)

    event_specs = [
        EventSpec(
            anomaly_id="G1",
            label="anomaly",
            anomaly_type="sudden_spike",
            account_name="prod-core",
            service_code="AmazonCloudWatch",
            start_date="2026-04-15",
            end_date="2026-04-18",
            description="Verbose trace sampling was left enabled in a hot path and CloudWatch ingest spiked for four days.",
            cost_multipliers=[2.9, 3.3, 3.4, 2.8],
            usage_multipliers=[2.7, 3.1, 3.2, 2.6],
        ),
        EventSpec(
            anomaly_id="G2",
            label="anomaly",
            anomaly_type="untagged_spend",
            account_name="prod-core",
            service_code="AmazonEC2",
            start_date="2026-04-15",
            end_date="2026-05-05",
            description="A long-lived EC2 workload lost team ownership tags and cost allocation became unreliable for three weeks.",
            cost_multipliers=[1.18] * 21,
            usage_multipliers=[1.12] * 21,
            blank_team=True,
            blank_owner=True,
        ),
        EventSpec(
            anomaly_id="G3",
            label="anomaly",
            anomaly_type="gradual_drift",
            account_name="data-analytics",
            service_code="AmazonDynamoDB",
            start_date="2026-04-05",
            end_date="2026-05-20",
            description="Provisioned throughput was scaled up repeatedly and never scaled back down, creating a gradual spend drift.",
            cost_multipliers=[round(value, 4) for value in np.linspace(1.0, 1.75, 46)],
            usage_multipliers=[round(value, 4) for value in np.linspace(1.0, 1.62, 46)],
        ),
        EventSpec(
            anomaly_id="G4",
            label="anomaly",
            anomaly_type="runaway_usage",
            account_name="ml-research",
            service_code="AmazonSageMaker",
            start_date="2026-04-08",
            end_date="2026-04-18",
            description="Notebook kernels were left warm after an experimentation sprint and spend stayed elevated for eleven days.",
            cost_multipliers=[1.65, 1.72, 1.8, 1.88, 1.95, 2.02, 2.08, 2.12, 2.08, 1.96, 1.84],
            usage_multipliers=[1.52, 1.58, 1.66, 1.74, 1.8, 1.86, 1.91, 1.94, 1.9, 1.82, 1.73],
        ),
        EventSpec(
            anomaly_id="GB1",
            label="benign",
            anomaly_type="benign_event",
            account_name="prod-core",
            service_code="AWSDataTransfer",
            start_date="2026-05-10",
            end_date="2026-05-12",
            description="A planned partner export drove a short egress spike and should not trigger containment.",
            cost_multipliers=[2.35, 2.55, 2.25],
            usage_multipliers=[2.2, 2.35, 2.1],
        ),
        EventSpec(
            anomaly_id="GB2",
            label="benign",
            anomaly_type="benign_event",
            account_name="staging",
            service_code="AmazonRDS",
            start_date="2026-04-18",
            end_date="2026-04-20",
            description="A ticketed migration dry-run temporarily doubled RDS spend and should be treated as expected.",
            cost_multipliers=[1.75, 1.92, 1.68],
            usage_multipliers=[1.68, 1.85, 1.62],
        ),
    ]

    cur_truth = base_cur.copy()
    generated_logs: list[dict[str, Any]] = []
    generated_labels: list[dict[str, Any]] = []
    protected_all_resources = set(protected_public_resources)

    for spec in event_specs:
        resource_id = choose_resource(
            cur_truth,
            account_name=spec.account_name,
            service_code=spec.service_code,
            excluded_resources=protected_all_resources,
            start_date=spec.start_date,
            end_date=spec.end_date,
        )
        affected_rows = apply_event(cur_truth, resource_id, spec)
        protected_all_resources.add(resource_id)
        generated_logs.append(
            {
                "scenario_id": spec.anomaly_id,
                "category": "generated_label",
                "target_file": "cur_line_items.csv",
                "affected_rows": affected_rows,
                "details": f"{spec.label} / {spec.anomaly_type} injected on {resource_id}",
            }
        )
        generated_labels.append(build_generated_label(cur_truth, resource_id, spec))

    extended_labels = pd.concat(
        [
            public_labels.assign(source="public_anchor"),
            pd.DataFrame(generated_labels).assign(source="generated_validation"),
        ],
        ignore_index=True,
    )
    extended_labels["start_date"] = pd.to_datetime(extended_labels["start_date"]).dt.normalize()
    extended_labels["end_date"] = pd.to_datetime(extended_labels["end_date"]).dt.normalize()

    ce_truth = build_ce_from_cur(cur_truth, base_ce)
    protected_ce_keys = build_protected_ce_keys(extended_labels)
    ce_final, ce_quality_logs = apply_ce_quality_noise(ce_truth, protected_ce_keys, rng)

    metrics_final, metrics_quality_logs = build_metrics_from_truth(base_metrics, base_cur, cur_truth, extended_labels, rng)
    cur_final, cur_quality_logs = apply_cur_quality_noise(cur_truth, protected_all_resources, rng)

    public_checks = validate_public_labels(cur_final, public_labels)
    transformation_log = pd.DataFrame(generated_logs + ce_quality_logs + cur_quality_logs + metrics_quality_logs)

    final_counts = {
        "ce_rows": int(len(ce_final)),
        "cur_rows": int(len(cur_final)),
        "metrics_rows": int(len(metrics_final)),
        "public_label_rows": int(len(public_labels)),
        "extended_label_rows": int(len(extended_labels)),
    }

    manifest = {
        "dataset_name": OUTPUT_DIR_NAME,
        "seed": SEED,
        "base_files": {
            "cost_explorer_daily": "data/cost_explorer_daily.csv",
            "cur_line_items": "data/cur_line_items.csv",
            "anomaly_labels_public": "data/anomaly_labels_public.csv",
        },
        "original_counts": original_counts,
        "final_counts": final_counts,
        "generated_labels": [
            {
                "anomaly_id": row["anomaly_id"],
                "label": row["label"],
                "anomaly_type": row["anomaly_type"],
                "service": row["service"],
                "resource_id": row["resource_id"],
                "start_date": row["start_date"],
                "end_date": row["end_date"],
            }
            for row in generated_labels
        ],
        "data_quality_scenarios": ce_quality_logs + cur_quality_logs + metrics_quality_logs,
        "checks": {
            "ce_schema_matches_base": list(ce_final.columns) == list(base_ce.columns),
            "cur_schema_matches_base": list(cur_final.drop(columns=["usage_date"]).columns) == list(base_cur.drop(columns=["usage_date"]).columns),
            "metrics_folder_present": True,
            "public_label_checks": public_checks,
            "all_public_labels_preserved": all(item["preserved"] for item in public_checks),
        },
    }

    ce_to_write = ce_final.copy()
    ce_to_write["date"] = ce_to_write["date"].dt.date.astype(str)
    ce_to_write["is_estimated"] = ce_to_write["is_estimated"].astype(bool)
    ce_to_write.to_csv(output_dir / "cost_explorer_daily.csv", index=False)

    cur_to_write = cur_final.drop(columns=["usage_date"]).copy()
    cur_to_write["bill_billing_period_start_date"] = cur_to_write["bill_billing_period_start_date"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    cur_to_write["line_item_usage_start_date"] = cur_to_write["line_item_usage_start_date"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    cur_to_write["line_item_usage_end_date"] = cur_to_write["line_item_usage_end_date"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    cur_to_write.to_csv(output_dir / "cur_line_items.csv", index=False)

    public_to_write = public_labels.copy()
    public_to_write["start_date"] = public_to_write["start_date"].dt.date.astype(str)
    public_to_write["end_date"] = public_to_write["end_date"].dt.date.astype(str)
    public_to_write.to_csv(output_dir / "anomaly_labels_public.csv", index=False)

    metrics_dir = output_dir / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    metrics_final = metrics_final.copy()
    metrics_final["timestamp"] = pd.to_datetime(metrics_final["timestamp"], utc=True).dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    metrics_service_map = (
        base_cur.groupby("line_item_resource_id", as_index=False)["line_item_product_code"]
        .agg(first_non_empty)
        .rename(columns={"line_item_resource_id": "resource_id", "line_item_product_code": "service_code"})
    )
    metrics_final = metrics_final.merge(metrics_service_map, on="resource_id", how="left")
    metrics_final["target_file"] = metrics_final["service_code"].fillna("unknown").map(metric_target_file)
    for target_file, frame in metrics_final.groupby("target_file", dropna=False):
        target_columns = metric_schema_map[str(target_file)]
        to_write = frame.drop(columns=["service_code", "target_file"], errors="ignore").copy()
        for column in target_columns:
            if column not in to_write.columns:
                to_write[column] = np.nan
        to_write = to_write[target_columns]
        to_write.to_csv(metrics_dir / str(target_file), index=False)

    extended_to_write = extended_labels.copy()
    extended_to_write["start_date"] = extended_to_write["start_date"].dt.date.astype(str)
    extended_to_write["end_date"] = extended_to_write["end_date"].dt.date.astype(str)
    extended_to_write.to_csv(output_dir / "anomaly_labels_extended.csv", index=False)

    transformation_log.to_csv(output_dir / "transformation_log.csv", index=False)
    (output_dir / "dataset_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    (output_dir / "README.md").write_text(build_readme(manifest), encoding="utf-8")

    print(json.dumps(asdict if False else {  # keeps stdout compact and JSON-like
        "output_dir": str(output_dir),
        "ce_rows": final_counts["ce_rows"],
        "cur_rows": final_counts["cur_rows"],
        "metrics_rows": final_counts["metrics_rows"],
        "extended_label_rows": final_counts["extended_label_rows"],
        "public_labels_preserved": manifest["checks"]["all_public_labels_preserved"],
    }, indent=2))


if __name__ == "__main__":
    main()
