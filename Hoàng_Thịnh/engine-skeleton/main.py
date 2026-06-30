from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import FastAPI, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field, model_validator

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from detect_core import (
    MODEL_NAME,
    build_result,
    detect_events,
    evaluate_public,
    load_local_metrics,
    normalize_timestamp,
    parse_cpu_hourly_samples,
    resolve_metrics_dir,
    validate_label_catalog,
)
from llm_rca import load_rca_generator


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "docs" / "assets" / "detect"
STORE: dict[str, dict[str, Any]] = {}
IDEMPOTENCY_INDEX: dict[tuple[str, str], dict[str, Any]] = {}
RCA_GENERATOR = load_rca_generator()

app = FastAPI(title="FinOps Watch Detector", version="1.2.0")


class DetectRequest(BaseModel):
    data_source_type: str = Field(pattern="^(RAW_JSON|S3_POINTER)$")
    is_ad_hoc: bool = False
    aws_cost_explorer_daily: list[dict[str, Any]]
    aws_cur_line_items: list[dict[str, Any]] | None = None
    s3_bucket_uri: str | None = None

    @model_validator(mode="after")
    def validate_mode(self) -> "DetectRequest":
        if self.data_source_type == "RAW_JSON" and not self.aws_cur_line_items:
            raise ValueError("aws_cur_line_items is required for RAW_JSON")
        if self.data_source_type == "S3_POINTER" and not self.s3_bucket_uri:
            raise ValueError("s3_bucket_uri is required for S3_POINTER")
        return self


class ExtendRequest(BaseModel):
    audit_id: str
    extend_seconds: int = Field(gt=0, le=86400)
    reason: str = Field(min_length=3, max_length=300)
    anomaly_id: str | None = None


class RollbackRequest(BaseModel):
    audit_id: str
    requested_by_user: str = Field(min_length=3, max_length=200)
    justification_on_rollback: str = Field(min_length=3, max_length=500)
    anomaly_id: str | None = None


