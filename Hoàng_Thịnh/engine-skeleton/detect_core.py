from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from xgboost import XGBClassifier


MODEL_NAME = "xgboost-supervised-v1"
XGB_RANDOM_STATE = 42
TEMPORAL_TRAIN_RATIO = 0.7
FP_TARGET_MAX = 0.10
PRECISION_TARGET_MIN = 0.50
WALK_FORWARD_MAX_FOLDS = 3
WALK_FORWARD_MIN_TRAIN_DAYS = 21
WALK_FORWARD_MIN_VALID_DAYS = 7
VARIANT_THRESHOLD_FLOOR = {
    "unified_hourly": 0.70,
}
RUNTIME_MIN_TRAIN_LABEL_ROWS = 300
RUNTIME_MIN_METRICS_COVERAGE = 0.20
RUNTIME_MIN_LABEL_COVERAGE = 0.15
INCIDENT_COOLDOWN_DAYS = 7
DEFAULT_METRIC_COLS = [
    "cpu_percent",
    "gpu_utilization",
    "database_connections",
    "network_in_bytes",
    "network_out_bytes",
    "memory_mib",
    "disk_io_ops",
    "cpu_subhour_mean",
    "cpu_subhour_std",
    "cpu_subhour_max",
    "cpu_subhour_peak_samples",
    "event_type_present_int",
    "has_metrics_int",
]
SUPERVISED_FEATURES = [
    "cost_log",
    "usage_log",
    "pair_cost_log",
    "pair_ratio7",
    "pair_delta_log",
    "pair_robust_z7",
    "cost_median14_log",
    "cost_mad_ratio14",
    "resource_cost_ratio7",
    "resource_cost_delta_log",
    "resource_cost_mean_ratio_28vprev28",
    "resource_usage_ratio7",
    "resource_usage_mean_ratio_28vprev28",
    "resource_network_ratio7",
    "usage_density_24h",
    "consecutive_days",
    "weekend_days",
    "cpu_percent",
    "gpu_utilization",
    "database_connections",
    "cpu_subhour_mean",
    "cpu_subhour_std",
    "cpu_subhour_max",
    "cpu_subhour_peak_samples",
    "network_in_log",
    "network_out_log",
    "memory_mib_log",
    "disk_io_ops_log",
    "has_metrics_int",
    "team_missing_int",
    "owner_missing_int",
    "new_after_warmup_int",
    "migration_flag_int",
    "load_test_flag_int",
    "campaign_flag_int",
    "debug_flag_int",
    "cluster_flag_int",
    "benign_growth_signal_int",
    "day_of_week",
    "is_weekend_int",
    "is_estimated_int",
    "service_code_encoded",
    "account_encoded",
    "region_encoded",
    "environment_encoded",
    "pricing_unit_encoded",
    "usage_type_encoded",
    "resource_family_encoded",
]
TYPE_SCORE_NAMES = {
    "untagged_spend": "type_score_untagged",
    "idle_resource": "type_score_idle",
    "runaway_usage": "type_score_runaway",
    "sudden_spike": "type_score_spike",
    "gradual_drift": "type_score_drift",
}
EVENT_MIN_DAYS = {
    "untagged_spend": 5,
    "idle_resource": 14,
    "runaway_usage": 5,
    "sudden_spike": 4,
    "gradual_drift": 14,
}
PRECISION_CLEANUP_MIN_CONFIDENCE = 0.55
PRECISION_CLEANUP_MIN_COST = 600.0


def blank(value: Any) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value)) or (isinstance(value, str) and not value.strip())


def first_non_empty(values: pd.Series) -> Any:
    for value in values:
        if not blank(value):
            return value
    return None


def clamp(value: float, low: float = 0.0, high: float = 0.99) -> float:
    return max(low, min(high, value))


def safe_round(value: Any, digits: int = 2) -> float | None:
    if value is None or pd.isna(value):
        return None
    return round(float(value), digits)


def normalize_timestamp(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce", utc=True).dt.tz_localize(None)


def parse_cpu_hourly_samples(value: Any) -> list[float]:
    if blank(value):
        return []
    if isinstance(value, list):
        raw = value
    else:
        text = str(value).strip()
        if not text:
            return []
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            return []
    samples: list[float] = []
    for item in raw:
        try:
            samples.append(float(item))
        except (TypeError, ValueError):
            continue
    return samples


def prioritized_label(values: pd.Series) -> str:
    labels = values.fillna("").astype(str).str.strip().str.lower()
    if labels.eq("anomaly").any():
        return "anomaly"
    if labels.eq("benign").any():
        return "benign"
    if labels.eq("normal").any():
        return "normal"
    return "unknown"


def resolve_metrics_dir(data_dir: Path) -> Path | None:
    for candidate in [data_dir / "metrics_data", data_dir / "metrics"]:
        if candidate.exists():
            return candidate
    return None


def collapse_identity(values: pd.Series) -> str | None:
    valid = [str(value) for value in values if not blank(value)]
    if not valid:
        return None
    uniq = sorted(set(valid))
    return uniq[0] if len(uniq) == 1 else "multiple"


def rolling_median(series: pd.Series, window: int, min_periods: int) -> pd.Series:
    return series.rolling(window, min_periods=min_periods).median()


def rolling_mad(series: pd.Series, window: int, min_periods: int) -> pd.Series:
    def compute(values: np.ndarray) -> float:
        clean = values[~np.isnan(values)]
        if len(clean) == 0:
            return float("nan")
        median = float(np.median(clean))
        return float(np.median(np.abs(clean - median)))

    return series.rolling(window, min_periods=min_periods).apply(compute, raw=True)


def temporal_train_cutoff(dates: pd.Series, train_ratio: float = TEMPORAL_TRAIN_RATIO) -> pd.Timestamp:
    unique_dates = sorted(pd.to_datetime(dates).dt.normalize().unique())
    if len(unique_dates) < 2:
        return pd.Timestamp(unique_dates[0]) if unique_dates else pd.Timestamp.utcnow().normalize()
    split_index = max(0, min(len(unique_dates) - 2, int(math.floor(len(unique_dates) * train_ratio)) - 1))
    return pd.Timestamp(unique_dates[split_index])


def load_local_metrics(metrics_dir: Path) -> pd.DataFrame | None:
    if not metrics_dir.exists():
        return None
    frames: list[pd.DataFrame] = []
    for path in sorted(metrics_dir.glob("*.csv")):
        frame = pd.read_csv(path, parse_dates=["timestamp"])
        frame["timestamp"] = normalize_timestamp(frame["timestamp"])
        frame["usage_date"] = frame["timestamp"].dt.normalize()
        frame["metric_source"] = path.stem
        if "cpu_utilization_hourly" in frame.columns:
            cpu_samples = frame["cpu_utilization_hourly"].apply(parse_cpu_hourly_samples)
            frame["cpu_subhour_mean"] = cpu_samples.apply(lambda values: float(np.mean(values)) if values else np.nan)
            frame["cpu_subhour_std"] = cpu_samples.apply(lambda values: float(np.std(values)) if values else np.nan)
            frame["cpu_subhour_max"] = cpu_samples.apply(lambda values: float(np.max(values)) if values else np.nan)
            frame["cpu_subhour_peak_samples"] = cpu_samples.apply(lambda values: int(sum(sample >= 85.0 for sample in values)) if values else 0)
        if "event_type" in frame.columns:
            frame["event_type_present_int"] = frame["event_type"].notna().astype(int)
        frame["has_metrics_int"] = 1
        frames.append(frame)
    if not frames:
        return None
    metrics = pd.concat(frames, ignore_index=True)
    is_unified_hourly = (
        len(frames) == 1
        and {"resource_type", "account_name", "cpu_utilization_hourly", "event_type"}.issubset(metrics.columns)
    )
    metrics.attrs["metrics_dir"] = str(metrics_dir)
    metrics.attrs["metrics_variant"] = "unified_hourly" if is_unified_hourly else "legacy_split"
    metrics.attrs["label_source"] = (
        "metrics.label_hourly_aggregated_daily" if is_unified_hourly and "label" in metrics.columns else "metrics.label"
    )
    return metrics


def signed_log1p(value: pd.Series | float | int) -> pd.Series | float:
    if isinstance(value, pd.Series):
        filled = value.fillna(0.0).astype(float)
        return np.sign(filled) * np.log1p(np.abs(filled))
    val = float(value)
    return math.copysign(math.log1p(abs(val)), val)


def safe_divide(left: pd.Series, right: pd.Series) -> pd.Series:
    return left / right.replace(0, pd.NA)


def normalize_ratio(series: pd.Series, default: float = 1.0, lower: float = 0.0, upper: float = 8.0) -> pd.Series:
    return series.fillna(default).clip(lower=lower, upper=upper)


def encode_categories(frame: pd.DataFrame, mapping: dict[str, str], reference_mask: pd.Series | None = None) -> pd.DataFrame:
    frame = frame.copy()
    for source, target in mapping.items():
        values = frame[source].fillna("unknown").astype(str)
        reference_values = values.loc[reference_mask.fillna(False)] if reference_mask is not None else values
        ordered_known = list(dict.fromkeys(reference_values.tolist()))
        if "unknown" not in ordered_known:
            ordered_known.append("unknown")
        mapping_dict = {value: index for index, value in enumerate(ordered_known)}
        unknown_code = len(mapping_dict)
        frame[target] = values.map(mapping_dict).fillna(unknown_code).astype(int)
    return frame


def summarize_binary_metrics(y_true: pd.Series, y_pred: pd.Series) -> dict[str, float]:
    truth = y_true.astype(int).to_numpy()
    pred = y_pred.astype(int).to_numpy()
    tp = int(((truth == 1) & (pred == 1)).sum())
    tn = int(((truth == 0) & (pred == 0)).sum())
    fp = int(((truth == 0) & (pred == 1)).sum())
    fn = int(((truth == 1) & (pred == 0)).sum())
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    fpr = fp / (fp + tn) if (fp + tn) else 0.0
    accuracy = (tp + tn) / max(len(truth), 1)
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "fpr": round(fpr, 4),
        "accuracy": round(accuracy, 4),
        "tp": tp,
        "tn": tn,
        "fp": fp,
        "fn": fn,
    }


def fbeta_score_from_metrics(metrics: dict[str, float], beta: float = 2.0) -> float:
    precision = float(metrics.get("precision", 0.0))
    recall = float(metrics.get("recall", 0.0))
    if precision <= 0.0 and recall <= 0.0:
        return 0.0
    beta_sq = beta * beta
    denom = (beta_sq * precision) + recall
    if denom <= 0.0:
        return 0.0
    return ((1.0 + beta_sq) * precision * recall) / denom


