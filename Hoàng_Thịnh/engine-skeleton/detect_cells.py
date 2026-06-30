# -*- coding: utf-8 -*-
from __future__ import annotations

# %%
import argparse
import json
import sys
import uuid
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from detect_core import (
    MODEL_NAME,
    apply_fp_suppressor,
    apply_rule_flags,
    build_evaluation_bundle,
    build_candidate_events,
    build_pair_daily,
    build_resource_daily,
    build_result,
    cooldown_dedup_events,
    evaluate_public,
    group_related_events,
    load_local_metrics,
    normalize_timestamp,
    parse_cpu_hourly_samples,
    precision_cleanup_events,
    resolve_metrics_dir,
    rerank_events,
    validate_label_catalog,
)
from llm_rca import load_rca_generator


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "docs" / "assets" / "detect_cells"
RCA_GENERATOR = load_rca_generator()

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def parse_data_dir() -> Path:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--data-dir", type=str, default=None)
    args, _ = parser.parse_known_args()
    if args.data_dir:
        candidate = Path(args.data_dir)
        if not candidate.is_absolute():
            candidate = (ROOT / candidate).resolve()
        return candidate
    return DATA_DIR


def resolve_dataset_file(data_dir: Path, candidates: list[str]) -> Path:
    for candidate in candidates:
        path = data_dir / candidate
        if path.exists():
            return path
    raise FileNotFoundError(f"Could not find any of {candidates} under {data_dir}")


def load_direct_metrics(metrics_path: Path) -> pd.DataFrame:
    metrics = pd.read_csv(metrics_path, parse_dates=["timestamp"])
    metrics["timestamp"] = normalize_timestamp(metrics["timestamp"])
    metrics["usage_date"] = metrics["timestamp"].dt.normalize()
    if "cpu_utilization_hourly" in metrics.columns:
        cpu_samples = metrics["cpu_utilization_hourly"].apply(parse_cpu_hourly_samples)
        metrics["cpu_subhour_mean"] = cpu_samples.apply(lambda values: float(sum(values) / len(values)) if values else float("nan"))
        metrics["cpu_subhour_std"] = cpu_samples.apply(lambda values: float(pd.Series(values).std(ddof=0)) if values else float("nan"))
        metrics["cpu_subhour_max"] = cpu_samples.apply(lambda values: float(max(values)) if values else float("nan"))
        metrics["cpu_subhour_peak_samples"] = cpu_samples.apply(lambda values: int(sum(sample >= 85.0 for sample in values)) if values else 0)
    if "event_type" in metrics.columns:
        metrics["event_type_present_int"] = metrics["event_type"].notna().astype(int)
    metrics["has_metrics_int"] = 1
    metrics.attrs["metrics_dir"] = str(metrics_path.parent)
    metrics.attrs["metrics_variant"] = "unified_hourly"
    metrics.attrs["label_source"] = "metrics.label_hourly_aggregated_daily" if "label" in metrics.columns else "unlabeled"
    return metrics


def load_metrics_for_dir(data_dir: Path) -> pd.DataFrame | None:
    metrics_dir = resolve_metrics_dir(data_dir)
    if metrics_dir is not None:
        return load_local_metrics(metrics_dir)
    for metrics_path in [data_dir / "new_metrics.csv", data_dir / "metrics.csv"]:
        if metrics_path.exists():
            return load_direct_metrics(metrics_path)
    return None


DATASET_DIR = parse_data_dir()
COST_PATH = resolve_dataset_file(DATASET_DIR, ["cost_explorer_daily.csv", "new_cost_explorer_daily.csv"])
CUR_PATH = resolve_dataset_file(DATASET_DIR, ["cur_line_items.csv", "new_cur_line_items.csv"])
LABELS_PATH = resolve_dataset_file(DATASET_DIR, ["anomaly_labels_public.csv", "anomaly_labels_test.csv"])
RUN_OUTPUT_DIR = OUTPUT_DIR if DATASET_DIR == DATA_DIR else OUTPUT_DIR / DATASET_DIR.name

print(f"San sang voi detector {MODEL_NAME}")
print(f"Dataset dir: {DATASET_DIR}")


# %%
# Cell 1: Nap du lieu dau vao
ce = pd.read_csv(COST_PATH)
cur = pd.read_csv(CUR_PATH)
labels = pd.read_csv(LABELS_PATH, parse_dates=["start_date", "end_date"])
metrics = load_metrics_for_dir(DATASET_DIR)

ce["date"] = pd.to_datetime(ce["date"]).dt.normalize()
cur["line_item_usage_start_date"] = pd.to_datetime(cur["line_item_usage_start_date"])
cur["usage_date"] = cur["line_item_usage_start_date"].dt.tz_localize(None).dt.normalize()

print("So dong Cost Explorer:", len(ce))
print("So dong CUR:", len(cur))
print("So dong metrics:", 0 if metrics is None else len(metrics))
print("So dong labels:", len(labels))
print("Nguon metrics:", "none" if metrics is None else metrics.attrs.get("metrics_variant", "unknown"))


