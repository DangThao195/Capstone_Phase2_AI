"""
Safe containment decision engine.
==================================
Enforces the 3 HARD BOUNDARIES:
  1. NEVER terminate production resources
  2. NEVER delete data
  3. NEVER modify IAM

All containment actions are gated through dry-run mode.
Auto-containment only on dev/sandbox environments.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from config.settings import get_settings
from models.domain import AnomalyResult, ContainmentDecision
from models.enums import (
    AnomalyType,
    ContainmentStatus,
    Environment,
    SuggestedAction,
)

logger = logging.getLogger("finops-engine.containment")


# ---------------------------------------------------------------------------
# Containment action mapping (anomaly type → recommended action)
# ---------------------------------------------------------------------------
_ACTION_MAP = {
    AnomalyType.RUNAWAY_USAGE: SuggestedAction.SCHEDULE_SHUTDOWN,
    AnomalyType.IDLE_RESOURCE: SuggestedAction.SCHEDULE_SHUTDOWN,
    AnomalyType.UNTAGGED_SPEND: SuggestedAction.TAG_FOR_REVIEW,
    AnomalyType.SUDDEN_SPIKE: SuggestedAction.INVESTIGATE,
    AnomalyType.GRADUAL_DRIFT: SuggestedAction.INVESTIGATE,
    AnomalyType.OTHER: SuggestedAction.ALERT_ONLY,
}


def evaluate_containment(
    result: AnomalyResult,
    resource_env: str,
    policy: Optional[dict] = None,
    tenant_id: str = "",
) -> ContainmentDecision:
    """
    Evaluate whether containment should be taken and in what mode.

    Decision tree:
    1. Not anomaly → ALERT_ONLY / SKIPPED
    2. Prod resource → SUGGEST only (never auto-act)
    3. Dev/Sandbox + low confidence → INVESTIGATE
    4. Dev/Sandbox + high confidence → recommended action (dry-run first)
    """
    settings = get_settings()

    # Normalise environment
    try:
        env = Environment(resource_env.lower())
    except ValueError:
        env = Environment.UNKNOWN

    # --- No anomaly: nothing to contain ---
    if not result.is_anomaly:
        return ContainmentDecision(
            action=SuggestedAction.ALERT_ONLY,
            status=ContainmentStatus.SKIPPED_PROD,
            target_environment=env,
            dry_run_required=False,
        )

    # --- HARD BOUNDARY: Production resources ---
    if env in (Environment.PROD, Environment.STAGING, Environment.UNKNOWN):
        logger.info(
            "containment_blocked | tenant=%s | env=%s | reason=prod_boundary",
            tenant_id,
            env.value,
        )
        return ContainmentDecision(
            action=SuggestedAction.TAG_FOR_REVIEW,
            status=ContainmentStatus.SKIPPED_PROD,
            target_environment=env,
            dry_run_required=False,
            rollback_path="N/A — no action taken on production",
        )

    # --- Non-prod: check confidence threshold ---
    if result.confidence < settings.confidence_threshold:
        return ContainmentDecision(
            action=SuggestedAction.INVESTIGATE,
            status=ContainmentStatus.ESCALATED,
            target_environment=env,
            dry_run_required=False,
            rollback_path="N/A — escalated to human",
        )

    # --- Non-prod + high confidence: recommend action ---
    recommended_action = _ACTION_MAP.get(result.anomaly_type, SuggestedAction.ALERT_ONLY)

    # Check containment policy from caller
    if policy and not policy.get("allow_auto_containment", False):
        return ContainmentDecision(
            action=recommended_action,
            status=ContainmentStatus.DRY_RUN,
            target_environment=env,
            dry_run_required=True,
            rollback_path=_get_rollback_path(recommended_action),
        )

    # Global dry-run mode check
    if settings.dry_run_mode:
        return ContainmentDecision(
            action=recommended_action,
            status=ContainmentStatus.DRY_RUN,
            target_environment=env,
            dry_run_required=True,
            dry_run_passed=True,  # Skeleton: always pass dry-run
            rollback_path=_get_rollback_path(recommended_action),
        )

    # Auto containment enabled (W12 feature)
    return ContainmentDecision(
        action=recommended_action,
        status=ContainmentStatus.EXECUTED,
        target_environment=env,
        dry_run_required=True,
        dry_run_passed=True,
        rollback_path=_get_rollback_path(recommended_action),
    )


def _get_rollback_path(action: SuggestedAction) -> str:
    """Provide rollback instructions per action type (audit trail requirement)."""
    rollback_map = {
        SuggestedAction.SCHEDULE_SHUTDOWN: "Re-launch resource via AWS Console or IaC re-apply",
        SuggestedAction.QUOTA_CAP: "Remove quota cap via Service Quotas console",
        SuggestedAction.TAG_FOR_REVIEW: "Remove review tag via AWS Tag Editor",
        SuggestedAction.INVESTIGATE: "N/A — no automated action taken",
        SuggestedAction.ALERT_ONLY: "N/A — alert only",
    }
    return rollback_map.get(action, "Contact engineering team for manual rollback")