def assess_runtime_quality(
    rd: pd.DataFrame,
    train_mask: pd.Series,
    metrics_context: dict[str, Any],
) -> dict[str, Any]:
    valid_labels = rd["supervised_label"].isin(["anomaly", "normal", "benign"])
    metrics_coverage = float(rd["has_metrics_int"].fillna(0.0).gt(0.0).mean()) if "has_metrics_int" in rd.columns else 0.0
    label_coverage = float(valid_labels.mean()) if len(rd) else 0.0
    train_label_rows = int(train_mask.sum())
    train_unique_resources = int(rd.loc[train_mask, "line_item_resource_id"].nunique()) if train_label_rows else 0
    telemetry_cols = ["cpu_percent", "network_out_bytes", "memory_mib", "database_connections", "gpu_utilization"]
    telemetry_non_null = float(rd[telemetry_cols].notna().any(axis=1).mean()) if set(telemetry_cols).issubset(rd.columns) and len(rd) else 0.0

    if (
        train_label_rows >= RUNTIME_MIN_TRAIN_LABEL_ROWS
        and metrics_coverage >= RUNTIME_MIN_METRICS_COVERAGE
        and label_coverage >= RUNTIME_MIN_LABEL_COVERAGE
    ):
        quality_tier = "healthy"
        model_enabled = True
    elif train_label_rows >= 200 and label_coverage >= 0.08:
        quality_tier = "degraded"
        model_enabled = True
    else:
        quality_tier = "poor"
        model_enabled = False

    return {
        "quality_tier": quality_tier,
        "model_enabled": model_enabled,
        "metrics_variant": metrics_context.get("metrics_variant", "none"),
        "label_source": metrics_context.get("label_source", "unlabeled"),
        "metrics_coverage": round(metrics_coverage, 4),
        "telemetry_non_null_rate": round(telemetry_non_null, 4),
        "label_coverage": round(label_coverage, 4),
        "train_labeled_rows": train_label_rows,
        "train_unique_resources": train_unique_resources,
    }


def choose_probability_threshold(y_true: pd.Series, probabilities: pd.Series) -> tuple[float, dict[str, float]]:
    quantiles = probabilities.quantile(np.linspace(0.2, 0.95, 16)).to_numpy()
    candidates = sorted({round(float(value), 4) for value in np.concatenate([np.linspace(0.2, 0.95, 16), quantiles])}, reverse=True)
    best_threshold = 0.7
    best_metrics = summarize_binary_metrics(y_true, pd.Series(probabilities >= best_threshold, index=probabilities.index))
    best_satisfies = best_metrics["fpr"] <= FP_TARGET_MAX and best_metrics["precision"] >= PRECISION_TARGET_MIN
    best_fbeta = fbeta_score_from_metrics(best_metrics, beta=2.0)
    best_fallback_satisfies = best_metrics["fpr"] <= FP_TARGET_MAX
    best_fallback_threshold = best_threshold
    best_fallback_metrics = best_metrics
    best_fallback_fbeta = best_fbeta

    for threshold in candidates:
        prediction = pd.Series(probabilities >= threshold, index=probabilities.index)
        metrics = summarize_binary_metrics(y_true, prediction)
        satisfies = metrics["fpr"] <= FP_TARGET_MAX and metrics["precision"] >= PRECISION_TARGET_MIN
        fallback_satisfies = metrics["fpr"] <= FP_TARGET_MAX
        current_fbeta = fbeta_score_from_metrics(metrics, beta=2.0)
        if fallback_satisfies:
            current_fallback_rank = (current_fbeta, metrics["recall"], metrics["precision"], -metrics["fpr"], -threshold)
            best_fallback_rank = (
                best_fallback_fbeta,
                best_fallback_metrics["recall"],
                best_fallback_metrics["precision"],
                -best_fallback_metrics["fpr"],
                -best_fallback_threshold,
            )
            if (not best_fallback_satisfies) or current_fallback_rank > best_fallback_rank:
                best_fallback_satisfies = True
                best_fallback_threshold = threshold
                best_fallback_metrics = metrics
                best_fallback_fbeta = current_fbeta
        if satisfies and not best_satisfies:
            best_threshold = threshold
            best_metrics = metrics
            best_satisfies = True
            best_fbeta = current_fbeta
            continue
        if satisfies and best_satisfies:
            current_rank = (current_fbeta, metrics["recall"], metrics["precision"], -metrics["fpr"], -threshold)
            best_rank = (best_fbeta, best_metrics["recall"], best_metrics["precision"], -best_metrics["fpr"], -best_threshold)
            if current_rank > best_rank:
                best_threshold = threshold
                best_metrics = metrics
                best_fbeta = current_fbeta
            continue
        if best_satisfies:
            continue
        current_rank = (-metrics["fpr"], current_fbeta, metrics["recall"], metrics["precision"], -threshold)
        best_rank = (-best_metrics["fpr"], best_fbeta, best_metrics["recall"], best_metrics["precision"], -best_threshold)
        if current_rank > best_rank:
            best_threshold = threshold
            best_metrics = metrics
            best_fbeta = current_fbeta

    if not best_satisfies and best_fallback_satisfies:
        best_threshold = best_fallback_threshold
        best_metrics = best_fallback_metrics

    return round(float(best_threshold), 4), best_metrics


def build_xgb_model(scale_pos_weight: float) -> XGBClassifier:
    return XGBClassifier(
        n_estimators=320,
        max_depth=5,
        learning_rate=0.05,
        subsample=0.85,
        colsample_bytree=0.85,
        reg_lambda=1.5,
        min_child_weight=3,
        random_state=XGB_RANDOM_STATE,
        objective="binary:logistic",
        eval_metric="logloss",
        tree_method="hist",
        n_jobs=1,
        scale_pos_weight=scale_pos_weight,
        missing=np.nan,
    )


