"""
API schemas for POST /v1/decide — Containment planning.
=========================================================
Contract ref: ai-api-contract.md §5.2

CDO sends anomaly context after detection completes.
AI Engine returns RCA + action plan + AWS CLI payloads + dashboard data.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------

class AnomalyContext(BaseModel):
    """Context about the detected anomaly. CDO extracts from /v1/status response."""
    anomaly_id: str = Field(description="Format: ANM-YYYY-MMDD[A-Z]")
    anomaly_type: str = Field(description="runaway_usage | idle_resource | ...")
    resource_id: str = Field(description="ARN or instance ID")
    environment: str = Field(description="prod | staging | dev | sandbox | ml-research | data-analytics")
    unblended_cost_24h_usd: float = Field(ge=0.0)
    cost_ratio_to_7d_avg: float = Field(ge=0.0)
    responsible_team: Optional[str] = None
    cost_center_code: Optional[str] = None


class DecideRequest(BaseModel):
    """POST /v1/decide request body. Contract §5.2."""
    correlation_id: str = Field(description="Must match correlation_id from /v1/detect")
    idempotency_key: str = Field(description="UUID v4 — dedup key")
    dry_run_mode: bool = Field(description="true = log only, no real action")
    anomaly_context: AnomalyContext


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------

class ActionPlanStep(BaseModel):
    """Single step in the containment action plan."""
    step: int = Field(ge=1)
    action: str = Field(description="tag-for-review | time-gated-countdown | auto-shutdown | quota-cap")
    target: str = Field(description="ARN of target resource")
    params: Optional[Dict[str, Any]] = Field(default_factory=dict)


class AppliedPayload(BaseModel):
    """AWS CLI command for containment execution."""
    action_type: str = Field(description="inject_aws_tag | stop_instance | stop_sagemaker_notebook | restrict_quota")
    aws_cli_command: str = Field(description="Full AWS CLI command for CDO to execute")


class Boto3Equivalent(BaseModel):
    """Boto3 call parameters for CDO offline rollback.
    CDO caches this in DynamoDB immediately after receiving DecideResponse.
    When AI Engine is unavailable, CDO calls boto3 directly using these params.
    Contract ref: ai-api-contract.md v1.3.0 §5.2 rollback_payload.boto3_equivalent.
    """
    service: str = Field(description="AWS service name: ec2, rds, sagemaker")
    method: str = Field(description="Boto3 method: start_instances, delete_tags, etc.")
    parameters: Dict[str, Any] = Field(description="Method kwargs for boto3 call")


class RollbackPayload(BaseModel):
    """AWS CLI command + boto3 equivalent for rollback.
    CDO MUST cache boto3_equivalent into DynamoDB local immediately upon receipt.
    Contract ref: ai-api-contract.md v1.3.0 §5.2.
    """
    action_type: str = Field(description="remove_aws_tag | start_instance | start_sagemaker_notebook | restore_quota")
    aws_cli_rollback_command: str
    original_resource_id: str
    boto3_equivalent: Boto3Equivalent = Field(
        description="CDO uses this for offline rollback via boto3 when AI Engine is unreachable",
    )


class FinanceMetrics(BaseModel):
    """Financial impact metrics for CFO dashboard."""
    unblended_cost_24h_usd: float
    cost_ratio_to_7d_avg: float
    projected_monthly_waste_usd: float


class FinanceAllocation(BaseModel):
    """Cost allocation info."""
    responsible_team: str
    cost_center_code: str


class FinanceDashboardData(BaseModel):
    """Data for Finance Team & CFO Dashboard."""
    target_recipient: str = "Finance Team & CFO Dashboard"
    metrics: FinanceMetrics
    allocation: FinanceAllocation
    executive_summary: str


class TechnicalContext(BaseModel):
    """Technical details for engineering console."""
    aws_service: str
    usage_type: str
    pricing_unit: str
    usage_amount_24h: float
    usage_density_24h: float


class RootCauseAnalysis(BaseModel):
    """RCA output from AI Engine."""
    primary_driver_feature: str
    technical_reason: str
    missing_mandatory_tags: List[str] = Field(default_factory=list)


class EngineeringDashboardData(BaseModel):
    """Data for Engineering Console & Slack alert."""
    target_recipient: str = "Engineering Console & Slack #finops-alert-engineering"
    technical_context: TechnicalContext
    root_cause_analysis: RootCauseAnalysis


class DecideResponse(BaseModel):
    """POST /v1/decide response body. Contract §5.2."""
    matched_runbook: str = Field(description="Name of matched runbook from library")
    action_plan: List[ActionPlanStep]
    applied_payload: AppliedPayload
    rollback_payload: RollbackPayload
    finance_dashboard_data: FinanceDashboardData
    engineering_dashboard_data: EngineeringDashboardData
    correlation_id: str
    dry_run_mode: bool
