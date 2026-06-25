"""
API schemas for POST /v1/detect — Synchronous anomaly detection.
=================================================================
Contract ref: ai-api-contract.md v1.3.0 §5.1

CDO sends telemetry data (CUR primary + optional CE fallback + optional CloudWatch).
AI Engine returns 200 OK with full DetectResponse (anomalies_list + data_confidence).

Schema fields match 1:1 with telemetry-contract.md v3.1.0 §6, §7, §8.
"""

from __future__ import annotations

from datetime import date as date_type, datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request — Cost Explorer daily signal (telemetry-contract §6)
# Conditional: only required when telemetry_delay_event = true
# ---------------------------------------------------------------------------

class CostExplorerItem(BaseModel):
    """Single CE record = 1 service × 1 day × 1 region.
    Matches telemetry-contract v3.1.0 §6.1 JSON Schema exactly.
    v1.2.0: Demoted from required to conditional (CDO-P5).
    """
    date: date_type = Field(description="Date of the cost record (YYYY-MM-DD)")
    linked_account_id: str = Field(pattern=r"^[0-9]{12}$")
    linked_account_name: Optional[str] = None
    service_code: str = Field(description="CUR short code: AmazonEC2, AmazonRDS")
    service: str = Field(description="CE display name: 'Amazon Elastic Compute Cloud - Compute'")
    region: str = Field(description="e.g. us-east-1, ap-southeast-1")
    unblended_cost: float = Field(ge=0.0, description="Daily cost in USD")
    cost_ratio_to_7d_avg: float = Field(ge=0.0, description="unblended_cost / rolling_7d_avg")
    day_of_week: int = Field(ge=0, le=6, description="0=Mon, 6=Sun")
    is_weekend: bool
    is_estimated: bool = Field(
        default=False,
        description="true for last 2 days (CUR not finalized). AI Engine lowers confidence.",
    )


# ---------------------------------------------------------------------------
# Request — CUR line items signal (telemetry-contract §7)
# Source of truth for detection — primary data source
# ---------------------------------------------------------------------------

class CURLineItem(BaseModel):
    """Single CUR record = 1 resource × 1 day.
    Source of truth for detection. Matches telemetry-contract v3.1.0 §7.1 JSON Schema.
    """
    bill_billing_period_start_date: Optional[datetime] = None
    line_item_usage_start_date: datetime
    line_item_usage_end_date: Optional[datetime] = None
    line_item_usage_account_id: str = Field(pattern=r"^[0-9]{12}$")
    line_item_usage_account_name: Optional[str] = None
    line_item_product_code: Optional[str] = None
    line_item_usage_type: str = Field(description="e.g. BoxUsage:p3.2xlarge")
    line_item_operation: Optional[str] = None
    line_item_resource_id: str = Field(description="ARN or instance ID")
    line_item_usage_amount: float = Field(ge=0.0)
    pricing_unit: str = Field(description="Hrs, GB, Requests")
    line_item_unblended_rate: Optional[float] = Field(default=None, ge=0.0)
    line_item_unblended_cost: float = Field(ge=0.0, description="Source of truth for detection")
    usage_density_24h: float = Field(
        ge=0.0, le=1.0,
        description="Continuous run density. 1.0 = running 24/24.",
    )
    resource_tags_user_environment: str = Field(
        description="prod | staging | dev | sandbox | ml-research | data-analytics",
    )
    resource_tags_user_team: Optional[str] = None
    resource_tags_user_owner: Optional[str] = None
    resource_tags_user_cost_center: Optional[str] = None


# ---------------------------------------------------------------------------
# Request — CloudWatch utilization metrics (telemetry-contract §8)
# v3.1.0: CDO sends cpu_utilization_hourly raw, AI Engine computes idle_hours
# ---------------------------------------------------------------------------

class UtilizationMetric(BaseModel):
    """CloudWatch metrics aggregated per resource per 24h.
    Matches telemetry-contract v3.1.0 §8.1 JSON Schema.
    v3.1.0 change: idle_hours_continuous removed — AI Engine computes from cpu_utilization_hourly.
    """
    resource_id: str = Field(description="Must match line_item_resource_id in CUR")
    cpu_percent: float = Field(ge=0.0, le=100.0, description="CPUUtilization avg 24h")
    cpu_utilization_hourly: List[float] = Field(
        min_length=24, max_length=24,
        description=(
            "Array of 24 elements — CPU% average per hour UTC "
            "(index 0 = 00:00, index 23 = 23:00). "
            "AI Engine computes idle_hours_continuous from this array."
        ),
    )
    memory_mib: Optional[float] = Field(default=None, ge=0.0)
    network_in_bytes: float = Field(ge=0.0, description="NetworkIn total 24h")
    network_out_bytes: float = Field(ge=0.0, description="NetworkOut total 24h")
    disk_io_ops: Optional[float] = Field(default=None, ge=0.0)
    database_connections: Optional[int] = Field(default=None, ge=0)
    gpu_utilization: Optional[float] = Field(default=None, ge=0.0, le=100.0)


