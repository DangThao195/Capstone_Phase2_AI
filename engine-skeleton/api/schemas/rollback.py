"""
API schemas for POST /v1/audit/{audit_id}/rollback — Rollback notification.
============================================================================
Contract ref: ai-api-contract.md v1.3.0 §5.6

v1.2.0 change (CDO-P1): CDO now executes rollback independently via boto3.
This endpoint receives NOTIFICATION after CDO has already rolled back.
AI Engine records audit trail and updates error budget / feedback loop.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class RollbackRequest(BaseModel):
    """POST /v1/audit/{audit_id}/rollback request body.
    Contract ref: ai-api-contract.md v1.3.0 §5.6.
    CDO sends this AFTER executing rollback via boto3 locally.
    """
    reason: str = Field(description="Why rolling back (e.g. False Positive, engineer override)")
    rolled_back_by: str = Field(description="Email of engineer initiating rollback")
    rollback_executed_at: datetime = Field(
        description="Timestamp when CDO executed boto3 rollback (RFC3339 UTC)",
    )
    rollback_status: str = Field(
        description="SUCCESS or FAILED — result of CDO's boto3 execution",
        pattern=r"^(SUCCESS|FAILED)$",
    )
    boto3_result: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Raw boto3 response for AI Engine audit logging",
    )


class RollbackResponse(BaseModel):
    """POST /v1/audit/{audit_id}/rollback response body.
    Contract ref: ai-api-contract.md v1.3.0 §5.6.
    """
    audit_recorded: bool = Field(description="AI Engine has recorded the audit trail")
    false_positive_count_updated: bool = Field(description="FP count updated for feedback loop")
    new_error_budget_burned_pct: float = Field(
        ge=0.0,
        description="Error budget burned percentage after this rollback",
    )
    containment_locked: bool = Field(
        description="true if error budget exceeded threshold → tenant enters LOCKED_MODE",
    )
    message: str = Field(description="Human-readable status message")
