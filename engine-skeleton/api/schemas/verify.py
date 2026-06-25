"""
API schemas for POST /v1/verify — Post-action verification.
=============================================================
Contract ref: ai-api-contract.md §5.3

CDO sends post-action telemetry + execution report.
AI Engine evaluates effectiveness → returns DONE / RETRY / ROLLBACK / ESCALATE.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from api.schemas.detect import CostExplorerItem, CURLineItem


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------

class ActionExecuted(BaseModel):
    """What CDO actually executed."""
    action: str = Field(description="tag-for-review | time-gated-countdown | auto-shutdown | quota-cap")
    target: str = Field(description="ARN of target resource")
    status: str = Field(description="COMPLETED or FAILED")
    execution_time_seconds: Optional[int] = Field(default=None, ge=0)


class PostTelemetryWindow(BaseModel):
    """Post-action telemetry data — same conditional logic as detect request.
    v1.2.0: CE daily only required when telemetry_delay_event=true (CDO-P5).
    """
    data_source_type: str = Field(pattern=r"^(RAW_JSON|S3_POINTER)$")
    telemetry_delay_event: bool = Field(
        default=False,
        description="true = CUR not finalized, CE fallback mode",
    )
    aws_cost_explorer_daily: Optional[List[CostExplorerItem]] = Field(
        default=None,
        description="CE data — only required when telemetry_delay_event=true",
    )
    aws_cur_line_items: Optional[List[CURLineItem]] = None
    s3_bucket_uri: Optional[str] = None


class VerifyRequest(BaseModel):
    """POST /v1/verify request body. Contract §5.3."""
    correlation_id: str = Field(description="Must match across detect → decide → verify")
    idempotency_key: str
    dry_run_mode: bool
    action_executed: ActionExecuted
    post_telemetry_window: PostTelemetryWindow


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------

class EscalationMetrics(BaseModel):
    """Snapshot metrics at time of escalation."""
    unblended_cost_24h_usd: float
    cost_ratio_to_7d_avg: float
    usage_density_24h: float


class EscalationBundle(BaseModel):
    """Context bundle for CDO when AI escalates.
    Required when next_action = ESCALATE.
    """
    reason: str = Field(description="Why self-healing failed")
    logs: Optional[List[str]] = None
    metrics: Optional[EscalationMetrics] = None


class VerifyResponse(BaseModel):
    """POST /v1/verify response body. Contract §5.3."""
    success: bool = Field(description="Cost returned to baseline?")
    regression_detected: bool = Field(description="Side-effect cost spike detected?")
    next_action: str = Field(description="DONE | RETRY | ROLLBACK | ESCALATE")
    escalation_bundle: Optional[EscalationBundle] = None
