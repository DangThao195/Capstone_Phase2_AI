"""
Domain models (internal representation, not API schema).
These are the core data structures the engine reasons about.

Updated for Contract v1.1 — added JobRecord for async detection tracking.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from models.enums import (
    AnomalyType,
    AlertRoute,
    ContainmentStatus,
    DetectionStatus,
    Environment,
    SuggestedAction,
)


class CostRecord(BaseModel):
    """Single normalised cost data point from CDO pipeline."""
    tenant_id: str
    account_id: str
    service: str
    region: str
    cost_usd: float
    usage_type: str
    tags: Dict[str, str] = Field(default_factory=dict)
    environment: Environment = Environment.UNKNOWN
    owner: Optional[str] = None
    cost_period_start: datetime
    cost_period_end: datetime
    idempotency_key: Optional[str] = None


class AnomalyResult(BaseModel):
    """Internal detection result before mapping to API response."""
    is_anomaly: bool
    anomaly_type: AnomalyType = AnomalyType.OTHER
    severity: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(max_length=300)
    affected_account: Optional[str] = None
    affected_account_name: Optional[str] = None     # Human-readable account name
    affected_service: Optional[str] = None
    affected_resource_id: Optional[str] = None      # line_item_resource_id for drill-down
    baseline_cost_usd: Optional[float] = None
    current_cost_usd: Optional[float] = None
    cost_delta_usd: Optional[float] = None
    cost_delta_pct: Optional[float] = None


class ContainmentDecision(BaseModel):
    """Result of the containment evaluation."""
    action: SuggestedAction
    status: ContainmentStatus
    target_resource: Optional[str] = None
    target_environment: Environment = Environment.UNKNOWN
    dry_run_required: bool = True
    dry_run_passed: Optional[bool] = None
    rollback_path: Optional[str] = None


class AuditEntry(BaseModel):
    """Immutable audit trail record."""
    audit_id: UUID
    timestamp: datetime
    tenant_id: str
    correlation_id: Optional[str] = None
    detection_result: AnomalyResult
    containment_decision: Optional[ContainmentDecision] = None
    actor: str = "ai-engine"
    before_state: Optional[Dict] = None
    after_state: Optional[Dict] = None


# ---------------------------------------------------------------------------
# Async job tracking (Contract v1.1)
# ---------------------------------------------------------------------------

class JobRecord(BaseModel):
    """
    Tracks an async detection job from POST /v1/detect → GET /v1/status/{id}.

    Skeleton: stored in-memory dict.
    W12: swap to DynamoDB (pk=tenant_id, sk=correlation_id).
    """
    correlation_id: str
    tenant_id: str
    status: DetectionStatus = DetectionStatus.PROCESSING
    created_at: datetime
    completed_at: Optional[datetime] = None
    # Detection results (populated when COMPLETED)
    anomalies_detected: bool = False
    anomalies_list: List[Dict[str, Any]] = Field(default_factory=list)
    # Error info (populated when FAILED)
    error_message: Optional[str] = None