DetectRequest.model_rebuild()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_rfc3339(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, pd.Timestamp):
        if value.tzinfo is None:
            return value.isoformat()
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def payload_hash(payload: DetectRequest) -> str:
    raw = payload.model_dump(mode="json")
    text = json.dumps(json_safe(raw), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def resolve_pointer_path(uri: str) -> Path:
    if uri.startswith("s3://local/"):
        candidate = ROOT / uri.removeprefix("s3://local/")
    elif uri.startswith("file://"):
        candidate = Path(uri.removeprefix("file://"))
    else:
        candidate = Path(uri)
        if not candidate.is_absolute():
            candidate = ROOT / candidate
    candidate = candidate.resolve()
    if not str(candidate).startswith(str(ROOT.resolve())):
        raise HTTPException(status_code=400, detail="ERR_INVALID_S3_POINTER_PATH")
    if not candidate.exists():
        raise HTTPException(status_code=404, detail="ERR_S3_POINTER_NOT_FOUND")
    return candidate


def load_cur_from_pointer(uri: str) -> list[dict[str, Any]]:
    path = resolve_pointer_path(uri)
    suffixes = path.suffixes
    if suffixes[-2:] == [".json", ".gz"]:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    elif suffixes[-2:] == [".csv", ".gz"]:
        return pd.read_csv(path).to_dict(orient="records")
    elif path.suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
    elif path.suffix == ".csv":
        return pd.read_csv(path).to_dict(orient="records")
    else:
        raise HTTPException(status_code=400, detail="ERR_UNSUPPORTED_S3_POINTER_FORMAT")

    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and "aws_cur_line_items" in payload:
        return payload["aws_cur_line_items"]
    raise HTTPException(status_code=400, detail="ERR_INVALID_S3_POINTER_PAYLOAD")


def build_frames(payload: DetectRequest) -> tuple[pd.DataFrame, pd.DataFrame]:
    ce = pd.DataFrame(payload.aws_cost_explorer_daily)
    cur_records = payload.aws_cur_line_items or []
    if payload.data_source_type == "S3_POINTER":
        cur_records = load_cur_from_pointer(payload.s3_bucket_uri or "")
    cur = pd.DataFrame(cur_records)
    if ce.empty or cur.empty:
        raise HTTPException(status_code=400, detail="Both cost explorer and CUR data are required.")
    ce["date"] = pd.to_datetime(ce["date"]).dt.normalize()
    cur["line_item_usage_start_date"] = pd.to_datetime(cur["line_item_usage_start_date"])
    cur["usage_date"] = cur["line_item_usage_start_date"].dt.tz_localize(None).dt.normalize()
    return ce, cur


def compute_telemetry_quality(ce: pd.DataFrame, cur: pd.DataFrame, metrics: pd.DataFrame | None = None) -> dict[str, Any]:
    required_cur = [
        "line_item_usage_start_date",
        "line_item_usage_account_id",
        "line_item_product_code",
        "line_item_resource_id",
        "line_item_unblended_cost",
    ]
    completeness = float(cur[required_cur].notna().all(axis=1).mean()) if not cur.empty else 0.0
    estimated = bool(ce["is_estimated"].fillna(False).any()) if "is_estimated" in ce.columns else False
    latest_ce = pd.to_datetime(ce["date"]).max() if not ce.empty else None
    latest_cur = pd.to_datetime(cur["usage_date"]).max() if not cur.empty else None
    freshest = max([value for value in [latest_ce, latest_cur] if value is not None], default=None)
    max_expected = max([value for value in [latest_ce, latest_cur] if value is not None], default=None)
    freshness = 1.0
    if freshest is not None and max_expected is not None:
        age_days = max((max_expected - freshest).days, 0)
        freshness = max(0.0, 1.0 - (age_days / 2.0))
    integrity = 1.0 if not cur.empty and not ce.empty else 0.0
    metrics_rows = int(len(metrics)) if metrics is not None else 0
    metrics_variant = metrics.attrs.get("metrics_variant", "none") if metrics is not None else "none"
    metrics_label_source = metrics.attrs.get("label_source", "unlabeled") if metrics is not None else "unlabeled"
    return {
        "cur_status": "HEALTHY" if not cur.empty else "MISSING",
        "cost_explorer_status": "HEALTHY" if not ce.empty else "STALE",
        "metrics_status": "HEALTHY" if metrics_rows > 0 else "MISSING",
        "metrics_variant": metrics_variant,
        "metrics_label_source": metrics_label_source,
        "metrics_rows": metrics_rows,
        "completeness_score": round(completeness, 3),
        "freshness_score": round(freshness, 3),
        "integrity_score": round(integrity, 3),
        "is_forced_dry_run": bool(completeness < 0.80 or estimated),
    }


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
    if metrics_dir is None:
        direct_metrics = [data_dir / "new_metrics.csv", data_dir / "metrics.csv"]
        for metrics_path in direct_metrics:
            if metrics_path.exists():
                return load_direct_metrics(metrics_path)
        return None
    return load_local_metrics(metrics_dir)


def load_default_metrics() -> pd.DataFrame | None:
    return load_metrics_for_dir(DATA_DIR)


def generate_rollback_payload(anomaly: dict[str, Any]) -> dict[str, Any]:
    applied = anomaly["engineering_dashboard_data"]["mitigation_action"]["applied_payload"]
    action_type = applied["action_type"]
    resource_id = applied["resource_id"]
    if action_type == "tag-for-review":
        return {
            "action_type": "remove-review-tag",
            "resource_id": resource_id,
            "parameters": {"tag_key": "FinOps_Alert"},
        }
    if action_type == "schedule-shutdown-review":
        return {
            "action_type": "cancel-shutdown-review",
            "resource_id": resource_id,
            "parameters": {"restore_state": "running"},
        }
    if action_type == "stop-instance-request":
        return {
            "action_type": "start-instance-request",
            "resource_id": resource_id,
            "parameters": {"instance_ids": applied["parameters"].get("instance_ids", [resource_id])},
        }
    if action_type == "restrict-quota-request":
        return {
            "action_type": "restore-quota-request",
            "resource_id": resource_id,
            "parameters": {"service_code": applied["parameters"].get("service_code", "")},
        }
    return {
        "action_type": "manual-review-only",
        "resource_id": resource_id,
        "parameters": {},
    }


def init_action_state(result: dict[str, Any], created_at: str) -> dict[str, Any]:
    created_dt = parse_rfc3339(created_at)
    state: dict[str, Any] = {}
    for anomaly in result["anomalies_list"]:
        anomaly_id = anomaly["anomaly_metadata"]["anomaly_id"]
        countdown = anomaly["engineering_dashboard_data"]["mitigation_action"]["enforcement_countdown"]["time_lock_seconds"]
        expires_at = None
        if countdown:
            expires_at = (created_dt + timedelta(seconds=int(countdown))).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        state[anomaly_id] = {
            "expires_at": expires_at,
            "rollback_payload": generate_rollback_payload(anomaly),
            "rolled_back": False,
            "extensions": [],
        }
    return state


def persist_run(
    payload: DetectRequest,
    tenant_id: str,
    request_hash: str,
    x_idempotency_key: str | None,
    ce: pd.DataFrame,
    cur: pd.DataFrame,
) -> dict[str, Any]:
    audit_id = str(uuid.uuid4())
    metrics = load_default_metrics()
    events = detect_events(ce, cur, metrics)
    result = build_result(events, audit_id=audit_id, rca_generator=RCA_GENERATOR)
    telemetry_quality = compute_telemetry_quality(ce, cur, metrics)
    split_meta = result.get("temporal_split", {})
    evaluation_bundle = events.attrs.get("evaluation_bundle", {})
    result["telemetry_quality"] = telemetry_quality
    result["processing_context"] = {
        "is_ad_hoc": payload.is_ad_hoc,
        "data_source_type": payload.data_source_type,
        "temporal_split": split_meta,
    }
    result["llm_context"] = {"provider": RCA_GENERATOR.provider, "model_id": RCA_GENERATOR.model_id, "enabled": RCA_GENERATOR.enabled}
    result["evaluation_context"] = {
        "split_summary": evaluation_bundle.get("split_summary", {}),
        "training_summary_compact": evaluation_bundle.get("training_summary_compact", {}),
        "method_comparison": evaluation_bundle.get("method_comparison", {}),
    }
    created_at = utc_now()
    STORE[audit_id] = {
        "tenant_id": tenant_id,
        "result": result,
        "events": events.to_dict(orient="records") if not events.empty else [],
        "created_at": created_at,
        "request_hash": request_hash,
        "idempotency_key": x_idempotency_key,
        "telemetry_quality": telemetry_quality,
        "action_state": init_action_state(result, created_at),
        "action_history": [],
    }
    if x_idempotency_key and not payload.is_ad_hoc:
        IDEMPOTENCY_INDEX[(tenant_id, x_idempotency_key)] = {"audit_id": audit_id, "request_hash": request_hash}
    return result


def safe_json_response(result: dict[str, Any], start: int | None = None, end: int | None = None, limit: int | None = None) -> dict[str, Any]:
    response = json_safe(result)
    if start is not None and end is not None and limit is not None:
        response["anomalies_list"] = response["anomalies_list"][start:end]
        response["pagination"] = {"next_token": str(end) if end < response["total_anomalies_found"] else None, "limit": limit}
    return response


def run_demo(write_output: bool = True, data_dir: Path | None = None) -> dict[str, Any]:
    dataset_dir = data_dir.resolve() if data_dir is not None else DATA_DIR
    cost_path = resolve_dataset_file(dataset_dir, ["cost_explorer_daily.csv", "new_cost_explorer_daily.csv"])
    cur_path = resolve_dataset_file(dataset_dir, ["cur_line_items.csv", "new_cur_line_items.csv"])
    labels_path = resolve_dataset_file(dataset_dir, ["anomaly_labels_public.csv", "anomaly_labels_test.csv"])
    payload = DetectRequest(
        data_source_type="RAW_JSON",
        aws_cost_explorer_daily=pd.read_csv(cost_path).to_dict(orient="records"),
        aws_cur_line_items=pd.read_csv(cur_path).to_dict(orient="records"),
    )
    ce, cur = build_frames(payload)
    metrics = load_metrics_for_dir(dataset_dir)
    events = detect_events(ce, cur, metrics)
    result = build_result(events, audit_id=str(uuid.uuid4()), rca_generator=RCA_GENERATOR)
    split_meta = result.get("temporal_split", {})
    evaluation_bundle = events.attrs.get("evaluation_bundle", {})
    result["telemetry_quality"] = compute_telemetry_quality(ce, cur, metrics)
    result["processing_context"] = {"is_ad_hoc": False, "data_source_type": "RAW_JSON", "temporal_split": split_meta}
    result["llm_context"] = {"provider": RCA_GENERATOR.provider, "model_id": RCA_GENERATOR.model_id, "enabled": RCA_GENERATOR.enabled}
    result["evaluation_context"] = {
        "split_summary": evaluation_bundle.get("split_summary", {}),
        "training_summary_compact": evaluation_bundle.get("training_summary_compact", {}),
        "method_comparison": evaluation_bundle.get("method_comparison", {}),
    }
    public_eval = evaluate_public(events, labels_path)
    label_catalog_validation = validate_label_catalog(cur, labels_path)
    public_eval["label_catalog_validation"] = label_catalog_validation
    supervised_only_events = events.attrs.get("supervised_only_events", pd.DataFrame())
    public_eval_by_method = {
        "hybrid_pipeline": public_eval,
        "supervised_only": {
            **evaluate_public(supervised_only_events, labels_path),
            "label_catalog_validation": label_catalog_validation,
        },
    }
    output = {
        "result": result,
        "public_eval": public_eval,
        "public_eval_by_method": public_eval_by_method,
        "model_name": MODEL_NAME,
        "dataset_dir": str(dataset_dir),
    }
    if write_output:
        output_dir = OUTPUT_DIR if dataset_dir == DATA_DIR else OUTPUT_DIR / dataset_dir.name
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "demo_result.json").write_text(json.dumps(json_safe(output), indent=2, ensure_ascii=False), encoding="utf-8")
        if not events.empty:
            events.to_csv(output_dir / "detected_events.csv", index=False)
        (output_dir / "public_eval.json").write_text(json.dumps(public_eval, indent=2, ensure_ascii=False), encoding="utf-8")
        (output_dir / "method_comparison.json").write_text(
            json.dumps(json_safe({"holdout": evaluation_bundle.get("method_comparison", {}), "public": public_eval_by_method}), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        training_summary = evaluation_bundle.get("training_summary_full", {})
        (output_dir / "split_summary.json").write_text(json.dumps(json_safe(evaluation_bundle.get("split_summary", {})), indent=2, ensure_ascii=False), encoding="utf-8")
        (output_dir / "cv_summary.json").write_text(json.dumps(json_safe(training_summary), indent=2, ensure_ascii=False), encoding="utf-8")
        (output_dir / "holdout_test_metrics.json").write_text(
            json.dumps(json_safe(evaluation_bundle.get("holdout_test_metrics", {})), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        cv_fold_metrics = evaluation_bundle.get("cv_fold_metrics", pd.DataFrame())
        if not cv_fold_metrics.empty:
            cv_fold_metrics.to_csv(output_dir / "fold_metrics.csv", index=False)
        holdout_predictions = evaluation_bundle.get("holdout_predictions", pd.DataFrame())
        if not holdout_predictions.empty:
            holdout_predictions.to_csv(output_dir / "holdout_predictions.csv", index=False)
        cv_oof_predictions = evaluation_bundle.get("cv_oof_predictions", pd.DataFrame())
        if not cv_oof_predictions.empty:
            cv_oof_predictions.to_csv(output_dir / "cv_oof_predictions.csv", index=False)
    return output


def find_anomalies(stored: dict[str, Any], anomaly_id: str | None = None) -> list[dict[str, Any]]:
    anomalies = stored["result"]["anomalies_list"]
    if anomaly_id:
        matches = [item for item in anomalies if item["anomaly_metadata"]["anomaly_id"] == anomaly_id]
        if not matches:
            raise HTTPException(status_code=404, detail="ERR_ANOMALY_NOT_FOUND")
        return matches
    return anomalies


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "healthy",
        "timestamp": utc_now(),
        "services": {"detector": "ready", "audit_store": "in_memory", "action_store": "in_memory"},
        "model_name": MODEL_NAME,
        "llm": {"provider": RCA_GENERATOR.provider, "model_id": RCA_GENERATOR.model_id, "enabled": RCA_GENERATOR.enabled},
    }


@app.post("/v1/detect")
def detect(
    payload: DetectRequest,
    response: Response,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    x_idempotency_key: str | None = Header(default=None, alias="X-Idempotency-Key"),
) -> dict[str, Any]:
    tenant_id = x_tenant_id or "demo-tenant"
    request_hash = payload_hash(payload)
    if x_idempotency_key and not payload.is_ad_hoc:
        existing = IDEMPOTENCY_INDEX.get((tenant_id, x_idempotency_key))
        if existing:
            if existing["request_hash"] != request_hash:
                raise HTTPException(status_code=400, detail="ERR_IDEMPOTENCY_MISMATCH")
            response.status_code = 200
            return {
                "audit_id": existing["audit_id"],
                "status": "completed",
                "retry_after_seconds": 0,
                "message": "Cached detection returned for the same idempotency key.",
                "created_at": STORE[existing["audit_id"]]["created_at"],
                "model_name": MODEL_NAME,
            }

    ce, cur = build_frames(payload)
    result = persist_run(payload, tenant_id, request_hash, x_idempotency_key, ce, cur)
    response.status_code = 202
    return {
        "audit_id": result["audit_id"],
        "status": "processing",
        "retry_after_seconds": 1,
        "message": "Detection accepted.",
        "created_at": STORE[result["audit_id"]]["created_at"],
        "model_name": MODEL_NAME,
    }


@app.get("/v1/detect/result/{audit_id}")
def detect_result(
    audit_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    next_token: str | None = Query(default=None),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> dict[str, Any]:
    stored = STORE.get(audit_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="ERR_AUDIT_NOT_FOUND")
    if x_tenant_id and stored["tenant_id"] != x_tenant_id:
        raise HTTPException(status_code=403, detail="ERR_CROSS_TENANT_DENIED")
    start = int(next_token) if next_token else 0
    end = start + limit
    result = safe_json_response(stored["result"], start=start, end=end, limit=limit)
    result["action_history"] = json_safe(stored["action_history"])
    return result


@app.post("/v1/action/extend")
def action_extend(
    payload: ExtendRequest,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> dict[str, Any]:
    stored = STORE.get(payload.audit_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="ERR_AUDIT_NOT_FOUND")
    if x_tenant_id and stored["tenant_id"] != x_tenant_id:
        raise HTTPException(status_code=403, detail="ERR_CROSS_TENANT_DENIED")

    target_anomalies = find_anomalies(stored, payload.anomaly_id)
    extendable = [
        item
        for item in target_anomalies
        if item["engineering_dashboard_data"]["mitigation_action"]["enforcement_countdown"]["time_lock_seconds"] > 0
    ]
    if not extendable:
        raise HTTPException(status_code=422, detail="ERR_EXTEND_NOT_SUPPORTED")

    affected: list[str] = []
    new_expirations: list[str] = []
    for anomaly in extendable:
        anomaly_id = anomaly["anomaly_metadata"]["anomaly_id"]
        state = stored["action_state"][anomaly_id]
        base = parse_rfc3339(state["expires_at"] or stored["created_at"])
        extended = (base + timedelta(seconds=payload.extend_seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        state["expires_at"] = extended
        state["extensions"].append({"extended_at": utc_now(), "extend_seconds": payload.extend_seconds, "reason": payload.reason})
        affected.append(anomaly_id)
        new_expirations.append(extended)

    stored["action_history"].append(
        {
            "action": "extend",
            "at": utc_now(),
            "reason": payload.reason,
            "extend_seconds": payload.extend_seconds,
            "affected_anomaly_ids": affected,
        }
    )
    return {
        "audit_id": payload.audit_id,
        "status": "extended",
        "affected_anomaly_ids": affected,
        "new_expiration_time": max(new_expirations),
        "message": "Countdown extended successfully.",
    }


@app.post("/v1/action/rollback")
def action_rollback(
    payload: RollbackRequest,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> dict[str, Any]:
    stored = STORE.get(payload.audit_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="ERR_AUDIT_NOT_FOUND")
    if x_tenant_id and stored["tenant_id"] != x_tenant_id:
        raise HTTPException(status_code=403, detail="ERR_CROSS_TENANT_DENIED")

    target_anomalies = find_anomalies(stored, payload.anomaly_id)
    anomaly = target_anomalies[0]
    anomaly_id = anomaly["anomaly_metadata"]["anomaly_id"]
    state = stored["action_state"][anomaly_id]
    if state["rolled_back"]:
        raise HTTPException(status_code=422, detail="ERR_ALREADY_ROLLED_BACK")

    state["rolled_back"] = True
    rollback_payload = state["rollback_payload"]
    stored["action_history"].append(
        {
            "action": "rollback",
            "at": utc_now(),
            "requested_by_user": payload.requested_by_user,
            "justification_on_rollback": payload.justification_on_rollback,
            "affected_anomaly_ids": [anomaly_id],
        }
    )
    return {
        "audit_id": payload.audit_id,
        "status": "rollback_initiated",
        "affected_anomaly_ids": [anomaly_id],
        "rollback_payload": rollback_payload,
        "message": "Rollback payload generated for review and execution.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-write-output", action="store_true")
    parser.add_argument("--data-dir", type=str, default=None)
    args = parser.parse_args()
    data_dir = Path(args.data_dir) if args.data_dir else None
    if data_dir is not None and not data_dir.is_absolute():
        data_dir = (ROOT / data_dir).resolve()
    output = run_demo(write_output=not args.no_write_output, data_dir=data_dir)
    result = output["result"]
    split_meta = result.get("temporal_split", {})
    training_summary = split_meta.get("training_summary", {})
    print(f"model_name={MODEL_NAME}")
    print(f"dataset_dir={output.get('dataset_dir')}")
    print(f"audit_id={result['audit_id']}")
    print(f"total_anomalies_found={result['total_anomalies_found']}")
    print(
        "split_summary="
        f"train_end={split_meta.get('train_end_date')} "
        f"test_start={split_meta.get('test_start_date')} "
        f"cv={split_meta.get('cross_validation_strategy')} "
        f"folds={training_summary.get('cv_folds_completed')}"
    )
    print(
        "threshold_summary="
        f"source={training_summary.get('threshold_source')} "
        f"value={training_summary.get('threshold')} "
        f"holdout_pipeline={training_summary.get('test_pipeline_metrics')}"
    )
    print(
        "public_fp_gate="
        f"rate={output['public_eval'].get('public_false_positive_rate')} "
        f"max={output['public_eval'].get('fp_requirement_max')} "
        f"passed={output['public_eval'].get('fp_requirement_passed')}"
    )
    print(
        "artifact_bundle="
        "demo_result.json detected_events.csv public_eval.json split_summary.json "
        "cv_summary.json fold_metrics.csv holdout_test_metrics.json holdout_predictions.csv cv_oof_predictions.csv"
    )


if __name__ == "__main__":
    main()