# %%
# Cell 2: Tao feature robust theo account-service va chia train/test theo thoi gian
pair = build_pair_daily(ce)
print("Temporal split:", pair.attrs.get("temporal_split", {}))
pair[["usage_date", "linked_account_name", "service_code", "pair_cost_24h", "pair_ratio7", "pair_delta"]].head(10)


# %%
# Cell 3: Gom CUR ve muc resource-day, join telemetry va label supervised
resource_daily = build_resource_daily(cur, metrics, pair)
resource_daily[
    [
        "usage_date",
        "line_item_resource_id",
        "line_item_product_code",
        "line_item_unblended_cost",
        "resource_cost_ratio7",
        "resource_usage_ratio7",
        "supervised_label",
        "migration_flag",
        "load_test_flag",
        "campaign_flag",
    ]
].head(10)


# %%
# Cell 4: Score XGBoost supervised va tao type hint cho anomaly
scored_daily = apply_rule_flags(resource_daily)
training_summary = scored_daily.attrs.get("supervised_training", {})
print(
    "Training summary compact:",
    {
        "cv_folds_completed": training_summary.get("cv_folds_completed"),
        "threshold_source": training_summary.get("threshold_source"),
        "threshold": training_summary.get("threshold"),
        "cv_oof_metrics": training_summary.get("cv_oof_metrics"),
        "test_pipeline_metrics": training_summary.get("test_pipeline_metrics"),
        "runtime_mode": training_summary.get("runtime_mode"),
        "runtime_quality": training_summary.get("runtime_quality"),
    },
)
print("So resource-day duoc model flag:", int(scored_daily["xgb_candidate"].sum()))
scored_daily[
    [
        "usage_date",
        "line_item_resource_id",
        "supervised_label",
        "xgb_score",
        "xgb_candidate",
        "anomaly_type_hint",
        "type_hint_confidence",
    ]
].head(10)


# %%
# Cell 5: Collapse cac ngay lien tiep thanh candidate events
candidate_events = build_candidate_events(scored_daily)
candidate_events = candidate_events.sort_values(["detector_score", "event_total_cost"], ascending=False).reset_index(drop=True)

print("So candidate events truoc suppressor:", len(candidate_events))
candidate_events[
    [
        "anomaly_type",
        "resource_id",
        "service_code",
        "event_start",
        "event_end",
        "event_total_cost",
        "detector_score",
        "candidate_sources",
    ]
]


# %%
# Cell 6: FP suppressor de loai planned migration, load test, campaign, benign growth
suppressed_candidates = apply_fp_suppressor(candidate_events)
print("So candidate bi suppress:", int(suppressed_candidates["suppressed"].sum()))
suppressed_candidates[
    [
        "anomaly_type",
        "resource_id",
        "suppressed",
        "suppression_reason",
        "is_estimated",
        "benign_growth_signal",
    ]
].sort_values(["suppressed", "resource_id"], ascending=[False, True])


# %%
# Cell 7: Group nhieu resource cung incident va re-rank candidate cuoi cung
incident_events = cooldown_dedup_events(group_related_events(suppressed_candidates))
final_events = precision_cleanup_events(rerank_events(incident_events))
supervised_only_events = rerank_events(
    cooldown_dedup_events(
        group_related_events(
        apply_fp_suppressor(
            build_candidate_events(scored_daily, flag_col="supervised_only_prediction")
        )
        )
    )
)
supervised_only_events = precision_cleanup_events(supervised_only_events)
runtime_mode = scored_daily.attrs.get("supervised_training", {}).get("runtime_mode")
runtime_quality_tier = scored_daily.attrs.get("supervised_training", {}).get("runtime_quality", {}).get("quality_tier", "unknown")
if not final_events.empty:
    final_events["runtime_mode"] = runtime_mode
    final_events["runtime_quality_tier"] = runtime_quality_tier
if not supervised_only_events.empty:
    supervised_only_events["runtime_mode"] = runtime_mode
    supervised_only_events["runtime_quality_tier"] = runtime_quality_tier
final_events.attrs["temporal_split"] = {
    **pair.attrs.get("temporal_split", {}),
    "training_summary": scored_daily.attrs.get("supervised_training", {}),
    "label_source": scored_daily.attrs.get("supervised_training", {}).get("label_source", "unlabeled"),
    "metrics_variant": scored_daily.attrs.get("metrics_context", {}).get("metrics_variant", "none"),
    "metrics_dir": scored_daily.attrs.get("metrics_context", {}).get("metrics_dir"),
    "runtime_mode": scored_daily.attrs.get("supervised_training", {}).get("runtime_mode"),
    "runtime_quality": scored_daily.attrs.get("supervised_training", {}).get("runtime_quality", {}),
}
evaluation_bundle = build_evaluation_bundle(scored_daily, final_events.attrs["temporal_split"])
final_events.attrs["evaluation_bundle"] = evaluation_bundle
final_events.attrs["supervised_only_events"] = supervised_only_events