def build_walk_forward_folds(
    dates: pd.Series,
    max_folds: int = WALK_FORWARD_MAX_FOLDS,
    min_train_days: int = WALK_FORWARD_MIN_TRAIN_DAYS,
    min_valid_days: int = WALK_FORWARD_MIN_VALID_DAYS,
) -> list[dict[str, Any]]:
    unique_dates = [pd.Timestamp(value) for value in sorted(pd.to_datetime(dates).dt.normalize().unique())]
    if len(unique_dates) < (min_train_days + min_valid_days):
        return []

    remaining_dates = unique_dates[min_train_days:]
    fold_count = min(max_folds, max(1, len(remaining_dates) // min_valid_days))
    if fold_count <= 0:
        return []

    validation_chunks = [list(chunk) for chunk in np.array_split(np.array(remaining_dates, dtype="datetime64[ns]"), fold_count) if len(chunk) > 0]
    folds: list[dict[str, Any]] = []
    train_cutoff = min_train_days
    for fold_index, chunk in enumerate(validation_chunks, start=1):
        validation_dates = [pd.Timestamp(value) for value in chunk]
        train_dates = unique_dates[:train_cutoff]
        if len(train_dates) < min_train_days or len(validation_dates) < min_valid_days:
            train_cutoff += len(validation_dates)
            continue
        folds.append(
            {
                "fold_index": fold_index,
                "train_dates": train_dates,
                "validation_dates": validation_dates,
                "train_start_date": train_dates[0].date().isoformat(),
                "train_end_date": train_dates[-1].date().isoformat(),
                "validation_start_date": validation_dates[0].date().isoformat(),
                "validation_end_date": validation_dates[-1].date().isoformat(),
            }
        )
        train_cutoff += len(validation_dates)
    return folds


def walk_forward_cv(train_frame: pd.DataFrame) -> tuple[list[dict[str, Any]], pd.Series]:
    folds = build_walk_forward_folds(train_frame["usage_date"])
    fold_metrics: list[dict[str, Any]] = []
    oof_probabilities = pd.Series(index=train_frame.index, dtype=float)

    for fold in folds:
        fold_train_mask = train_frame["usage_date"].isin(fold["train_dates"])
        fold_valid_mask = train_frame["usage_date"].isin(fold["validation_dates"])
        fold_train = train_frame.loc[fold_train_mask]
        fold_valid = train_frame.loc[fold_valid_mask]
        if fold_train.empty or fold_valid.empty:
            continue
        if fold_train["supervised_target"].nunique() < 2:
            continue

        positives = int(fold_train["supervised_target"].sum())
        negatives = int(len(fold_train)) - positives
        model = build_xgb_model(scale_pos_weight=max(1.0, negatives / max(positives, 1)))
        model.fit(fold_train[SUPERVISED_FEATURES], fold_train["supervised_target"])

        train_prob = pd.Series(model.predict_proba(fold_train[SUPERVISED_FEATURES])[:, 1], index=fold_train.index)
        fold_threshold, _ = choose_probability_threshold(fold_train["supervised_target"], train_prob)
        valid_prob = pd.Series(model.predict_proba(fold_valid[SUPERVISED_FEATURES])[:, 1], index=fold_valid.index)
        valid_pred = pd.Series(valid_prob >= fold_threshold, index=fold_valid.index)
        metrics = summarize_binary_metrics(fold_valid["supervised_target"], valid_pred)
        oof_probabilities.loc[fold_valid.index] = valid_prob.round(4)
        fold_metrics.append(
            {
                "fold_index": fold["fold_index"],
                "train_start_date": fold["train_start_date"],
                "train_end_date": fold["train_end_date"],
                "validation_start_date": fold["validation_start_date"],
                "validation_end_date": fold["validation_end_date"],
                "train_rows": int(len(fold_train)),
                "validation_rows": int(len(fold_valid)),
                "train_anomaly_rows": positives,
                "validation_anomaly_rows": int(fold_valid["supervised_target"].sum()),
                "threshold": fold_threshold,
                **metrics,
            }
        )

    return fold_metrics, oof_probabilities.dropna()


def build_pair_daily(ce: pd.DataFrame) -> pd.DataFrame:
    pair = ce.sort_values(["linked_account_id", "service_code", "date"]).copy()
    pair["date"] = pd.to_datetime(pair["date"]).dt.normalize()
    pair["is_estimated"] = pair["is_estimated"].fillna(False).astype(bool)
    group_key = ["linked_account_id", "service_code"]
    split_cutoff = temporal_train_cutoff(pair["date"])

    pair["pair_median7"] = pair.groupby(group_key)["unblended_cost"].transform(lambda s: rolling_median(s.shift(1), 7, 3))
    pair["pair_mad7"] = pair.groupby(group_key)["unblended_cost"].transform(lambda s: rolling_mad(s.shift(1), 7, 3))
    pair["pair_lag7"] = pair["pair_median7"]
    pair["pair_ratio7"] = safe_divide(pair["unblended_cost"], pair["pair_median7"])
    pair["pair_delta"] = pair["unblended_cost"] - pair["pair_median7"].fillna(0.0)
    pair["pair_robust_z7"] = safe_divide(pair["pair_delta"], pair["pair_mad7"] * 1.4826)
    pair["day_of_week"] = pair["date"].dt.dayofweek
    pair["is_weekend"] = pair["day_of_week"] >= 5
    pair = pair.rename(columns={"date": "usage_date", "unblended_cost": "pair_cost_24h"})
    pair["is_estimated_int"] = pair["is_estimated"].astype(int)
    pair["dataset_phase"] = np.where(pair["usage_date"] <= split_cutoff, "train", "test")
    pair.attrs["temporal_split"] = {
        "strategy": "time_ordered_train_then_score_all",
        "train_ratio": TEMPORAL_TRAIN_RATIO,
        "train_end_date": split_cutoff.date().isoformat(),
        "test_start_date": (split_cutoff + pd.Timedelta(days=1)).date().isoformat(),
        "train_rows": int(pair["dataset_phase"].eq("train").sum()),
        "test_rows": int(pair["dataset_phase"].eq("test").sum()),
        "baseline_method": "rolling_median_mad",
        "model_type": "xgboost_supervised_binary",
        "feature_scaler": "not_required_tree_model",
        "cross_validation_strategy": "walk_forward_expanding_window",
        "cross_validation_max_folds": WALK_FORWARD_MAX_FOLDS,
    }
    return pair


def resource_family(resource_id: Any) -> str:
    rid = str(resource_id).lower()
    for keyword in ["fbgpu", "flashsale", "loadtest", "migration", "debug", "orphan", "untagged"]:
        if keyword in rid:
            return keyword
    return rid


def build_resource_daily(cur: pd.DataFrame, metrics: pd.DataFrame | None, pair: pd.DataFrame) -> pd.DataFrame:
    agg = {
        "line_item_unblended_cost": "sum",
        "line_item_usage_amount": "sum",
        "pricing_unit": first_non_empty,
        "line_item_usage_type": first_non_empty,
        "line_item_operation": first_non_empty,
        "product_region_code": first_non_empty,
        "product_instance_type": first_non_empty,
        "resource_tags_user_team": first_non_empty,
        "resource_tags_user_environment": first_non_empty,
        "resource_tags_user_cost_center": first_non_empty,
        "resource_tags_user_owner": first_non_empty,
    }
    rd = (
        cur.groupby(
            ["usage_date", "line_item_usage_account_id", "line_item_usage_account_name", "line_item_product_code", "line_item_resource_id"],
            as_index=False,
        )
        .agg(agg)
        .sort_values(["line_item_resource_id", "usage_date"])
        .reset_index(drop=True)
    )

    if metrics is not None and not metrics.empty:
        metric_agg: dict[str, Any] = {}
        for name in DEFAULT_METRIC_COLS:
            if name not in metrics.columns:
                continue
            if name in {"cpu_subhour_max", "has_metrics_int"}:
                metric_agg[name] = "max"
            elif name in {"cpu_subhour_peak_samples", "event_type_present_int"}:
                metric_agg[name] = "sum"
            else:
                metric_agg[name] = "mean"
        if "label" in metrics.columns:
            metric_agg["label"] = prioritized_label
        if "event_type" in metrics.columns:
            metric_agg["event_type"] = first_non_empty
        metric_daily = (
            metrics.groupby(["resource_id", "usage_date"], as_index=False)
            .agg(metric_agg)
            .rename(columns={"resource_id": "line_item_resource_id"})
        )
        rd = rd.merge(metric_daily, on=["line_item_resource_id", "usage_date"], how="left")

    for col in DEFAULT_METRIC_COLS:
        if col not in rd.columns:
            rd[col] = pd.NA
        rd[col] = pd.to_numeric(rd[col], errors="coerce")
    if "label" not in rd.columns:
        rd["label"] = "unknown"
    if "event_type" not in rd.columns:
        rd["event_type"] = "unknown"
    rd["event_type"] = rd["event_type"].fillna("unknown").astype(str).str.strip().replace("", "unknown")

    join_cols = [
        "usage_date",
        "linked_account_id",
        "service_code",
        "pair_cost_24h",
        "pair_lag7",
        "pair_mad7",
        "pair_ratio7",
        "pair_delta",
        "pair_robust_z7",
        "is_estimated",
        "dataset_phase",
    ]
    rd = rd.merge(
        pair[join_cols],
        left_on=["usage_date", "line_item_usage_account_id", "line_item_product_code"],
        right_on=["usage_date", "linked_account_id", "service_code"],
        how="left",
    )

    rd["environment_bucket"] = rd.apply(
        lambda row: "prod"
        if str(row["line_item_usage_account_name"]).startswith("prod")
        else (
            row["line_item_usage_account_name"]
            if str(row["line_item_usage_account_name"]) in {"dev", "staging", "ml-research", "data-analytics", "sandbox"}
            else row["resource_tags_user_environment"]
        ),
        axis=1,
    )
    rd["team_missing"] = rd["resource_tags_user_team"].apply(blank)
    rd["owner_missing"] = rd["resource_tags_user_owner"].apply(blank)
    rd["is_weekend"] = rd["usage_date"].dt.dayofweek >= 5
    rd["day_of_week"] = rd["usage_date"].dt.dayofweek
    rd["first_seen"] = rd.groupby("line_item_resource_id")["usage_date"].transform("min")
    rd["gap"] = rd.groupby("line_item_resource_id")["usage_date"].diff().dt.days
    rd["streak_id"] = rd.groupby("line_item_resource_id")["gap"].transform(lambda s: s.ne(1).cumsum())
    rd["consecutive_days"] = rd.groupby(["line_item_resource_id", "streak_id"]).cumcount() + 1
    rd["weekend_days"] = rd.groupby(["line_item_resource_id", "streak_id"])["is_weekend"].cumsum()
    rd["cost_median14"] = rd.groupby("line_item_resource_id")["line_item_unblended_cost"].transform(lambda s: rolling_median(s, 14, 7))
    rd["cost_mad14"] = rd.groupby("line_item_resource_id")["line_item_unblended_cost"].transform(lambda s: rolling_mad(s, 14, 7))
    rd["cost_mad_ratio14"] = safe_divide(rd["cost_mad14"], rd["cost_median14"])
    rd["usage_density_24h"] = rd.apply(lambda row: float(row["line_item_usage_amount"]) / 24.0 if row["pricing_unit"] == "Hrs" else 1.0, axis=1)
    rd["new_after_warmup"] = rd["first_seen"] >= (rd["usage_date"].min() + pd.Timedelta(days=7))

    rd["resource_cost_lag7"] = rd.groupby("line_item_resource_id")["line_item_unblended_cost"].transform(lambda s: rolling_median(s.shift(1), 7, 3))
    rd["resource_cost_ratio7"] = safe_divide(rd["line_item_unblended_cost"], rd["resource_cost_lag7"])
    rd["resource_cost_delta"] = rd["line_item_unblended_cost"] - rd["resource_cost_lag7"].fillna(0.0)
    rd["resource_cost_mean28"] = rd.groupby("line_item_resource_id")["line_item_unblended_cost"].transform(lambda s: rolling_median(s, 28, 14))
    rd["resource_cost_mean_prev28"] = rd.groupby("line_item_resource_id")["line_item_unblended_cost"].transform(lambda s: rolling_median(s.shift(28), 28, 14))
    rd["resource_cost_mean_ratio_28vprev28"] = safe_divide(rd["resource_cost_mean28"], rd["resource_cost_mean_prev28"])
    rd["resource_usage_lag7"] = rd.groupby("line_item_resource_id")["line_item_usage_amount"].transform(lambda s: rolling_median(s.shift(1), 7, 3))
    rd["resource_usage_ratio7"] = safe_divide(rd["line_item_usage_amount"], rd["resource_usage_lag7"])
    rd["resource_usage_mean28"] = rd.groupby("line_item_resource_id")["line_item_usage_amount"].transform(lambda s: rolling_median(s, 28, 14))
    rd["resource_usage_mean_prev28"] = rd.groupby("line_item_resource_id")["line_item_usage_amount"].transform(lambda s: rolling_median(s.shift(28), 28, 14))
    rd["resource_usage_mean_ratio_28vprev28"] = safe_divide(rd["resource_usage_mean28"], rd["resource_usage_mean_prev28"])
    rd["resource_network_lag7"] = rd.groupby("line_item_resource_id")["network_out_bytes"].transform(lambda s: rolling_median(s.shift(1), 7, 3))
    rd["resource_network_ratio7"] = safe_divide(rd["network_out_bytes"], rd["resource_network_lag7"])
    rd["traffic_growth_signal"] = rd["resource_usage_ratio7"].fillna(0.0).ge(1.2) | rd["resource_network_ratio7"].fillna(0.0).ge(1.2)

    rid_lower = rd["line_item_resource_id"].astype(str).str.lower()
    rd["migration_flag"] = rid_lower.str.contains("migration", regex=False)
    rd["load_test_flag"] = rid_lower.str.contains("loadtest", regex=False)
    rd["campaign_flag"] = rid_lower.str.contains("flashsale", regex=False) | rid_lower.str.contains("campaign", regex=False)
    rd["debug_flag"] = rid_lower.str.contains("debug", regex=False)
    rd["cluster_flag"] = rid_lower.str.contains("fbgpu", regex=False)
    rd["resource_family"] = rd["line_item_resource_id"].map(resource_family)
    rd["benign_growth_signal"] = (
        rd["traffic_growth_signal"]
        & rd["pair_ratio7"].fillna(1.0).ge(1.05)
        & rd["resource_cost_ratio7"].fillna(1.0).ge(1.05)
        & ~rd["debug_flag"]
    )

    rd["supervised_label"] = rd["label"].fillna("unknown").astype(str).str.lower()
    rd["supervised_target"] = rd["supervised_label"].eq("anomaly").astype(int)
    rd["team_missing_int"] = rd["team_missing"].astype(int)
    rd["owner_missing_int"] = rd["owner_missing"].astype(int)
    rd["new_after_warmup_int"] = rd["new_after_warmup"].astype(int)
    rd["migration_flag_int"] = rd["migration_flag"].astype(int)
    rd["load_test_flag_int"] = rd["load_test_flag"].astype(int)
    rd["campaign_flag_int"] = rd["campaign_flag"].astype(int)
    rd["debug_flag_int"] = rd["debug_flag"].astype(int)
    rd["cluster_flag_int"] = rd["cluster_flag"].astype(int)
    rd["benign_growth_signal_int"] = rd["benign_growth_signal"].astype(int)
    rd["is_weekend_int"] = rd["is_weekend"].astype(int)
    rd["is_estimated_int"] = rd["is_estimated"].fillna(False).astype(int)

    rd = encode_categories(
        rd,
        {
            "line_item_product_code": "service_code_encoded",
            "line_item_usage_account_id": "account_encoded",
            "product_region_code": "region_encoded",
            "environment_bucket": "environment_encoded",
            "pricing_unit": "pricing_unit_encoded",
            "line_item_usage_type": "usage_type_encoded",
            "resource_family": "resource_family_encoded",
            "event_type": "event_type_encoded",
        },
        reference_mask=rd["dataset_phase"].eq("train"),
    )

    rd["cost_log"] = np.log1p(rd["line_item_unblended_cost"].clip(lower=0))
    rd["usage_log"] = np.log1p(rd["line_item_usage_amount"].clip(lower=0))
    rd["pair_cost_log"] = np.log1p(rd["pair_cost_24h"].fillna(0.0).clip(lower=0))
    rd["pair_delta_log"] = signed_log1p(rd["pair_delta"])
    rd["cost_median14_log"] = np.log1p(rd["cost_median14"].fillna(0.0).clip(lower=0))
    rd["resource_cost_delta_log"] = signed_log1p(rd["resource_cost_delta"])
    rd["network_in_log"] = np.log1p(rd["network_in_bytes"].fillna(0.0).clip(lower=0))
    rd["network_out_log"] = np.log1p(rd["network_out_bytes"].fillna(0.0).clip(lower=0))
    rd["memory_mib_log"] = np.log1p(rd["memory_mib"].fillna(0.0).clip(lower=0))
    rd["disk_io_ops_log"] = np.log1p(rd["disk_io_ops"].fillna(0.0).clip(lower=0))
    rd["pair_ratio7"] = normalize_ratio(rd["pair_ratio7"])
    rd["resource_cost_ratio7"] = normalize_ratio(rd["resource_cost_ratio7"])
    rd["resource_usage_ratio7"] = normalize_ratio(rd["resource_usage_ratio7"])
    rd["resource_usage_mean_ratio_28vprev28"] = normalize_ratio(rd["resource_usage_mean_ratio_28vprev28"], upper=6.0)
    rd["resource_cost_mean_ratio_28vprev28"] = normalize_ratio(rd["resource_cost_mean_ratio_28vprev28"], upper=6.0)
    rd["resource_network_ratio7"] = normalize_ratio(rd["resource_network_ratio7"])
    rd["pair_robust_z7"] = rd["pair_robust_z7"].fillna(0.0).clip(lower=-10.0, upper=10.0)
    rd["cost_mad_ratio14"] = rd["cost_mad_ratio14"].fillna(0.0).clip(lower=0.0, upper=2.0)
    rd["usage_density_24h"] = rd["usage_density_24h"].fillna(0.0).clip(lower=0.0, upper=2.0)
    rd["cpu_percent"] = rd["cpu_percent"].fillna(0.0).clip(lower=0.0, upper=100.0)
    rd["cpu_subhour_mean"] = rd["cpu_subhour_mean"].fillna(rd["cpu_percent"]).clip(lower=0.0, upper=100.0)
    rd["cpu_subhour_std"] = rd["cpu_subhour_std"].fillna(0.0).clip(lower=0.0, upper=50.0)
    rd["cpu_subhour_max"] = rd["cpu_subhour_max"].fillna(rd["cpu_percent"]).clip(lower=0.0, upper=100.0)
    rd["cpu_subhour_peak_samples"] = rd["cpu_subhour_peak_samples"].fillna(0.0).clip(lower=0.0, upper=288.0)
    rd["gpu_utilization"] = rd["gpu_utilization"].fillna(0.0).clip(lower=0.0, upper=100.0)
    rd["database_connections"] = rd["database_connections"].fillna(0.0).clip(lower=0.0)
    rd["event_type_present_int"] = rd["event_type_present_int"].fillna(0.0).clip(lower=0.0, upper=24.0)
    rd["has_metrics_int"] = rd["has_metrics_int"].fillna(0.0).clip(lower=0.0, upper=1.0)
    rd.attrs["metrics_context"] = {
        "label_source": (metrics.attrs.get("label_source") if metrics is not None else None) or "unlabeled",
        "metrics_variant": (metrics.attrs.get("metrics_variant") if metrics is not None else None) or "none",
        "metrics_dir": (metrics.attrs.get("metrics_dir") if metrics is not None else None),
    }

    return rd


def add_type_evidence(rd: pd.DataFrame) -> pd.DataFrame:
    rd = rd.copy()
    idle_service_context = rd["line_item_product_code"].isin(["AmazonRDS", "AmazonEC2", "AmazonElastiCache", "AmazonSageMaker"])
    debug_spike_context = (
        rd["line_item_product_code"].eq("AmazonCloudWatch")
        & rd["debug_flag"]
        & rd["new_after_warmup"]
        & rd["line_item_unblended_cost"].ge(150.0)
    )
    idle_low_util = (
        idle_service_context
        & (
            ((rd["line_item_product_code"] == "AmazonRDS") & rd["cpu_percent"].le(5.0) & rd["database_connections"].le(1.0))
            | ((rd["line_item_product_code"] == "AmazonEC2") & rd["cpu_percent"].le(10.0) & rd["gpu_utilization"].le(15.0))
            | ((rd["line_item_product_code"] == "AmazonElastiCache") & rd["cpu_percent"].le(8.0) & rd["database_connections"].le(2.0))
            | ((rd["line_item_product_code"] == "AmazonSageMaker") & rd["cpu_percent"].le(6.0))
        )
    )
    runaway_context = (
        rd["line_item_product_code"].isin(["AmazonEC2", "AmazonSageMaker"])
        & rd["line_item_usage_account_name"].isin(["dev", "ml-research"])
    )
    spike_context = rd["line_item_product_code"].isin(["AmazonCloudWatch", "AWSDataTransfer"]) | rd["debug_flag"]
    drift_context = rd["line_item_product_code"].isin(["AmazonDynamoDB", "AmazonElastiCache", "AmazonRDS", "AmazonEC2", "AmazonS3"])

    rd[TYPE_SCORE_NAMES["untagged_spend"]] = (
        rd["team_missing"].astype(float)
        * (
            0.60
            + (rd["line_item_unblended_cost"] / 250.0).clip(upper=0.20)
            + (rd["consecutive_days"] / 60.0).clip(upper=0.15)
            + rd["owner_missing"].astype(float) * 0.05
        )
    ).clip(upper=0.99)
    rd[TYPE_SCORE_NAMES["idle_resource"]] = (
        idle_low_util.astype(float)
        * (
            0.35
            + (1.0 - rd["cost_mad_ratio14"].fillna(1.0).clip(upper=1.0)) * 0.20
            + (rd["consecutive_days"] / 45.0).clip(upper=1.0) * 0.25
            + (rd["line_item_unblended_cost"] / 120.0).clip(upper=0.10)
            + ((rd["environment_bucket"] != "prod") | rd["owner_missing"]).astype(float) * 0.10
        )
    ).clip(upper=0.99)
    rd[TYPE_SCORE_NAMES["runaway_usage"]] = (
        runaway_context.astype(float)
        * (
            0.35
            + rd["new_after_warmup"].astype(float) * 0.10
            + (rd["line_item_unblended_cost"] / 180.0).clip(upper=0.12)
            + rd["usage_density_24h"].clip(upper=1.0) * 0.12
            + (rd["cpu_percent"] / 100.0).clip(upper=1.0) * 0.10
            + (rd["gpu_utilization"] / 100.0).clip(upper=1.0) * 0.12
            + rd["weekend_days"].ge(2).astype(float) * 0.09
        )
    ).clip(upper=0.99)
    rd[TYPE_SCORE_NAMES["sudden_spike"]] = (
        (spike_context | rd["pair_ratio7"].ge(1.25) | rd["resource_cost_ratio7"].ge(1.20) | debug_spike_context).astype(float)
        * (
            0.28
            + (rd["pair_ratio7"].fillna(1.0) - 1.0).clip(lower=0.0, upper=2.0) * 0.18
            + (rd["resource_cost_ratio7"].fillna(1.0) - 1.0).clip(lower=0.0, upper=2.0) * 0.22
            + (rd["pair_robust_z7"].abs() / 8.0).clip(upper=0.15)
            + rd["new_after_warmup"].astype(float) * 0.08
            + debug_spike_context.astype(float) * 0.12
            + rd["debug_flag"].astype(float) * 0.05
        )
    ).clip(upper=0.99)
    rd[TYPE_SCORE_NAMES["gradual_drift"]] = (
        drift_context.astype(float)
        * (
            0.25
            + (rd["resource_cost_mean_ratio_28vprev28"].fillna(1.0) - 1.0).clip(lower=0.0, upper=1.0) * 0.30
            + (rd["resource_usage_mean_ratio_28vprev28"].fillna(1.0) - 1.0).clip(lower=0.0, upper=1.0) * 0.18
            + (1.0 - rd["cost_mad_ratio14"].fillna(1.0).clip(upper=1.0)) * 0.12
            + (rd["consecutive_days"] / 60.0).clip(upper=1.0) * 0.15
        )
    ).clip(upper=0.99)

    type_matrix = rd[list(TYPE_SCORE_NAMES.values())].fillna(0.0)
    inverse_map = {value: key for key, value in TYPE_SCORE_NAMES.items()}
    rd["type_hint_confidence"] = type_matrix.max(axis=1).round(4)
    rd["anomaly_type_hint"] = type_matrix.idxmax(axis=1).map(inverse_map)
    fallback_type = np.select(
        [
            rd["team_missing"],
            runaway_context,
            spike_context,
            drift_context,
        ],
        [
            "untagged_spend",
            "runaway_usage",
            "sudden_spike",
            "gradual_drift",
        ],
        default="idle_resource",
    )
    rd["anomaly_type_hint"] = np.where(rd["type_hint_confidence"].gt(0.0), rd["anomaly_type_hint"], fallback_type)
    return rd


def apply_rule_flags(rd: pd.DataFrame) -> pd.DataFrame:
    metrics_context = dict(rd.attrs.get("metrics_context", {}))
    metrics_variant = metrics_context.get("metrics_variant", "none")
    threshold_floor = float(VARIANT_THRESHOLD_FLOOR.get(metrics_variant, 0.0))
    rd = add_type_evidence(rd)
    rd = rd.copy()
    rd.attrs["metrics_context"] = metrics_context
    rd["xgb_score"] = 0.0
    rd["xgb_candidate"] = False
    rd["cv_oof_score"] = np.nan
    rd["is_holdout_row"] = False

    labeled_mask = rd["supervised_label"].isin(["anomaly", "normal", "benign"])
    train_mask = labeled_mask & rd["dataset_phase"].eq("train")
    test_mask = labeled_mask & rd["dataset_phase"].eq("test")
    rd.loc[test_mask, "is_holdout_row"] = True
    runtime_quality = assess_runtime_quality(rd, train_mask, metrics_context)
    training_summary: dict[str, Any] = {
        "label_source": metrics_context.get("label_source", "unlabeled"),
        "train_labeled_rows": int(train_mask.sum()),
        "test_labeled_rows": int(test_mask.sum()),
        "train_anomaly_rows": int(rd.loc[train_mask, "supervised_target"].sum()),
        "test_anomaly_rows": int(rd.loc[test_mask, "supervised_target"].sum()),
        "cv_strategy": "walk_forward_expanding_window",
        "cv_folds_requested": WALK_FORWARD_MAX_FOLDS,
        "cv_folds_completed": 0,
        "cv_fold_metrics": [],
        "runtime_quality": runtime_quality,
        "runtime_mode": "hybrid_supervised" if runtime_quality["model_enabled"] else "rule_backbone_safe_mode",
    }

    if runtime_quality["model_enabled"] and train_mask.sum() >= 200 and rd.loc[train_mask, "supervised_target"].nunique() == 2:
        train_frame = rd.loc[train_mask].copy()
        fold_metrics, oof_prob = walk_forward_cv(train_frame)
        training_summary["cv_folds_completed"] = len(fold_metrics)
        training_summary["cv_fold_metrics"] = fold_metrics

        threshold_source = "in_sample_train"
        chosen_threshold_before_floor: float | None = None
        if not oof_prob.empty and train_frame.loc[oof_prob.index, "supervised_target"].nunique() == 2:
            cv_threshold, cv_oof_metrics = choose_probability_threshold(train_frame.loc[oof_prob.index, "supervised_target"], oof_prob)
            threshold = cv_threshold
            chosen_threshold_before_floor = cv_threshold
            threshold_source = "walk_forward_oof"

        positives = int(train_frame["supervised_target"].sum())
        negatives = int(len(train_frame)) - positives
        model = build_xgb_model(scale_pos_weight=max(1.0, negatives / max(positives, 1)))
        model.fit(train_frame[SUPERVISED_FEATURES], train_frame["supervised_target"])
        train_prob = pd.Series(model.predict_proba(train_frame[SUPERVISED_FEATURES])[:, 1], index=train_frame.index)
        if threshold_source == "in_sample_train":
            threshold, _ = choose_probability_threshold(train_frame["supervised_target"], train_prob)
            chosen_threshold_before_floor = threshold

        if threshold_floor > 0.0 and threshold < threshold_floor:
            threshold = threshold_floor
            threshold_source = f"{threshold_source}_with_variant_floor"

        if not oof_prob.empty and train_frame.loc[oof_prob.index, "supervised_target"].nunique() == 2:
            rd.loc[oof_prob.index, "cv_oof_score"] = oof_prob.round(4)
            training_summary["cv_oof_rows"] = int(len(oof_prob))
            training_summary["cv_oof_anomaly_rows"] = int(train_frame.loc[oof_prob.index, "supervised_target"].sum())
            training_summary["cv_oof_metrics"] = summarize_binary_metrics(
                train_frame.loc[oof_prob.index, "supervised_target"],
                pd.Series(oof_prob >= threshold, index=oof_prob.index),
            )

        train_pred = pd.Series(train_prob >= threshold, index=train_prob.index)
        train_metrics = summarize_binary_metrics(train_frame["supervised_target"], train_pred)
        all_prob = pd.Series(model.predict_proba(rd[SUPERVISED_FEATURES])[:, 1], index=rd.index)
        rd["xgb_score"] = all_prob.round(4)
        rd["xgb_candidate"] = rd["xgb_score"].ge(threshold)
        training_summary["threshold"] = threshold
        training_summary["threshold_before_variant_floor"] = chosen_threshold_before_floor
        training_summary["threshold_floor_applied"] = threshold_floor > 0.0 and (chosen_threshold_before_floor or 0.0) < threshold_floor
        training_summary["threshold_floor_value"] = threshold_floor if threshold_floor > 0.0 else None
        training_summary["threshold_source"] = threshold_source
        training_summary["train_metrics"] = train_metrics
        if test_mask.any():
            test_prob = rd.loc[test_mask, "xgb_score"]
            test_pred = pd.Series(test_prob >= threshold, index=test_prob.index)
            training_summary["test_model_only_metrics"] = summarize_binary_metrics(rd.loc[test_mask, "supervised_target"], test_pred)
    else:
        training_summary["threshold"] = 0.8
        training_summary["threshold_source"] = "quality_gate_rule_backbone" if not runtime_quality["model_enabled"] else "heuristic_fallback"
        rd["xgb_candidate"] = rd["type_hint_confidence"].ge(0.70)
        training_summary["threshold_before_variant_floor"] = None
        training_summary["threshold_floor_applied"] = False
        training_summary["threshold_floor_value"] = threshold_floor if threshold_floor > 0.0 else None

    cost_only_rule_context = rd["has_metrics_int"].le(0.0)
    untagged_rule = (
        cost_only_rule_context
        &
        rd[TYPE_SCORE_NAMES["untagged_spend"]].ge(0.72)
        & rd["team_missing"]
        & rd["line_item_unblended_cost"].ge(40.0)
        & rd["consecutive_days"].ge(4)
    )
    idle_rule = pd.Series(False, index=rd.index)
    runaway_rule = (
        cost_only_rule_context
        &
        rd[TYPE_SCORE_NAMES["runaway_usage"]].ge(0.74)
        & rd["consecutive_days"].ge(3)
        & rd["line_item_unblended_cost"].ge(20.0)
    )
    debug_spike_rule = (
        cost_only_rule_context
        &
        rd["line_item_product_code"].eq("AmazonCloudWatch")
        & rd["debug_flag"]
        & rd["new_after_warmup"]
        & rd["line_item_unblended_cost"].ge(150.0)
        & rd["consecutive_days"].ge(4)
    )
    spike_rule = (
        cost_only_rule_context
        &
        rd[TYPE_SCORE_NAMES["sudden_spike"]].ge(0.62)
        & (
            rd["pair_ratio7"].fillna(1.0).ge(1.15)
            | rd["resource_cost_ratio7"].fillna(1.0).ge(1.15)
            | debug_spike_rule
        )
    )
    drift_rule = pd.Series(False, index=rd.index)
    rd["rule_candidate"] = untagged_rule | idle_rule | runaway_rule | spike_rule | drift_rule
    rd["rule_support_score"] = pd.concat(
        [
            rd[TYPE_SCORE_NAMES["untagged_spend"]].where(untagged_rule, 0.0),
            rd[TYPE_SCORE_NAMES["idle_resource"]].where(idle_rule, 0.0),
            rd[TYPE_SCORE_NAMES["runaway_usage"]].where(runaway_rule, 0.0),
            rd[TYPE_SCORE_NAMES["sudden_spike"]].where(spike_rule, 0.0),
            rd[TYPE_SCORE_NAMES["gradual_drift"]].where(drift_rule, 0.0),
        ],
        axis=1,
    ).max(axis=1).fillna(0.0)

    domain_guard = (
        rd["type_hint_confidence"].ge(0.32)
        | rd["team_missing"]
        | rd["line_item_unblended_cost"].ge(80.0)
        | rd["resource_cost_ratio7"].fillna(1.0).ge(1.25)
        | rd["resource_cost_mean_ratio_28vprev28"].fillna(1.0).ge(1.25)
        | rd["rule_candidate"]
    )
    rd["xgb_candidate"] = rd["xgb_candidate"] & domain_guard
    estimated = rd["is_estimated"].astype("boolean").fillna(False)
    rd.loc[estimated, "xgb_score"] = rd.loc[estimated, "xgb_score"].clip(upper=0.49)
    rd["supervised_only_prediction"] = rd["xgb_candidate"].astype(int)
    rd["predicted_anomaly"] = ((rd["xgb_candidate"] | rd["rule_candidate"]) & domain_guard).astype(int)
    rd["detector_score_raw"] = np.maximum(rd["xgb_score"].fillna(0.0), rd["rule_support_score"].fillna(0.0)).clip(upper=0.99)
    if test_mask.any():
        training_summary["test_supervised_only_metrics"] = summarize_binary_metrics(
            rd.loc[test_mask, "supervised_target"],
            rd.loc[test_mask, "supervised_only_prediction"],
        )
        training_summary["test_pipeline_metrics"] = summarize_binary_metrics(rd.loc[test_mask, "supervised_target"], rd.loc[test_mask, "predicted_anomaly"])
    if train_mask.any():
        train_pipeline_mask = train_mask & rd["cv_oof_score"].notna()
        if train_pipeline_mask.any():
            cv_pipeline_pred = ((rd.loc[train_pipeline_mask, "cv_oof_score"] >= float(training_summary["threshold"])) & domain_guard.loc[train_pipeline_mask]).astype(int)
            cv_supervised_only_pred = cv_pipeline_pred.copy()
            cv_hybrid_pred = (cv_supervised_only_pred | rd.loc[train_pipeline_mask, "rule_candidate"].astype(int)).astype(int)
            training_summary["cv_oof_supervised_only_metrics"] = summarize_binary_metrics(
                rd.loc[train_pipeline_mask, "supervised_target"],
                cv_supervised_only_pred,
            )
            training_summary["cv_oof_pipeline_metrics"] = summarize_binary_metrics(
                rd.loc[train_pipeline_mask, "supervised_target"],
                cv_hybrid_pred,
            )
            training_summary["cv_oof_hybrid_metrics"] = training_summary["cv_oof_pipeline_metrics"]
    training_summary["hybrid_rule_trigger_rows"] = int(rd["rule_candidate"].sum())
    training_summary["hybrid_rule_only_rows"] = int(((rd["predicted_anomaly"] == 1) & (rd["supervised_only_prediction"] == 0)).sum())

    rd.attrs["metrics_context"] = metrics_context
    rd.attrs["supervised_training"] = training_summary
    return rd


def collapse_supervised_events(rd: pd.DataFrame) -> list[dict[str, Any]]:
    flagged = rd.loc[rd["predicted_anomaly"].eq(1)].copy()
    if flagged.empty:
        return []

    flagged["event_gap"] = flagged.groupby("line_item_resource_id")["usage_date"].diff().dt.days
    flagged["event_id"] = flagged.groupby("line_item_resource_id")["event_gap"].transform(lambda s: s.fillna(1).gt(2).cumsum())

    rows: list[dict[str, Any]] = []
    for (_, _), group in flagged.groupby(["line_item_resource_id", "event_id"]):
        group = group.sort_values("usage_date")
        mean_type_scores = {name: float(group[column].mean()) for name, column in TYPE_SCORE_NAMES.items()}
        event_type = max(mean_type_scores, key=mean_type_scores.get)
        event_span_days = int((group["usage_date"].max() - group["usage_date"].min()).days + 1)
        candidate_days = int(len(group))
        min_days = EVENT_MIN_DAYS[event_type]
        if candidate_days < max(2, math.ceil(min_days * 0.5)) and event_span_days < min_days:
            continue

        peak = group.loc[group["detector_score_raw"].idxmax()]
        last = group.iloc[-1]
        phases = sorted({str(value) for value in group["dataset_phase"].dropna().unique()})
        dataset_phase = phases[0] if len(phases) == 1 else ("cross_split" if phases else "unknown")
        pair_baseline = peak["pair_lag7"] if pd.notna(peak["pair_lag7"]) and peak["pair_lag7"] else 1.0
        resource_baseline = peak["resource_cost_lag7"] if pd.notna(peak["resource_cost_lag7"]) and peak["resource_cost_lag7"] else 1.0
        xgb_score = safe_round(group["xgb_score"].max(), 4) or 0.0
        rule_score = safe_round(group["rule_support_score"].max(), 4) or 0.0
        candidate_sources = ["heuristic_type_mapper"]
        if bool(group["xgb_candidate"].any()):
            candidate_sources.insert(0, "xgboost_supervised")
        if bool(group["rule_candidate"].any()):
            candidate_sources.insert(0, "hybrid_rule_backbone")
        rows.append(
            {
                "anomaly_type": event_type,
                "resource_id": str(last["line_item_resource_id"]),
                "primary_resource_id": str(last["line_item_resource_id"]),
                "impacted_resource_ids": [str(last["line_item_resource_id"])],
                "impacted_resource_count": 1,
                "incident_scope": "single_resource",
                "resource_family": str(last["resource_family"]),
                "account_id": int(last["line_item_usage_account_id"]),
                "account_name": str(last["line_item_usage_account_name"]),
                "environment": str(last["environment_bucket"]),
                "service_code": str(last["line_item_product_code"]),
                "usage_type": str(last["line_item_usage_type"]),
                "pricing_unit": str(last["pricing_unit"]),
                "team": None if blank(last["resource_tags_user_team"]) else str(last["resource_tags_user_team"]),
                "owner": None if blank(last["resource_tags_user_owner"]) else str(last["resource_tags_user_owner"]),
                "cost_center": None if blank(last["resource_tags_user_cost_center"]) else str(last["resource_tags_user_cost_center"]),
                "event_start": group["usage_date"].min(),
                "event_end": group["usage_date"].max(),
                "event_days": event_span_days,
                "candidate_days": candidate_days,
                "event_total_cost": round(float(group["line_item_unblended_cost"].sum()), 2),
                "avg_daily_cost": round(float(group["line_item_unblended_cost"].mean()), 2),
                "latest_daily_cost": round(float(last["line_item_unblended_cost"]), 2),
                "dataset_phase": dataset_phase,
                "usage_amount_24h": round(float(last["line_item_usage_amount"]), 4),
                "usage_density_24h": round(float(last["usage_density_24h"]), 4),
                "cost_ratio_to_7d_avg": round(float(last["line_item_unblended_cost"]) / float(pair_baseline), 2),
                "resource_cost_ratio_to_7d_avg": round(float(last["line_item_unblended_cost"]) / float(resource_baseline), 2),
                "resource_usage_ratio_to_7d_avg": safe_round(last["resource_usage_ratio7"]),
                "resource_network_ratio_to_7d_avg": safe_round(last["resource_network_ratio7"]),
                "resource_cost_mean_ratio_28vprev28": safe_round(last["resource_cost_mean_ratio_28vprev28"]),
                "resource_usage_mean_ratio_28vprev28": safe_round(last["resource_usage_mean_ratio_28vprev28"]),
                "detector_score": round(float(group["detector_score_raw"].max()), 2),
                "confidence_score": round(float(group["detector_score_raw"].max()), 2),
                "ranking_score": round(float(group["detector_score_raw"].max()), 2),
                "type_hint_confidence": round(float(group["type_hint_confidence"].max()), 2),
                "is_estimated": bool(last["is_estimated"]) if pd.notna(last["is_estimated"]) else False,
                "xgb_support": bool(group["xgb_candidate"].any()),
                "rule_support": bool(group["rule_candidate"].any()),
                "xgb_score": xgb_score,
                "rule_support_score": rule_score,
                "candidate_sources": candidate_sources,
                "cpu_percent": safe_round(last["cpu_percent"]),
                "gpu_utilization": safe_round(last["gpu_utilization"]),
                "database_connections": safe_round(last["database_connections"]),
                "network_out_bytes": safe_round(last["network_out_bytes"], 0),
                "migration_flag": bool(group["migration_flag"].any()),
                "load_test_flag": bool(group["load_test_flag"].any()),
                "campaign_flag": bool(group["campaign_flag"].any()),
                "benign_growth_signal": bool(group["benign_growth_signal"].any()),
            }
        )
    return rows


def build_candidate_events(rd: pd.DataFrame, flag_col: str = "predicted_anomaly") -> pd.DataFrame:
    if flag_col == "predicted_anomaly":
        return pd.DataFrame(collapse_supervised_events(rd))

    alt = rd.copy()
    alt["predicted_anomaly"] = alt[flag_col].astype(int)
    if flag_col == "supervised_only_prediction":
        alt["rule_candidate"] = False
        alt["rule_support_score"] = 0.0
        alt["detector_score_raw"] = alt["xgb_score"].fillna(0.0)
    return pd.DataFrame(collapse_supervised_events(alt))


def apply_fp_suppressor(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return events.copy()

    events = events.copy()
    events["suppressed"] = False
    events["suppression_reason"] = ""
    events["review_only"] = False
    events["benign_context_penalty"] = 0.0
    events["estimated_penalty"] = events["is_estimated"].astype(float) * 0.20

    migration_mask = events["migration_flag"] & events["anomaly_type"].isin(["sudden_spike", "runaway_usage", "gradual_drift"])
    load_test_mask = events["load_test_flag"] & events["anomaly_type"].isin(["sudden_spike", "runaway_usage", "gradual_drift"])
    campaign_mask = events["campaign_flag"] & events["anomaly_type"].isin(["sudden_spike", "runaway_usage", "gradual_drift"])
    benign_growth_mask = (
        events["benign_growth_signal"]
        & events["anomaly_type"].eq("sudden_spike")
        & events["resource_cost_ratio_to_7d_avg"].fillna(1.0).lt(1.18)
    )
    aggregate_spillover_mask = (
        events["anomaly_type"].eq("sudden_spike")
        & events["resource_cost_ratio_to_7d_avg"].fillna(1.0).lt(1.12)
        & events["detector_score"].fillna(0.0).lt(0.80)
        & events["resource_family"].ne("debug")
    )

    events.loc[migration_mask, ["suppressed", "suppression_reason"]] = [True, "scheduled_migration_context"]
    events.loc[load_test_mask, ["suppressed", "suppression_reason"]] = [True, "scheduled_load_test_context"]
    events.loc[campaign_mask, ["suppressed", "suppression_reason"]] = [True, "planned_campaign_context"]
    events.loc[benign_growth_mask, ["suppressed", "suppression_reason"]] = [True, "benign_growth_with_traffic"]
    events.loc[aggregate_spillover_mask, ["suppressed", "suppression_reason"]] = [True, "aggregate_spillover_without_resource_jump"]

    benign_context = events[["migration_flag", "load_test_flag", "campaign_flag", "benign_growth_signal"]].any(axis=1)
    events.loc[benign_context, "benign_context_penalty"] = 0.25
    events.loc[events["is_estimated"], "review_only"] = True
    events.loc[events["is_estimated"], "detector_score"] = events.loc[events["is_estimated"], "detector_score"].clip(upper=0.49)
    events.loc[events["is_estimated"], "confidence_score"] = events.loc[events["is_estimated"], "confidence_score"].clip(upper=0.49)
    return events


def group_related_events(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return events.copy()

    active = events.loc[~events["suppressed"]].copy()
    if active.empty:
        return active

    rows: list[dict[str, Any]] = []
    for _, base_group in active.groupby(["anomaly_type", "account_id", "service_code", "environment", "resource_family"], dropna=False):
        base_group = base_group.sort_values(["event_start", "event_end", "event_total_cost"], ascending=[True, True, False]).reset_index(drop=True)
        base_group["start_gap"] = base_group["event_start"].diff().dt.days
        base_group["end_gap"] = base_group["event_end"].diff().dt.days
        base_group["incident_id"] = ((base_group["start_gap"].fillna(0).abs() > 1) | (base_group["end_gap"].fillna(0).abs() > 1)).cumsum()

        for _, incident_group in base_group.groupby("incident_id"):
            primary = incident_group.sort_values(["event_total_cost", "detector_score"], ascending=False).iloc[0]
            impacted_resource_ids = sorted({rid for values in incident_group["impacted_resource_ids"] for rid in values})
            incident_days = int((incident_group["event_end"].max() - incident_group["event_start"].min()).days + 1)
            incident_total_cost = round(float(incident_group["event_total_cost"].sum()), 2)
            row = primary.to_dict()
            row["resource_id"] = primary["resource_id"]
            row["primary_resource_id"] = primary["resource_id"]
            row["impacted_resource_ids"] = impacted_resource_ids
            row["impacted_resource_count"] = len(impacted_resource_ids)
            row["incident_scope"] = "multi_resource_cluster" if len(impacted_resource_ids) > 1 else "single_resource"
            row["event_start"] = incident_group["event_start"].min()
            row["event_end"] = incident_group["event_end"].max()
            row["event_days"] = incident_days
            row["event_total_cost"] = incident_total_cost
            row["avg_daily_cost"] = round(incident_total_cost / max(incident_days, 1), 2)
            row["latest_daily_cost"] = round(float(incident_group["latest_daily_cost"].sum()), 2)
            row["usage_amount_24h"] = round(float(incident_group["usage_amount_24h"].sum()), 4)
            row["team"] = collapse_identity(incident_group["team"])
            row["owner"] = collapse_identity(incident_group["owner"])
            row["cost_center"] = collapse_identity(incident_group["cost_center"])
            row["detector_score"] = round(float(incident_group["detector_score"].max()), 2)
            row["confidence_score"] = round(float(incident_group["confidence_score"].max()), 2)
            row["ranking_score"] = round(float(incident_group["confidence_score"].max()), 2)
            row["type_hint_confidence"] = round(float(incident_group["type_hint_confidence"].max()), 2)
            row["xgb_support"] = bool(incident_group["xgb_support"].any())
            row["rule_support"] = bool(incident_group["rule_support"].any()) if "rule_support" in incident_group else False
            row["xgb_score"] = safe_round(incident_group["xgb_score"].max(), 4) or 0.0
            row["rule_support_score"] = safe_round(incident_group["rule_support_score"].max(), 4) or 0.0
            row["candidate_sources"] = sorted({name for names in incident_group["candidate_sources"] for name in names})
            row["migration_flag"] = bool(incident_group["migration_flag"].any())
            row["load_test_flag"] = bool(incident_group["load_test_flag"].any())
            row["campaign_flag"] = bool(incident_group["campaign_flag"].any())
            row["benign_growth_signal"] = bool(incident_group["benign_growth_signal"].any())
            row["estimated_penalty"] = float(incident_group["estimated_penalty"].max())
            row["benign_context_penalty"] = float(incident_group["benign_context_penalty"].max())
            rows.append(row)
    return pd.DataFrame(rows)


def cooldown_dedup_events(events: pd.DataFrame, cooldown_days: int = INCIDENT_COOLDOWN_DAYS) -> pd.DataFrame:
    if events.empty:
        return events.copy()

    rows: list[dict[str, Any]] = []
    group_keys = ["anomaly_type", "resource_id", "service_code", "environment"]
    for _, resource_group in events.sort_values(["event_start", "event_end"]).groupby(group_keys, dropna=False):
        resource_group = resource_group.sort_values(["event_start", "event_end"]).reset_index(drop=True)
        current: dict[str, Any] | None = None
        recurrence_count = 0
        for row in resource_group.to_dict(orient="records"):
            row_start = pd.Timestamp(row["event_start"])
            row_end = pd.Timestamp(row["event_end"])
            if current is None:
                current = dict(row)
                recurrence_count = 1
                continue

            current_end = pd.Timestamp(current["event_end"])
            gap_days = int((row_start - current_end).days)
            if gap_days <= cooldown_days:
                recurrence_count += 1
                current["event_start"] = min(pd.Timestamp(current["event_start"]), row_start)
                current["event_end"] = max(current_end, row_end)
                current["event_days"] = int((pd.Timestamp(current["event_end"]) - pd.Timestamp(current["event_start"])).days + 1)
                current["event_total_cost"] = round(float(current["event_total_cost"]) + float(row["event_total_cost"]), 2)
                current["avg_daily_cost"] = round(float(current["event_total_cost"]) / max(current["event_days"], 1), 2)
                current["latest_daily_cost"] = round(max(float(current["latest_daily_cost"]), float(row["latest_daily_cost"])), 2)
                current["usage_amount_24h"] = round(float(current["usage_amount_24h"]) + float(row["usage_amount_24h"]), 4)
                current["detector_score"] = round(max(float(current["detector_score"]), float(row["detector_score"])), 2)
                current["confidence_score"] = round(max(float(current["confidence_score"]), float(row["confidence_score"])), 2)
                current["ranking_score"] = round(max(float(current["ranking_score"]), float(row["ranking_score"])), 2)
                current["type_hint_confidence"] = round(max(float(current["type_hint_confidence"]), float(row["type_hint_confidence"])), 2)
                current["xgb_support"] = bool(current["xgb_support"] or row["xgb_support"])
                current["rule_support"] = bool(current["rule_support"] or row["rule_support"])
                current["xgb_score"] = safe_round(max(float(current["xgb_score"]), float(row["xgb_score"])), 4) or 0.0
                current["rule_support_score"] = safe_round(max(float(current["rule_support_score"]), float(row["rule_support_score"])), 4) or 0.0
                current["candidate_sources"] = sorted(set(current["candidate_sources"]) | set(row["candidate_sources"]))
                current["impacted_resource_ids"] = sorted(set(current["impacted_resource_ids"]) | set(row["impacted_resource_ids"]))
                current["impacted_resource_count"] = len(current["impacted_resource_ids"])
                current["incident_scope"] = "multi_resource_cluster" if current["impacted_resource_count"] > 1 else "single_resource"
                current["estimated_penalty"] = round(max(float(current["estimated_penalty"]), float(row["estimated_penalty"])), 2)
                current["benign_context_penalty"] = round(max(float(current["benign_context_penalty"]), float(row["benign_context_penalty"])), 2)
                current["recurrence_count"] = recurrence_count
                continue

            current["recurrence_count"] = recurrence_count
            rows.append(current)
            current = dict(row)
            recurrence_count = 1

        if current is not None:
            current["recurrence_count"] = recurrence_count
            rows.append(current)

    return pd.DataFrame(rows)


def rerank_events(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return events.copy()

    events = events.copy()
    env_risk_map = {"prod": 0.10, "staging": 0.06, "data-analytics": 0.05, "ml-research": 0.04, "dev": 0.03, "sandbox": 0.02}
    missing_tag_score = (
        events["team"].apply(blank).astype(float)
        + events["owner"].apply(blank).astype(float)
        + events["cost_center"].apply(blank).astype(float)
    ) / 3.0 * 0.08
    cost_impact_score = (events["event_total_cost"] / 4000.0).clip(upper=0.20)
    duration_score = (events["event_days"] / 21.0).clip(upper=1.0) * 0.10
    environment_risk_score = events["environment"].map(env_risk_map).fillna(0.03)
    detector_score = events["detector_score"].clip(lower=0.0, upper=0.99)
    multi_resource_bonus = (events["impacted_resource_count"].fillna(1).astype(float) - 1.0).clip(lower=0.0, upper=4.0) * 0.02
    type_bonus = events["type_hint_confidence"].fillna(0.0).clip(lower=0.0, upper=0.99) * 0.04
    recurrence_bonus = (events.get("recurrence_count", pd.Series(1, index=events.index)).fillna(1).astype(float) - 1.0).clip(lower=0.0, upper=3.0) * 0.02

    events["cost_impact_score"] = cost_impact_score.round(2)
    events["duration_score"] = duration_score.round(2)
    events["missing_tag_score"] = missing_tag_score.round(2)
    events["environment_risk_score"] = environment_risk_score.round(2)
    events["estimated_penalty"] = events["estimated_penalty"].fillna(0.0).round(2)
    events["benign_context_penalty"] = events["benign_context_penalty"].fillna(0.0).round(2)

    final_score = (
        detector_score
        + cost_impact_score
        + duration_score
        + missing_tag_score
        + environment_risk_score
        + multi_resource_bonus
        + type_bonus
        + recurrence_bonus
        - events["estimated_penalty"]
        - events["benign_context_penalty"]
    )
    events["ranking_score"] = final_score.clip(lower=0.0, upper=0.99).round(2)
    events["confidence_score"] = events["ranking_score"]
    events.loc[events["is_estimated"], "confidence_score"] = events.loc[events["is_estimated"], "confidence_score"].clip(upper=0.49)
    return events.sort_values(["confidence_score", "event_total_cost"], ascending=False).reset_index(drop=True)


def precision_cleanup_events(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return events.copy()

    events = events.copy()
    keep_mask = (
        events["rule_support"].fillna(False)
        | events["confidence_score"].fillna(0.0).ge(PRECISION_CLEANUP_MIN_CONFIDENCE)
        | events["event_total_cost"].fillna(0.0).ge(PRECISION_CLEANUP_MIN_COST)
        | events["environment"].isin(["prod", "staging"])
    )
    events["precision_cleanup_dropped"] = ~keep_mask
    return events.loc[keep_mask].sort_values(["confidence_score", "event_total_cost"], ascending=False).reset_index(drop=True)


def detect_events(ce: pd.DataFrame, cur: pd.DataFrame, metrics: pd.DataFrame | None) -> pd.DataFrame:
    pair = build_pair_daily(ce)
    rd = build_resource_daily(cur, metrics, pair)
    scored = apply_rule_flags(rd)
    candidates = build_candidate_events(scored, flag_col="predicted_anomaly")
    suppressed = apply_fp_suppressor(candidates)
    incidents = cooldown_dedup_events(group_related_events(suppressed))
    ranked = precision_cleanup_events(rerank_events(incidents))
    supervised_only_events = precision_cleanup_events(
        rerank_events(
            cooldown_dedup_events(
                group_related_events(
                    apply_fp_suppressor(
                        build_candidate_events(scored, flag_col="supervised_only_prediction")
                    )
                )
            )
        )
    )
    temporal_split = dict(pair.attrs.get("temporal_split", {}))
    temporal_split["training_summary"] = scored.attrs.get("supervised_training", {})
    temporal_split["label_source"] = temporal_split["training_summary"].get("label_source", "unlabeled")
    temporal_split["metrics_variant"] = scored.attrs.get("metrics_context", {}).get("metrics_variant", "none")
    temporal_split["metrics_dir"] = scored.attrs.get("metrics_context", {}).get("metrics_dir")
    temporal_split["runtime_mode"] = temporal_split["training_summary"].get("runtime_mode")
    temporal_split["runtime_quality"] = temporal_split["training_summary"].get("runtime_quality", {})
    ranked["runtime_mode"] = temporal_split["runtime_mode"]
    ranked["runtime_quality_tier"] = temporal_split["runtime_quality"].get("quality_tier", "unknown")
    ranked.attrs["temporal_split"] = temporal_split
    ranked.attrs["evaluation_bundle"] = build_evaluation_bundle(scored, temporal_split)
    ranked.attrs["supervised_only_events"] = supervised_only_events
    return ranked


def compact_training_summary(training_summary: dict[str, Any]) -> dict[str, Any]:
    if not training_summary:
        return {}
    return {
        "label_source": training_summary.get("label_source"),
        "runtime_mode": training_summary.get("runtime_mode"),
        "runtime_quality": training_summary.get("runtime_quality"),
        "train_labeled_rows": training_summary.get("train_labeled_rows"),
        "test_labeled_rows": training_summary.get("test_labeled_rows"),
        "train_anomaly_rows": training_summary.get("train_anomaly_rows"),
        "test_anomaly_rows": training_summary.get("test_anomaly_rows"),
        "cv_strategy": training_summary.get("cv_strategy"),
        "cv_folds_completed": training_summary.get("cv_folds_completed"),
        "threshold_source": training_summary.get("threshold_source"),
        "threshold": training_summary.get("threshold"),
        "threshold_before_variant_floor": training_summary.get("threshold_before_variant_floor"),
        "threshold_floor_applied": training_summary.get("threshold_floor_applied"),
        "threshold_floor_value": training_summary.get("threshold_floor_value"),
        "cv_oof_metrics": training_summary.get("cv_oof_metrics"),
        "cv_oof_supervised_only_metrics": training_summary.get("cv_oof_supervised_only_metrics"),
        "cv_oof_pipeline_metrics": training_summary.get("cv_oof_pipeline_metrics"),
        "test_model_only_metrics": training_summary.get("test_model_only_metrics"),
        "test_supervised_only_metrics": training_summary.get("test_supervised_only_metrics"),
        "test_pipeline_metrics": training_summary.get("test_pipeline_metrics"),
        "hybrid_rule_trigger_rows": training_summary.get("hybrid_rule_trigger_rows"),
        "hybrid_rule_only_rows": training_summary.get("hybrid_rule_only_rows"),
    }


def build_evaluation_bundle(scored: pd.DataFrame, temporal_split: dict[str, Any]) -> dict[str, Any]:
    training_summary = temporal_split.get("training_summary", {})
    holdout_mask = scored["is_holdout_row"].astype(bool) & scored["supervised_label"].isin(["anomaly", "normal", "benign"])
    holdout_predictions = scored.loc[
        holdout_mask,
        [
            "usage_date",
            "line_item_resource_id",
            "line_item_product_code",
            "environment_bucket",
            "supervised_label",
            "supervised_target",
            "xgb_score",
            "supervised_only_prediction",
            "predicted_anomaly",
            "rule_candidate",
            "rule_support_score",
            "anomaly_type_hint",
            "type_hint_confidence",
        ],
    ].copy()
    holdout_predictions = holdout_predictions.rename(
        columns={
            "line_item_resource_id": "resource_id",
            "line_item_product_code": "service_code",
            "environment_bucket": "environment",
            "supervised_only_prediction": "predicted_anomaly_supervised_only",
            "predicted_anomaly": "predicted_anomaly_pipeline",
        }
    )
    if not holdout_predictions.empty:
        holdout_predictions["usage_date"] = pd.to_datetime(holdout_predictions["usage_date"]).dt.date.astype(str)

    cv_oof_predictions = scored.loc[
        scored["cv_oof_score"].notna(),
        [
            "usage_date",
            "line_item_resource_id",
            "line_item_product_code",
            "environment_bucket",
            "supervised_label",
            "supervised_target",
            "cv_oof_score",
            "anomaly_type_hint",
            "type_hint_confidence",
        ],
    ].copy()
    cv_oof_predictions = cv_oof_predictions.rename(
        columns={
            "line_item_resource_id": "resource_id",
            "line_item_product_code": "service_code",
            "environment_bucket": "environment",
        }
    )
    if not cv_oof_predictions.empty:
        cv_oof_predictions["usage_date"] = pd.to_datetime(cv_oof_predictions["usage_date"]).dt.date.astype(str)

    split_summary = {
        "strategy": temporal_split.get("strategy"),
        "train_ratio": temporal_split.get("train_ratio"),
        "train_end_date": temporal_split.get("train_end_date"),
        "test_start_date": temporal_split.get("test_start_date"),
        "train_rows_account_service": temporal_split.get("train_rows"),
        "test_rows_account_service": temporal_split.get("test_rows"),
        "baseline_method": temporal_split.get("baseline_method"),
        "model_type": temporal_split.get("model_type"),
        "feature_scaler": temporal_split.get("feature_scaler"),
        "cross_validation_strategy": temporal_split.get("cross_validation_strategy"),
        "cross_validation_max_folds": temporal_split.get("cross_validation_max_folds"),
        "label_source": temporal_split.get("label_source"),
        "metrics_variant": temporal_split.get("metrics_variant"),
        "runtime_mode": training_summary.get("runtime_mode"),
        "runtime_quality_tier": training_summary.get("runtime_quality", {}).get("quality_tier"),
    }

    return {
        "split_summary": split_summary,
        "training_summary_compact": compact_training_summary(training_summary),
        "training_summary_full": training_summary,
        "cv_fold_metrics": pd.DataFrame(training_summary.get("cv_fold_metrics", [])),
        "holdout_predictions": holdout_predictions,
        "cv_oof_predictions": cv_oof_predictions,
        "holdout_test_metrics": {
            "model_only": training_summary.get("test_model_only_metrics", {}),
            "supervised_only": training_summary.get("test_supervised_only_metrics", {}),
            "pipeline": training_summary.get("test_pipeline_metrics", {}),
        },
        "method_comparison": {
            "supervised_only": training_summary.get("test_supervised_only_metrics", {}),
            "hybrid_pipeline": training_summary.get("test_pipeline_metrics", {}),
            "cv_supervised_only": training_summary.get("cv_oof_supervised_only_metrics", {}),
            "cv_hybrid_pipeline": training_summary.get("cv_oof_pipeline_metrics", {}),
            "hybrid_rule_trigger_rows": training_summary.get("hybrid_rule_trigger_rows", 0),
            "hybrid_rule_only_rows": training_summary.get("hybrid_rule_only_rows", 0),
        },
    }


def mitigation_for(event: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    rid = event["resource_id"]
    env = event["environment"]
    runtime_mode = event.get("runtime_mode", "unknown")
    strategy = "Prod-safe tag and escalate" if env == "prod" else "Alert-only review"
    action_type = "tag-for-review"
    action = "tag-for-review"
    parameters: dict[str, Any] = {"tag_key": "FinOps_Alert", "tag_value": "Review_Required"}
    countdown = {"time_lock_seconds": 0, "fallback_action": ""}
    post_state = "tagged"

    if runtime_mode == "rule_backbone_safe_mode":
        strategy = "Runtime quality degraded - review only"
        action_type = "manual-review-only"
        action = "manual-review-only"
        parameters = {}
        post_state = "review-only"
    elif event["is_estimated"]:
        strategy = "Estimated-data safe mode"
        action_type = "review-estimated-cost"
        parameters = {}
    elif env == "staging":
        strategy = "Time-gated containment"
        action_type = "schedule-shutdown-review"
        parameters = {"delay_seconds": 14400}
        countdown = {"time_lock_seconds": 14400, "fallback_action": "schedule-shutdown"}
    elif env in {"dev", "sandbox", "ml-research"} and event["confidence_score"] >= 0.86 and event["incident_scope"] == "single_resource" and rid.startswith("i-"):
        strategy = "Auto-shutdown request in low-risk environment"
        action_type = "stop-instance-request"
        action = "auto-shutdown-request"
        parameters = {"instance_ids": [rid], "reason": "finops_runaway_usage"}
        post_state = "pending-stop"
    elif env == "data-analytics":
        strategy = "Quota-cap and human review"
        action_type = "restrict-quota-request"
        action = "quota-cap-request"
        parameters = {"service_code": event["service_code"], "reason": "finops_cost_spike"}
        post_state = "pending-restriction"

    return (
        {
            "strategy": strategy,
            "immediate_action": action,
            "applied_payload": {
                "action_type": action_type,
                "resource_id": rid,
                "parameters": parameters,
            },
            "enforcement_countdown": countdown,
        },
        {
            "action_triggered_by": "system_auto_containment" if action in {"auto-shutdown-request", "quota-cap-request"} else "human_operator",
            "pre_action_state": "running",
            "post_action_state": post_state,
            "execution_iam_role": f"arn:aws:iam::{event['account_id']}:role/FinOpsEngineExecutionRole",
            "rollback_script_encapsulated": "",
        },
    )


def apply_rca_generation(record: dict[str, Any], event: dict[str, Any], fallback: dict[str, str], rca_generator: Any | None) -> dict[str, Any]:
    root = record["engineering_dashboard_data"]["root_cause_analysis"]
    root["recommended_follow_up"] = fallback["recommended_follow_up"]
    root["llm_provider"] = "disabled"
    root["llm_model_id"] = "none"
    root["llm_latency_ms"] = None
    root["llm_error"] = None
    if rca_generator is None:
        return record

    generation = rca_generator.generate(event, fallback)
    record["finance_dashboard_data"]["executive_summary"] = generation.executive_summary
    root["primary_driver_feature"] = generation.primary_driver_feature
    root["technical_reason"] = generation.technical_reason
    root["recommended_follow_up"] = generation.recommended_follow_up
    root["rca_generation_mode"] = "bedrock_llm" if generation.status == "generated" else "fallback_rule_template"
    root["llm_rca_status"] = generation.status
    root["llm_provider"] = generation.provider
    root["llm_model_id"] = generation.model_id
    root["llm_latency_ms"] = generation.latency_ms
    root["llm_error"] = generation.error
    return record


def record_from_event(event: dict[str, Any], index: int, rca_generator: Any | None = None) -> dict[str, Any]:
    reasons = {
        "untagged_spend": (
            "resource_tags_user_team",
            "Cost is accruing without a team tag, so finance allocation is incomplete.",
        ),
        "idle_resource": (
            "database_connections" if event["service_code"] == "AmazonRDS" else "cpu_percent",
            "Spend is stable while utilization remains near zero, which matches an idle-resource pattern.",
        ),
        "runaway_usage": (
            "gpu_utilization" if event.get("gpu_utilization") is not None else "cpu_percent",
            "New compute workload is running continuously with no cooldown, which matches runaway usage.",
        ),
        "sudden_spike": (
            "resource_cost_ratio_to_7d_avg",
            "Daily spend jumped sharply versus its own recent baseline and stayed elevated.",
        ),
        "gradual_drift": (
            "resource_cost_mean_ratio_28vprev28",
            "Spend has climbed for multiple weeks without a sharp one-day jump, which matches gradual drift.",
        ),
    }
    missing = [
        name
        for name, value in {
            "resource_tags_user_team": event.get("team"),
            "resource_tags_user_owner": event.get("owner"),
            "resource_tags_user_cost_center": event.get("cost_center"),
        }.items()
        if blank(value)
    ]
    mitigation, audit = mitigation_for(event)
    anomaly_id = f"ANM-{pd.Timestamp(event['event_end']).strftime('%Y%m%d')}-{chr(65 + (index % 26))}"
    driver, reason = reasons[event["anomaly_type"]]
    fallback = {
        "primary_driver_feature": driver,
        "technical_reason": reason,
        "executive_summary": (
            f"{event['resource_id']} is costing about ${event['avg_daily_cost']:.2f}/day over {event['event_days']} days "
            f"and matches {event['anomaly_type']}."
        ),
        "recommended_follow_up": "Review the owning team, confirm intent, and validate whether containment should proceed.",
    }
    record = {
        "anomaly_metadata": {
            "anomaly_id": anomaly_id,
            "timestamp": pd.Timestamp(event["event_end"]).tz_localize("UTC").isoformat().replace("+00:00", "Z"),
            "resource_id": event["resource_id"],
            "environment": event["environment"],
            "dataset_phase": event.get("dataset_phase", "unknown"),
            "confidence_score": event["confidence_score"],
            "detector_score": event["detector_score"],
            "ranking_score": event["ranking_score"],
            "ai_model_used": MODEL_NAME,
            "runtime_mode": event.get("runtime_mode", "unknown"),
            "runtime_quality_tier": event.get("runtime_quality_tier", "unknown"),
            "supporting_detectors": event["candidate_sources"],
            "incident_scope": event["incident_scope"],
            "impacted_resource_count": event["impacted_resource_count"],
        },
        "finance_dashboard_data": {
            "target_recipient": "Finance Team & CFO Dashboard",
            "metrics": {
                "unblended_cost_24h_usd": event["latest_daily_cost"],
                "cost_ratio_to_7d_avg": event["cost_ratio_to_7d_avg"],
                "projected_monthly_waste_usd": round(float(event["avg_daily_cost"]) * 30.0, 2),
            },
            "allocation": {
                "responsible_team": event["team"] or "unassigned",
                "cost_center_code": event["cost_center"] or "unknown",
            },
            "executive_summary": fallback["executive_summary"],
        },
        "engineering_dashboard_data": {
            "target_recipient": "Engineering Console & Slack Alert",
            "technical_context": {
                "aws_service": event["service_code"],
                "usage_type": event["usage_type"],
                "pricing_unit": event["pricing_unit"],
                "usage_amount_24h": event["usage_amount_24h"],
                "usage_density_24h": event["usage_density_24h"],
                "resource_cost_ratio_to_7d_avg": event["resource_cost_ratio_to_7d_avg"],
                "resource_cost_mean_ratio_28vprev28": event.get("resource_cost_mean_ratio_28vprev28"),
                "resource_usage_mean_ratio_28vprev28": event.get("resource_usage_mean_ratio_28vprev28"),
                "xgb_score": event["xgb_score"],
                "type_hint_confidence": event.get("type_hint_confidence"),
                "impacted_resource_ids": event["impacted_resource_ids"],
            },
            "root_cause_analysis": {
                "primary_driver_feature": fallback["primary_driver_feature"],
                "technical_reason": fallback["technical_reason"],
                "missing_mandatory_tags": missing,
                "rca_generation_mode": "placeholder_rule_template",
                "llm_rca_status": "disabled",
            },
            "mitigation_action": mitigation,
            "audit_trail_context": audit,
        },
    }
    return apply_rca_generation(record, event, fallback, rca_generator)


def build_result(events: pd.DataFrame, audit_id: str, rca_generator: Any | None = None) -> dict[str, Any]:
    if events.empty:
        return {
            "audit_id": audit_id,
            "status": "completed",
            "total_anomalies_found": 0,
            "anomalies_list": [],
            "pagination": {"next_token": None, "limit": 50},
            "temporal_split": events.attrs.get("temporal_split", {}),
        }

    ordered = events.sort_values(["confidence_score", "event_total_cost"], ascending=False).to_dict(orient="records")
    records = [record_from_event(row, index, rca_generator=rca_generator) for index, row in enumerate(ordered)]
    return {
        "audit_id": audit_id,
        "status": "completed",
        "total_anomalies_found": len(records),
        "anomalies_list": records,
        "pagination": {"next_token": None, "limit": 50},
        "temporal_split": events.attrs.get("temporal_split", {}),
    }


def evaluate_public(events: pd.DataFrame, labels_path: Path) -> dict[str, Any]:
    labels = pd.read_csv(labels_path, parse_dates=["start_date", "end_date"])
    matched: list[str] = []
    missed: list[str] = []
    benign_hits: list[str] = []
    anomaly_support = int(labels["label"].eq("anomaly").sum())
    benign_support = int(labels["label"].ne("anomaly").sum())

    for _, label in labels.iterrows():
        if events.empty:
            overlap = pd.DataFrame()
        else:
            resource_match = (events["resource_id"] == label["resource_id"]) | events["impacted_resource_ids"].apply(
                lambda ids: label["resource_id"] in ids if isinstance(ids, list) else False
            )
            overlap = events[
                resource_match
                & (events["service_code"] == label["service"])
                & (pd.to_datetime(events["event_start"]) <= label["end_date"])
                & (pd.to_datetime(events["event_end"]) >= label["start_date"])
            ]
        if label["label"] == "anomaly":
            (matched if not overlap.empty else missed).append(label["anomaly_id"])
        elif not overlap.empty:
            benign_hits.append(label["anomaly_id"])

    tp = len(matched)
    fn = len(missed)
    fp = len(benign_hits)
    public_fp_rate = None if benign_support == 0 else round(fp / benign_support, 4)
    public_recall = None if anomaly_support == 0 else round(tp / anomaly_support, 4)
    return {
        "matched_anomalies": matched,
        "missed_anomalies": missed,
        "benign_hits": benign_hits,
        "tp_public": tp,
        "fn_public": fn,
        "fp_public": fp,
        "anomaly_support_public": anomaly_support,
        "benign_support_public": benign_support,
        "public_recall": public_recall,
        "public_false_positive_rate": public_fp_rate,
        "fp_requirement_max": FP_TARGET_MAX,
        "fp_requirement_passed": public_fp_rate is None or public_fp_rate <= FP_TARGET_MAX,
        "evaluation_note": "This supervised sandbox run uses metrics.label for training; final FP<=10% still needs full backtest disclosure.",
    }
