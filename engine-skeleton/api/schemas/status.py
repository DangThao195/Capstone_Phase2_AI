"""
API schemas for GET /v1/status/{id} — Polling endpoint.
=========================================================
Contract ref: ai-api-contract.md §5.5

Dual-purpose endpoint:
  Case A: {id} is correlation_id (UUID) → detection status + anomalies_list
  Case B: {id} is anomaly_id (ANM-YYYY-MMDD[A-Z]) → remediation lifecycle status
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Case A — Detection status polling
# ---------------------------------------------------------------------------

class AlertRouting(BaseModel):
    """Alert routing flags per anomaly."""
    finance: bool
    engineering: bool


class AnomalyItem(BaseModel):
    """Single anomaly in the detection result list."""
    anomaly_id: str = Field(description="Format: ANM-YYYY-MMDD[A-Z]")
    anomaly_type: str = Field(description="runaway_usage | idle_resource | ...")
    severity: str = Field(description="HIGH | MEDIUM | LOW")
    confidence_score: float = Field(ge=0.0, le=1.0)
    resource_id: str
    environment: str
    responsible_team: Optional[str] = None
    unblended_cost_24h_usd: float = Field(ge=0.0)
    cost_ratio_to_7d_avg: float = Field(ge=0.0)
    ai_model_used: str = "skeleton-rules-v1"
    alert_routing: AlertRouting


class DetectionStatusResponse(BaseModel):
    """GET /v1/status/{correlation_id} response. Contract §5.5 Case A."""
    status: str = Field(description="PROCESSING | COMPLETED | FAILED")
    anomalies_detected: Optional[bool] = None
    anomalies_list: Optional[List[AnomalyItem]] = None
    correlation_id: str
    error_message: Optional[str] = None


# ---------------------------------------------------------------------------
# Case B — Remediation status polling
# ---------------------------------------------------------------------------

class ActionLogEntry(BaseModel):
    """Single entry in the remediation actions log."""
    timestamp: datetime
    action: str = Field(description="tag-for-review | auto-shutdown | ...")
    status: str = Field(description="COMPLETED | DRY_RUN_COMPLETED | FAILED")
    actor: str


class RemediationStatusResponse(BaseModel):
    """GET /v1/status/{anomaly_id} response. Contract §5.5 Case B."""
    audit_id: str = Field(description="ANM-YYYY-MMDD[A-Z]")
    status: str = Field(description="PENDING_APPROVAL | IN_PROGRESS | SUCCESS | ROLLED_BACK | ESCALATED")
    containment_locked: bool = Field(description="true if tenant in LOCKED_MODE (dry-run only)")
    error_budget_remaining_pct: float = Field(ge=0.0, le=100.0)
    actions_log: List[ActionLogEntry]