# ---------------------------------------------------------------------------
# Detect Request — v1.3.0
# ---------------------------------------------------------------------------

class DetectRequest(BaseModel):
    """POST /v1/detect request body.
    Contract ref: ai-api-contract.md v1.3.0 §5.1.
    v1.2.0: aws_cost_explorer_daily demoted to conditional (CDO-P5).
    v1.3.0: callback_url added, s3_bucket_uri pattern updated.
    """
    data_source_type: str = Field(
        description="RAW_JSON or S3_POINTER",
        pattern=r"^(RAW_JSON|S3_POINTER)$",
    )
    is_ad_hoc: bool = Field(
        default=False,
        description="true = emergency scan, bypass idempotency",
    )
    telemetry_delay_event: bool = Field(
        default=False,
        description="true = CUR not finalized (delay > 36h), CE fallback mode",
    )
    callback_url: Optional[str] = Field(
        default=None,
        pattern=r"^https://",
        description=(
            "[v1.3.0] Optional. If provided, AI Engine POSTs a copy of DetectResponse "
            "to this URL after processing (fire-and-forget). CDO still receives sync response."
        ),
    )
    aws_cost_explorer_daily: Optional[List[CostExplorerItem]] = Field(
        default=None,
        description=(
            "CE API data — conditional: required ONLY when telemetry_delay_event=true. "
            "When CUR pipeline is ready, CDO does NOT need to pull CE API."
        ),
    )
    aws_cur_line_items: Optional[List[CURLineItem]] = Field(
        default=None,
        description="CUR resource-level data — required when RAW_JSON and telemetry_delay_event=false",
    )
    s3_bucket_uri: Optional[str] = Field(
        default=None,
        pattern=r"^s3://tf2-cdo[0-9]{2}-telemetry-[a-z0-9\-]+/.+\.json\.gz$",
        description=(
            "[v1.3.0] S3 URI for compressed CUR — required when S3_POINTER. "
            "Pattern enforces naming convention: tf2-cdo{NN}-telemetry-{region}"
        ),
    )
    resource_utilization_metrics: Optional[List[UtilizationMetric]] = Field(
        default=None,
        description="CloudWatch metrics — optional, improves confidence. v3.1.0: includes cpu_utilization_hourly",
    )


# ---------------------------------------------------------------------------
# Anomaly item in response (ai-api-contract §5.1 Response)
# ---------------------------------------------------------------------------

class AlertRouting(BaseModel):
    """Alert routing flags per anomaly."""
    finance: bool
    engineering: bool


class AnomalyResponseItem(BaseModel):
    """Single anomaly in DetectResponse.anomalies_list.
    Contract ref: ai-api-contract.md v1.3.0 §5.1 Response Schema.
    """
    anomaly_id: str = Field(description="Format: ANM-YYYY-MMDD[A-Z]", pattern=r"^ANM-[0-9]{4}-[0-9]{4}[A-Z]$")
    anomaly_type: str = Field(description="runaway_usage | idle_resource | untagged_spend | sudden_spike | gradual_drift")
    severity: str = Field(description="HIGH | MEDIUM | LOW")
    confidence_score: float = Field(ge=0.0, le=1.0)
    resource_id: str
    environment: str
    responsible_team: Optional[str] = None
    unblended_cost_24h_usd: float = Field(ge=0.0)
    cost_ratio_to_7d_avg: float = Field(ge=0.0)
    ai_model_used: str
    alert_routing: AlertRouting


# ---------------------------------------------------------------------------
# Detect Response — 200 OK (synchronous, v1.3.0)
# ---------------------------------------------------------------------------

class DetectResponse(BaseModel):
    """POST /v1/detect response body (200 OK — synchronous).
    Contract ref: ai-api-contract.md v1.3.0 §5.1 Response.
    v1.2.0 change: switched from 202 async to 200 sync with full anomalies_list.
    """
    success: bool
    correlation_id: str = Field(description="UUID v4 — trace ID for detect → decide → verify chain")
    anomalies_detected: bool = Field(description="true if cost anomalies were found")
    data_confidence: str = Field(
        description="HIGH (CUR complete) or LOW (CE fallback / telemetry_delay_event=true)",
        pattern=r"^(HIGH|LOW)$",
    )
    anomalies_list: List[AnomalyResponseItem] = Field(
        description="List of detected anomalies. Empty array if anomalies_detected=false",
    )
    error_message: Optional[str] = Field(
        default=None,
        description="Error detail when success=false",
    )


# ---------------------------------------------------------------------------
# Health check — v1.3.0 (ai-api-contract §5.4)
# ---------------------------------------------------------------------------

class HealthServices(BaseModel):
    """Dependency status for health check.
    Contract ref: ai-api-contract.md v1.3.0 §5.4.
    """
    s3_audit_bucket: str = "connected"
    bedrock_api: str = "accessible"
    s3_cur_bucket: str = "reachable"


class HealthResponse(BaseModel):
    """GET /health response. Contract ref: ai-api-contract.md v1.3.0 §5.4."""
    status: str = "healthy"
    timestamp: datetime
    services: HealthServices = Field(default_factory=HealthServices)