print("So incident cuoi cung:", len(final_events))
final_events[
    [
        "anomaly_type",
        "resource_id",
        "incident_scope",
        "impacted_resource_count",
        "event_total_cost",
        "detector_score",
        "confidence_score",
        "xgb_score",
    ]
]


# %%
# Cell 8: Doi chieu voi public labels
public_eval = evaluate_public(final_events, LABELS_PATH)
label_catalog_validation = validate_label_catalog(cur, LABELS_PATH)
public_eval["label_catalog_validation"] = label_catalog_validation
public_eval


# %%
# Cell 9: Dung JSON dau ra theo contract demo
result = build_result(final_events, audit_id=str(uuid.uuid4()), rca_generator=RCA_GENERATOR)
result["llm_context"] = {"provider": RCA_GENERATOR.provider, "model_id": RCA_GENERATOR.model_id, "enabled": RCA_GENERATOR.enabled}
result["evaluation_context"] = {
    "split_summary": evaluation_bundle.get("split_summary", {}),
    "training_summary_compact": evaluation_bundle.get("training_summary_compact", {}),
    "method_comparison": evaluation_bundle.get("method_comparison", {}),
}
supervised_only_events = final_events.attrs.get("supervised_only_events", pd.DataFrame())
public_eval_by_method = {
    "hybrid_pipeline": public_eval,
    "supervised_only": {
        **evaluate_public(supervised_only_events, LABELS_PATH),
        "label_catalog_validation": label_catalog_validation,
    },
}

print("Model:", MODEL_NAME)
print("Split summary:", evaluation_bundle.get("split_summary", {}))
print("Tong anomaly cuoi cung:", result["total_anomalies_found"])
print("LLM provider:", RCA_GENERATOR.provider)


# %%
# Cell 10: Luu output ra file
RUN_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
(RUN_OUTPUT_DIR / "detect_cells_result.json").write_text(
    json.dumps({"result": result, "public_eval": public_eval, "public_eval_by_method": public_eval_by_method, "model_name": MODEL_NAME, "dataset_dir": str(DATASET_DIR)}, indent=2, default=str, ensure_ascii=False),
    encoding="utf-8",
)
if not final_events.empty:
    final_events.to_csv(RUN_OUTPUT_DIR / "detect_cells_events.csv", index=False)
(RUN_OUTPUT_DIR / "detect_cells_eval.json").write_text(
    json.dumps(public_eval, indent=2, ensure_ascii=False),
    encoding="utf-8",
)
(RUN_OUTPUT_DIR / "split_summary.json").write_text(
    json.dumps(evaluation_bundle.get("split_summary", {}), indent=2, ensure_ascii=False),
    encoding="utf-8",
)
(RUN_OUTPUT_DIR / "cv_summary.json").write_text(
    json.dumps(evaluation_bundle.get("training_summary_full", {}), indent=2, ensure_ascii=False),
    encoding="utf-8",
)
(RUN_OUTPUT_DIR / "holdout_test_metrics.json").write_text(
    json.dumps(evaluation_bundle.get("holdout_test_metrics", {}), indent=2, ensure_ascii=False),
    encoding="utf-8",
)
(RUN_OUTPUT_DIR / "method_comparison.json").write_text(
    json.dumps({"holdout": evaluation_bundle.get("method_comparison", {}), "public": public_eval_by_method}, indent=2, ensure_ascii=False),
    encoding="utf-8",
)
cv_fold_metrics = evaluation_bundle.get("cv_fold_metrics", pd.DataFrame())
if not cv_fold_metrics.empty:
    cv_fold_metrics.to_csv(RUN_OUTPUT_DIR / "fold_metrics.csv", index=False)
holdout_predictions = evaluation_bundle.get("holdout_predictions", pd.DataFrame())
if not holdout_predictions.empty:
    holdout_predictions.to_csv(RUN_OUTPUT_DIR / "holdout_predictions.csv", index=False)
cv_oof_predictions = evaluation_bundle.get("cv_oof_predictions", pd.DataFrame())
if not cv_oof_predictions.empty:
    cv_oof_predictions.to_csv(RUN_OUTPUT_DIR / "cv_oof_predictions.csv", index=False)

print("Da luu:")
print(RUN_OUTPUT_DIR / "detect_cells_result.json")
print(RUN_OUTPUT_DIR / "detect_cells_events.csv")
print(RUN_OUTPUT_DIR / "detect_cells_eval.json")
print(RUN_OUTPUT_DIR / "split_summary.json")
print(RUN_OUTPUT_DIR / "cv_summary.json")
if not cv_fold_metrics.empty:
    print(RUN_OUTPUT_DIR / "fold_metrics.csv")
print(RUN_OUTPUT_DIR / "holdout_test_metrics.json")
print(RUN_OUTPUT_DIR / "method_comparison.json")
if not holdout_predictions.empty:
    print(RUN_OUTPUT_DIR / "holdout_predictions.csv")
if not cv_oof_predictions.empty:
    print(RUN_OUTPUT_DIR / "cv_oof_predictions.csv")
