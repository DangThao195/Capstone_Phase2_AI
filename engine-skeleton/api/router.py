"""
API Router — Contract v1.5.0 endpoint definitions.
====================================================
Endpoints (6 total):
  GET  /health                         → Health check (Contract §5.4)
  POST /v1/detect                      → Sync anomaly detection (Contract §5.1) — 200 OK
  GET  /v1/status/{id}                 → Remediation status (Contract §5.5)
  POST /v1/decide                      → RCA + action plan (Contract §5.2)
  POST /v1/verify                      → Post-action verification (Contract §5.3)
  POST /v1/audit/{audit_id}/rollback   → Rollback audit notification (Contract §5.6)

v1.5.0 changes:
  - /v1/detect: 200 sync (was 202 async) — returns full anomalies_list directly
  - CUR data is primary source of truth; CE only when telemetry_delay_event=true
  - business_context is now mandatory (includes traffic_volume for cost normalization)
  - s3_bucket_uri enforces globally unique naming convention
  - rollback_payload includes boto3_equivalent
  - Rollback endpoint: receives notification after CDO executes (audit_recorded)
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Dict, Union, Optional
from uuid import uuid4

from fastapi import APIRouter, Request, HTTPException, Header
from fastapi.responses import JSONResponse

from api.schemas.detect import (
    AlertRouting,
    AnomalyResponseItem,
    DetectRequest,
    DetectResponse,
    HealthResponse,
    HealthServices,
)
from api.schemas.decide import (
    ActionPlanStep,
    AppliedPayload,
    Boto3Equivalent,
    DecideRequest,
    DecideResponse,
    EngineeringDashboardData,
    FinanceAllocation,
    FinanceDashboardData,
    FinanceMetrics,
    RollbackPayload,
    RootCauseAnalysis,
    TechnicalContext,
)
from api.schemas.verify import (
    EscalationBundle,
    EscalationMetrics,
    VerifyRequest,
    VerifyResponse,
)
from api.schemas.status import (
    ActionLogEntry,
    AnomalyItem,
    DetectionStatusResponse,
    RemediationStatusResponse,
    AlertRouting as StatusAlertRouting,
)
from api.schemas.rollback import RollbackRequest, RollbackResponse
from config.settings import get_settings
from engine.alert_router import determine_alert_route
from engine.audit import audit_logger
from engine.containment import evaluate_containment
from engine.strategies.base import DetectionStrategy
from engine.strategies.dummy import DummyStrategy
from engine.strategies.statistical import StatisticalStrategy
from models.domain import AnomalyResult, CostRecord, JobRecord
from models.enums import (
    AnomalyType,
    ContainmentAction,
    DetectionStatus,
    Environment,
    Severity,
)

logger = logging.getLogger("finops-engine.router")

api_router = APIRouter()

# ---------------------------------------------------------------------------
# In-memory stores (skeleton phase — W12: swap to DynamoDB)
# ---------------------------------------------------------------------------
_jobs: Dict[str, JobRecord] = {}           # correlation_id → JobRecord
_decide_cache: Dict[str, dict] = {}        # correlation_id → decide response data
_error_budget: Dict[str, float] = {}       # tenant_id → burned % (0-100)


# ---------------------------------------------------------------------------
# Strategy selection (feature-flag driven)
# ---------------------------------------------------------------------------
def _get_strategy() -> DetectionStrategy:
    """
    Select detection strategy based on configuration.
    Skeleton phase: DummyStrategy.
    W12: switch to StatisticalStrategy via env var FINOPS_ENABLE_LLM_ANALYSIS.
    """
    settings = get_settings()
    if settings.enable_llm_analysis:
        return StatisticalStrategy()
    return DummyStrategy()


# ---------------------------------------------------------------------------
# Anomaly type → severity mapping
# ---------------------------------------------------------------------------
_SEVERITY_MAP = {
    AnomalyType.RUNAWAY_USAGE: Severity.HIGH,
    AnomalyType.SUDDEN_SPIKE: Severity.HIGH,
    AnomalyType.IDLE_RESOURCE: Severity.MEDIUM,
    AnomalyType.UNTAGGED_SPEND: Severity.MEDIUM,
    AnomalyType.GRADUAL_DRIFT: Severity.LOW,
    AnomalyType.OTHER: Severity.LOW,
}

# Anomaly type → containment action mapping
_ACTION_MAP = {
    AnomalyType.RUNAWAY_USAGE: ContainmentAction.TIME_GATED_COUNTDOWN,
    AnomalyType.IDLE_RESOURCE: ContainmentAction.AUTO_SHUTDOWN,
    AnomalyType.UNTAGGED_SPEND: ContainmentAction.TAG_FOR_REVIEW,
    AnomalyType.SUDDEN_SPIKE: ContainmentAction.TAG_FOR_REVIEW,
    AnomalyType.GRADUAL_DRIFT: ContainmentAction.TAG_FOR_REVIEW,
    AnomalyType.OTHER: ContainmentAction.TAG_FOR_REVIEW,
}

# Anomaly type → runbook mapping
_RUNBOOK_MAP = {
    AnomalyType.RUNAWAY_USAGE: "RunawayComputeContainmentRunbook",
    AnomalyType.IDLE_RESOURCE: "IdleResourceCleanupRunbook",
    AnomalyType.UNTAGGED_SPEND: "UntaggedSpendTaggingRunbook",
    AnomalyType.SUDDEN_SPIKE: "SuddenSpikeInvestigationRunbook",
    AnomalyType.GRADUAL_DRIFT: "GradualDriftReviewRunbook",
    AnomalyType.OTHER: "GeneralAnomalyRunbook",
}


def _is_uuid(value: str) -> bool:
    """Check if a string looks like a UUID v4."""
    return bool(re.match(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        value,
        re.IGNORECASE,
    ))


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------
@api_router.get(
    "/health",
    response_model=HealthResponse,
    tags=["operations"],
    summary="Health check endpoint",
    description="ALB/App Runner health probe. No authentication required. Contract §5.4.",
)
async def health_check():
    return HealthResponse(
        status="healthy",
        timestamp=datetime.now(timezone.utc),
        services=HealthServices(
            s3_audit_bucket="connected",    # Skeleton: always connected
            bedrock_api="accessible",       # Skeleton: always accessible
            s3_cur_bucket="reachable",      # Skeleton: always reachable
        ),
    )


# ---------------------------------------------------------------------------
# POST /v1/detect — Synchronous anomaly detection (200 OK)
# ---------------------------------------------------------------------------
@api_router.post(
    "/v1/detect",
    response_model=DetectResponse,
    status_code=200,
    tags=["detection"],
    summary="Detect cost anomalies (synchronous)",
    description=(
        "Accept telemetry data and run detection synchronously. "
        "Returns 200 OK with full anomalies_list and data_confidence. "
        "CUR is primary data source; CE only when telemetry_delay_event=true. "
        "Contract §5.1."
    ),
    responses={
        400: {"description": "Invalid request schema — do NOT retry"},
        429: {"description": "Rate limited — use exponential backoff"},
        503: {"description": "Engine unavailable — CDO must fallback to rule-based alert"},
    },
)
async def detect_anomaly(
    request: Request,
    body: DetectRequest,
    x_tenant_id: str = Header(..., alias="X-Tenant-Id", description="Tenant identifier for multi-tenancy"),
    x_correlation_id: Optional[str] = Header(None, alias="X-Correlation-Id", description="Optional correlation ID for tracing"),
):
    """
    Accepts telemetry → runs detection → returns full result synchronously.
    v1.3.0: 200 sync (was 202 async). CUR-primary detection.
    """
    tenant_id = getattr(request.state, "tenant_id", "unknown")
    correlation_id = getattr(request.state, "correlation_id", str(uuid4()))

    # Determine data confidence based on data source
    data_confidence = "LOW" if body.telemetry_delay_event else "HIGH"

    # --- Run detection ---
    strategy = _get_strategy()
    try:
        # Convert contract schema → internal CostRecord for strategy
        # v1.3.0: CUR is primary data source; CE is fallback only
        cost_window = []

        if body.aws_cur_line_items and not body.telemetry_delay_event:
            # Primary path: CUR data (source of truth)
            for cur in body.aws_cur_line_items:
                cost_window.append(CostRecord(
                    tenant_id=tenant_id,
                    account_id=cur.line_item_usage_account_id,
                    service=cur.line_item_product_code or cur.line_item_usage_type,
                    region="ap-southeast-1",  # CUR doesn't have region directly
                    cost_usd=cur.line_item_unblended_cost,
                    usage_type=cur.line_item_usage_type,
                    environment=_resolve_environment(cur.resource_tags_user_environment),
                    cost_period_start=cur.line_item_usage_start_date,
                    cost_period_end=cur.line_item_usage_end_date or cur.line_item_usage_start_date,
                ))
        elif body.aws_cost_explorer_daily:
            # Fallback path: CE data (when CUR delayed)
            for ce in body.aws_cost_explorer_daily:
                cost_window.append(CostRecord(
                    tenant_id=tenant_id,
                    account_id=ce.linked_account_id,
                    service=ce.service_code,
                    region=ce.region,
                    cost_usd=ce.unblended_cost,
                    usage_type=ce.service_code,
                    environment=_resolve_environment("unknown"),
                    cost_period_start=datetime.combine(ce.date, datetime.min.time(), tzinfo=timezone.utc),
                    cost_period_end=datetime.combine(ce.date, datetime.min.time(), tzinfo=timezone.utc),
                ))

        result = strategy.detect(
            cost_window=cost_window,
            baseline=None,
            tenant_id=tenant_id,
        )

        # Build anomalies_list from result
        anomalies_list: list[AnomalyResponseItem] = []
        if result.is_anomaly:
            anomaly_id = _generate_anomaly_id()
            severity = _SEVERITY_MAP.get(result.anomaly_type, Severity.LOW)
            alert_route = determine_alert_route(result)

            # Default values
            resource_id = result.affected_resource_id or "unknown"
            environment = "unknown"
            responsible_team = None
            unblended_cost = result.current_cost_usd or 0.0
            cost_ratio = result.cost_delta_pct or 0.0

            # Enrich from CUR data if available
            if body.aws_cur_line_items:
                first_cur = body.aws_cur_line_items[0]
                resource_id = first_cur.line_item_resource_id
                environment = first_cur.resource_tags_user_environment
                responsible_team = first_cur.resource_tags_user_team
                unblended_cost = first_cur.line_item_unblended_cost

            anomalies_list.append(AnomalyResponseItem(
                anomaly_id=anomaly_id,
                anomaly_type=result.anomaly_type.value,
                severity=severity.value,
                confidence_score=result.confidence,
                resource_id=resource_id,
                environment=environment,
                responsible_team=responsible_team,
                unblended_cost_24h_usd=unblended_cost,
                cost_ratio_to_7d_avg=cost_ratio,
                ai_model_used=f"skeleton-{strategy.strategy_name}",
                alert_routing=AlertRouting(
                    finance=alert_route.value in ("finance", "both"),
                    engineering=alert_route.value in ("engineering", "both"),
                ),
            ))

        # Store job for /v1/status polling (still useful for remediation tracking)
        job = JobRecord(
            correlation_id=correlation_id,
            tenant_id=tenant_id,
            status=DetectionStatus.COMPLETED,
            created_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            anomalies_detected=result.is_anomaly,
            anomalies_list=[a.model_dump() for a in anomalies_list],
        )
        _jobs[correlation_id] = job

        # Write audit trail
        if result.is_anomaly:
            audit_logger.create_audit_entry(
                tenant_id=tenant_id,
                correlation_id=correlation_id,
                detection_result=result,
            )

        return DetectResponse(
            success=True,
            correlation_id=correlation_id,
            anomalies_detected=result.is_anomaly,
            data_confidence=data_confidence,
            anomalies_list=anomalies_list,
        )

    except Exception as exc:
        logger.error("detection_failed | tenant=%s | error=%s", tenant_id, str(exc))

        # Store failed job for status tracking
        job = JobRecord(
            correlation_id=correlation_id,
            tenant_id=tenant_id,
            status=DetectionStatus.FAILED,
            created_at=datetime.now(timezone.utc),
            error_message=str(exc),
        )
        _jobs[correlation_id] = job

        return DetectResponse(
            success=False,
            correlation_id=correlation_id,
            anomalies_detected=False,
            data_confidence=data_confidence,
            anomalies_list=[],
            error_message=str(exc),
        )


# ---------------------------------------------------------------------------
# GET /v1/status/{id} — Poll status (dual-purpose)
# ---------------------------------------------------------------------------
@api_router.get(
    "/v1/status/{id}",
    tags=["detection"],
    summary="Poll detection/remediation status",
    description="Case A: UUID → detection results. Case B: ANM-ID → remediation status. Contract §5.5.",
    response_model=None,
)
async def get_status(
    id: str,
    x_tenant_id: str = Header(..., alias="X-Tenant-Id", description="Tenant identifier for multi-tenancy"),
    x_correlation_id: Optional[str] = Header(None, alias="X-Correlation-Id", description="Optional correlation ID for tracing"),
) -> Union[DetectionStatusResponse, RemediationStatusResponse]:
    """Dual-purpose status endpoint."""
    if _is_uuid(id):
        # Case A: Detection status
        job = _jobs.get(id)
        if not job:
            raise HTTPException(status_code=404, detail="Correlation ID not found")

        anomaly_items = None
        anomalies_detected = None
        if job.status == DetectionStatus.COMPLETED:
            anomalies_detected = job.anomalies_detected
            anomaly_items = [
                AnomalyItem(
                    anomaly_id=a["anomaly_id"],
                    anomaly_type=a["anomaly_type"],
                    severity=a["severity"],
                    confidence_score=a["confidence_score"],
                    resource_id=a["resource_id"],
                    environment=a["environment"],
                    responsible_team=a.get("responsible_team"),
                    unblended_cost_24h_usd=a["unblended_cost_24h_usd"],
                    cost_ratio_to_7d_avg=a["cost_ratio_to_7d_avg"],
                    ai_model_used=a.get("ai_model_used", "skeleton-rules-v1"),
                    alert_routing=StatusAlertRouting(**a["alert_routing"]),
                )
                for a in job.anomalies_list
            ]

        return DetectionStatusResponse(
            status=job.status.value,
            anomalies_detected=anomalies_detected,
            anomalies_list=anomaly_items,
            correlation_id=id,
            error_message=job.error_message,
        )
    else:
        # Case B: Remediation status (skeleton: return stub)
        return RemediationStatusResponse(
            audit_id=id,
            status="PENDING_APPROVAL",
            containment_locked=False,
            error_budget_remaining_pct=100.0 - _error_budget.get("default", 0.0),
            actions_log=[],
        )


# ---------------------------------------------------------------------------
# POST /v1/decide — RCA + action plan
# ---------------------------------------------------------------------------
@api_router.post(
    "/v1/decide",
    response_model=DecideResponse,
    tags=["remediation"],
    summary="Generate containment action plan",
    description="RCA + runbook match + AWS CLI payloads + boto3_equivalent. Contract §5.2.",
)
async def decide_action(
    request: Request,
    body: DecideRequest,
    x_tenant_id: str = Header(..., alias="X-Tenant-Id", description="Tenant identifier for multi-tenancy"),
    x_correlation_id: Optional[str] = Header(None, alias="X-Correlation-Id", description="Optional correlation ID for tracing"),
):
    """
    Generate RCA and containment plan based on anomaly context.
    v1.3.0: includes boto3_equivalent in rollback_payload for CDO offline rollback.
    Skeleton: deterministic mapping from anomaly_type → action.
    W12: LLM-enhanced RCA via Bedrock.
    """
    tenant_id = getattr(request.state, "tenant_id", "unknown")
    ctx = body.anomaly_context

    # Resolve anomaly type
    try:
        anomaly_type = AnomalyType(ctx.anomaly_type)
    except ValueError:
        anomaly_type = AnomalyType.OTHER

    # Match runbook
    runbook = _RUNBOOK_MAP.get(anomaly_type, "GeneralAnomalyRunbook")

    # Determine containment action
    containment_action = _ACTION_MAP.get(anomaly_type, ContainmentAction.TAG_FOR_REVIEW)

    # Build action plan
    action_plan = [
        ActionPlanStep(
            step=1,
            action=ContainmentAction.TAG_FOR_REVIEW.value,
            target=ctx.resource_id,
            params={},
        ),
    ]

    # Add second step if needed
    if containment_action != ContainmentAction.TAG_FOR_REVIEW:
        params = {}
        if containment_action == ContainmentAction.TIME_GATED_COUNTDOWN:
            params = {"time_lock_seconds": 14400, "fallback_action": "auto-shutdown"}
        action_plan.append(ActionPlanStep(
            step=2,
            action=containment_action.value,
            target=ctx.resource_id,
            params=params,
        ))

    # Build AWS CLI payloads (skeleton: tag commands)
    region = "ap-southeast-1"  # Default from contract
    applied_payload = AppliedPayload(
        action_type="inject_aws_tag",
        aws_cli_command=(
            f"aws ec2 create-tags --resources {ctx.resource_id} "
            f"--tags Key=finops:review,Value=pending Key=finops:anomaly-id,Value={ctx.anomaly_id} "
            f"--region {region}"
        ),
    )

    # v1.3.0: rollback_payload now includes boto3_equivalent for CDO offline rollback
    rollback_payload = RollbackPayload(
        action_type="remove_aws_tag",
        aws_cli_rollback_command=(
            f"aws ec2 delete-tags --resources {ctx.resource_id} "
            f"--tags Key=finops:review Key=finops:anomaly-id "
            f"--region {region}"
        ),
        original_resource_id=ctx.resource_id,
        boto3_equivalent=Boto3Equivalent(
            service="ec2",
            method="delete_tags",
            parameters={
                "Resources": [ctx.resource_id],
                "Tags": [{"Key": "finops:review"}, {"Key": "finops:anomaly-id"}],
            },
        ),
    )

    # Build dashboard data
    projected_monthly = ctx.unblended_cost_24h_usd * 30
    finance_data = FinanceDashboardData(
        target_recipient="Finance Team & CFO Dashboard",
        metrics=FinanceMetrics(
            unblended_cost_24h_usd=ctx.unblended_cost_24h_usd,
            cost_ratio_to_7d_avg=ctx.cost_ratio_to_7d_avg,
            projected_monthly_waste_usd=projected_monthly,
        ),
        allocation=FinanceAllocation(
            responsible_team=ctx.responsible_team or "unassigned",
            cost_center_code=ctx.cost_center_code or "N/A",
        ),
        executive_summary=(
            f"Resource {ctx.resource_id} ({ctx.environment}) "
            f"flagged as {ctx.anomaly_type} — ${ctx.unblended_cost_24h_usd:.2f}/day, "
            f"{ctx.cost_ratio_to_7d_avg:.1f}x baseline. "
            f"Projected monthly waste: ${projected_monthly:.2f}. "
            f"Action: {containment_action.value} ({'dry-run' if body.dry_run_mode else 'live'})."
        ),
    )

    engineering_data = EngineeringDashboardData(
        target_recipient="Engineering Console & Slack #finops-alert-engineering",
        technical_context=TechnicalContext(
            aws_service=ctx.anomaly_type,  # Skeleton: use anomaly_type as placeholder
            usage_type="unknown",
            pricing_unit="Hrs",
            usage_amount_24h=0.0,
            usage_density_24h=0.0,
        ),
        root_cause_analysis=RootCauseAnalysis(
            primary_driver_feature=f"{ctx.anomaly_type}_detection",
            technical_reason=(
                f"Skeleton RCA: {ctx.anomaly_type} detected on {ctx.resource_id} "
                f"in {ctx.environment}. Cost ratio {ctx.cost_ratio_to_7d_avg:.1f}x baseline."
            ),
            missing_mandatory_tags=[],
        ),
    )

    response = DecideResponse(
        matched_runbook=runbook,
        action_plan=action_plan,
        applied_payload=applied_payload,
        rollback_payload=rollback_payload,
        finance_dashboard_data=finance_data,
        engineering_dashboard_data=engineering_data,
        correlation_id=body.correlation_id,
        dry_run_mode=body.dry_run_mode,
    )

    # Cache for verify step
    _decide_cache[body.correlation_id] = {
        "anomaly_context": ctx.model_dump(),
        "action_plan": [s.model_dump() for s in action_plan],
        "containment_action": containment_action.value,
    }

    return response


# ---------------------------------------------------------------------------
# POST /v1/verify — Post-action verification
# ---------------------------------------------------------------------------
@api_router.post(
    "/v1/verify",
    response_model=VerifyResponse,
    tags=["remediation"],
    summary="Verify containment effectiveness",
    description="Evaluate post-action telemetry. Returns DONE/RETRY/ROLLBACK/ESCALATE. Contract §5.3.",
)
async def verify_action(
    request: Request,
    body: VerifyRequest,
    x_tenant_id: str = Header(..., alias="X-Tenant-Id", description="Tenant identifier for multi-tenancy"),
    x_correlation_id: Optional[str] = Header(None, alias="X-Correlation-Id", description="Optional correlation ID for tracing"),
):
    """
    Evaluate whether the containment action was effective.
    v1.3.0: post_telemetry_window.aws_cost_explorer_daily now optional.
    Skeleton: simple heuristic based on action_executed.status.
    W12: compare pre/post telemetry via Bedrock.
    """
    # Simple skeleton logic
    if body.action_executed.status == "COMPLETED":
        # Check if post-telemetry shows improvement
        # v1.3.0: CE data is optional, check CUR first
        post_costs = body.post_telemetry_window.aws_cost_explorer_daily
        post_cur = body.post_telemetry_window.aws_cur_line_items

        avg_post_cost = 0.0
        if post_cur:
            avg_post_cost = sum(c.line_item_unblended_cost for c in post_cur) / len(post_cur)
        elif post_costs:
            avg_post_cost = sum(c.unblended_cost for c in post_costs) / len(post_costs)

        # If cost is still high (> $100/day), suggest escalation
        if avg_post_cost > 100.0:
            return VerifyResponse(
                success=False,
                regression_detected=False,
                next_action="ESCALATE",
                escalation_bundle=EscalationBundle(
                    reason=(
                        f"Post-action cost still elevated at ${avg_post_cost:.2f}/day. "
                        f"Action '{body.action_executed.action}' on '{body.action_executed.target}' "
                        f"completed but cost has not returned to baseline."
                    ),
                    metrics=EscalationMetrics(
                        unblended_cost_24h_usd=avg_post_cost,
                        cost_ratio_to_7d_avg=0.0,
                        usage_density_24h=0.0,
                    ),
                ),
            )

        # Success case
        return VerifyResponse(
            success=True,
            regression_detected=False,
            next_action="DONE",
        )
    else:
        # Action failed → rollback
        return VerifyResponse(
            success=False,
            regression_detected=False,
            next_action="ROLLBACK",
        )


# ---------------------------------------------------------------------------
# POST /v1/audit/{audit_id}/rollback — Rollback audit notification
# ---------------------------------------------------------------------------
@api_router.post(
    "/v1/audit/{audit_id}/rollback",
    response_model=RollbackResponse,
    tags=["remediation"],
    summary="Record rollback audit trail",
    description=(
        "CDO sends notification AFTER executing rollback via boto3 locally. "
        "AI Engine records audit trail and updates error budget. Contract §5.6."
    ),
)
async def rollback_action(
    request: Request,
    audit_id: str,
    body: RollbackRequest,
    x_tenant_id: str = Header(..., alias="X-Tenant-Id", description="Tenant identifier for multi-tenancy"),
    x_correlation_id: Optional[str] = Header(None, alias="X-Correlation-Id", description="Optional correlation ID for tracing"),
):
    """
    Process rollback notification from CDO.
    v1.3.0 (CDO-P1): CDO executes rollback independently via boto3.
    This endpoint only records audit trail and updates feedback loop.
    Skeleton: in-memory error budget counter.
    W12: DynamoDB-backed error budget per tenant with 30-day sliding window.
    """
    settings = get_settings()
    tenant_id = getattr(request.state, "tenant_id", "unknown")

    # Update error budget (each rollback burns ~0.5% of budget)
    current_burned = _error_budget.get(tenant_id, 0.0)
    new_burned = current_burned + 0.5
    _error_budget[tenant_id] = new_burned

    # v1.3.0 (CDO-P3): Error budget lock per environment
    # Skeleton: use global threshold; W12: resolve from tenant environment
    locked = new_burned >= settings.error_budget_lock_threshold_pct

    logger.info(
        "rollback_audit_recorded | tenant=%s | audit_id=%s | by=%s | reason=%s | "
        "status=%s | burned=%.1f%% | locked=%s",
        tenant_id, audit_id, body.rolled_back_by, body.reason,
        body.rollback_status, new_burned, locked,
    )

    message = (
        f"Audit trail đã ghi cho {audit_id}. "
        f"FP count cập nhật cho feedback loop. "
        f"Error budget còn {100.0 - new_burned:.1f}%."
    )
    if locked:
        message += (
            f" Error budget vượt {settings.error_budget_lock_threshold_pct}%. "
            f"Tenant chuyển sang LOCKED_MODE — mọi containment action sẽ là dry-run."
        )

    return RollbackResponse(
        audit_recorded=True,
        false_positive_count_updated=True,
        new_error_budget_burned_pct=new_burned,
        containment_locked=locked,
        message=message,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_anomaly_id() -> str:
    """Generate anomaly ID in format ANM-YYYY-MMDDX."""
    now = datetime.now(timezone.utc)
    # Simple counter based on current second to avoid collisions
    suffix = chr(65 + (now.second % 26))  # A-Z
    return f"ANM-{now.year}-{now.month:02d}{now.day:02d}{suffix}"


def _resolve_environment(env_str: str) -> Environment:
    """Safely resolve environment string to enum."""
    try:
        return Environment(env_str.lower())
    except ValueError:
        return Environment.UNKNOWN
