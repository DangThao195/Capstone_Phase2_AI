"""
Dummy detection strategy — returns hardcoded anomaly results.
=============================================================
This is the SKELETON implementation deployed on W11 T5/T6.
CDO teams use this to validate their integration code path
without waiting for real AI logic.

Response schema is IDENTICAL to what the real engine will produce.
URL does not change. Schema does not change. CDO does not redo.

Updated for Contract v1.1: now accepts CostRecord (internal domain model).
"""

from __future__ import annotations

from typing import List, Optional, Any

from engine.strategies.base import DetectionStrategy
from models.domain import AnomalyResult, CostRecord
from models.enums import AnomalyType


class DummyStrategy(DetectionStrategy):
    """
    Always returns a deterministic anomaly result.
    Useful for:
      - CDO integration testing (schema validation)
      - E2E pipeline smoke tests
      - Demo rehearsals before real model is ready
    """

    @property
    def strategy_name(self) -> str:
        return "dummy_skeleton"

    def detect(
        self,
        cost_window: List[CostRecord],
        baseline: Optional[object],
        tenant_id: str,
        utilization_metrics: Optional[List[Any]] = None,
        business_context: Optional[Any] = None,
    ) -> AnomalyResult:
        # Deterministic: if any item has cost > 200, flag as anomaly
        total_cost = sum(item.cost_usd for item in cost_window)
        has_high_cost = any(item.cost_usd > 200 for item in cost_window)

        # Extract representative item info for response enrichment
        top_item = max(cost_window, key=lambda x: x.cost_usd) if cost_window else None

        if has_high_cost:
            return AnomalyResult(
                is_anomaly=True,
                anomaly_type=AnomalyType.RUNAWAY_USAGE,
                severity=0.85,
                confidence=0.78,
                reasoning=(
                    f"[SKELETON] Detected high cost item in cost window. "
                    f"Total window cost: ${total_cost:.2f}. "
                    f"Potential runaway compute workload in dev/sandbox account."
                ),
                affected_account=top_item.account_id if top_item else None,
                affected_service=top_item.service if top_item else None,
                affected_resource_id=None,
                baseline_cost_usd=50.0,
                current_cost_usd=total_cost,
                cost_delta_usd=total_cost - 50.0,
                cost_delta_pct=((total_cost / 50.0) - 1) * 100,
            )

        return AnomalyResult(
            is_anomaly=False,
            anomaly_type=AnomalyType.OTHER,
            severity=0.1,
            confidence=0.92,
            reasoning=(
                f"[SKELETON] Cost window within normal range. "
                f"Total: ${total_cost:.2f}. No anomaly detected."
            ),
            affected_account=top_item.account_id if top_item else None,
            affected_service=top_item.service if top_item else None,
            baseline_cost_usd=50.0,
            current_cost_usd=total_cost,
            cost_delta_usd=0.0,
            cost_delta_pct=0.0,
        )
