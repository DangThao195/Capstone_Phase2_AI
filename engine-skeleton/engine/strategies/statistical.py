"""
Statistical detection strategy — aligned with TF2 real data.
=============================================================
Uses threshold-based spike detection + heuristic anomaly classification
based on CUR 2.0 field patterns from tf2-data/.

Anomaly types detected:
  - runaway_usage:   Compute running 24/7 (GPU, expensive instances)
  - idle_resource:   Provisioned but ~0 usage, kéo dài
  - untagged_spend:  Missing tag 'team' → cannot allocate
  - sudden_spike:    Cost jump from misconfig (high ratio, short window)
  - gradual_drift:   Slow cost increase over weeks

Updated for Contract v1.1: now accepts CostRecord (internal domain model).

W12 TODO:
  - Replace heuristics with ML model (IsolationForest, Z-Score)
  - Add per-service baseline profiling from CUR data store
  - Integrate with historical CUR data for drift detection
"""

from __future__ import annotations

from typing import List, Optional, Any

from engine.strategies.base import DetectionStrategy
from models.domain import AnomalyResult, CostRecord
from models.enums import AnomalyType
from config.settings import get_settings


class StatisticalStrategy(DetectionStrategy):
    """
    Rule-based + statistical detection.
    Uses configurable thresholds from settings (environment-driven).
    Classification heuristics aligned with TF2 dataset anomaly types.
    """

    @property
    def strategy_name(self) -> str:
        return "statistical_v1"

    def detect(
        self,
        cost_window: List[CostRecord],
        baseline: Optional[object],
        tenant_id: str,
        utilization_metrics: Optional[List[Any]] = None,
        business_context: Optional[Any] = None,
    ) -> AnomalyResult:
        settings = get_settings()

        total_cost = sum(item.cost_usd for item in cost_window)
        baseline_avg = 0.0
        if baseline and hasattr(baseline, "baseline_avg_daily_cost_usd"):
            baseline_avg = baseline.baseline_avg_daily_cost_usd or 0.0

        # Extract representative item for enrichment
        top_item = max(cost_window, key=lambda x: x.cost_usd) if cost_window else None

        # Guard: no baseline → can't compare → flag for investigation
        if baseline_avg <= 0:
            return AnomalyResult(
                is_anomaly=False,
                anomaly_type=AnomalyType.OTHER,
                severity=0.2,
                confidence=0.3,
                reasoning="Insufficient baseline data for comparison. Manual review recommended.",
                affected_account=top_item.account_id if top_item else None,
                affected_service=top_item.service if top_item else None,
                current_cost_usd=total_cost,
                baseline_cost_usd=0.0,
                cost_delta_usd=total_cost,
                cost_delta_pct=0.0,
            )

        # --- Check untagged spend first (independent of spike) ---
        untagged_result = self._check_untagged(cost_window, settings)
        if untagged_result:
            return untagged_result

        # Spike detection: current vs baseline multiplier
        ratio = total_cost / baseline_avg
        is_spike = ratio >= settings.cost_spike_multiplier

        if is_spike:
            # Classify anomaly type based on heuristics
            anomaly_type = self._classify_spike(cost_window, ratio)
            severity = min(1.0, (ratio - 1.0) / 5.0)  # normalize to 0-1
            confidence = min(0.95, 0.5 + (ratio - settings.cost_spike_multiplier) * 0.1)

            return AnomalyResult(
                is_anomaly=True,
                anomaly_type=anomaly_type,
                severity=round(severity, 2),
                confidence=round(confidence, 2),
                reasoning=(
                    f"Cost spike detected: ${total_cost:.2f} vs baseline ${baseline_avg:.2f}/day "
                    f"({ratio:.1f}x). Threshold: {settings.cost_spike_multiplier}x."
                )[:300],
                affected_account=top_item.account_id if top_item else None,
                affected_service=top_item.service if top_item else None,
                baseline_cost_usd=baseline_avg,
                current_cost_usd=total_cost,
                cost_delta_usd=round(total_cost - baseline_avg, 2),
                cost_delta_pct=round((ratio - 1) * 100, 1),
            )

        return AnomalyResult(
            is_anomaly=False,
            anomaly_type=AnomalyType.OTHER,
            severity=0.05,
            confidence=0.9,
            reasoning=f"Cost ${total_cost:.2f} within normal range vs baseline ${baseline_avg:.2f}/day ({ratio:.1f}x).",
            affected_account=top_item.account_id if top_item else None,
            affected_service=top_item.service if top_item else None,
            baseline_cost_usd=baseline_avg,
            current_cost_usd=total_cost,
            cost_delta_usd=round(total_cost - baseline_avg, 2),
            cost_delta_pct=round((ratio - 1) * 100, 1),
        )

    def _check_untagged(self, cost_window: List[CostRecord], settings) -> Optional[AnomalyResult]:
        """
        Check for untagged_spend: items missing 'team' tag with significant cost.
        Based on TF2 data pattern: resource_tags_user_team is empty.
        """
        untagged_items = []
        for item in cost_window:
            team_tag = item.tags.get("team", "").strip() if item.tags else ""
            if not team_tag and item.cost_usd >= settings.untagged_cost_threshold_usd:
                untagged_items.append(item)

        untagged_ratio = len(untagged_items) / len(cost_window) if cost_window else 0
        if untagged_ratio >= settings.untagged_ratio_threshold and untagged_items:
            total_untagged_cost = sum(i.cost_usd for i in untagged_items)
            top_untagged = max(untagged_items, key=lambda x: x.cost_usd)
            return AnomalyResult(
                is_anomaly=True,
                anomaly_type=AnomalyType.UNTAGGED_SPEND,
                severity=min(1.0, total_untagged_cost / 1000.0),
                confidence=round(0.6 + untagged_ratio * 0.3, 2),
                reasoning=(
                    f"Untagged spend detected: {len(untagged_items)}/{len(cost_window)} items "
                    f"({untagged_ratio:.0%}) missing 'team' tag. "
                    f"Total untagged cost: ${total_untagged_cost:.2f}."
                )[:300],
                affected_account=top_untagged.account_id,
                affected_service=top_untagged.service,
                current_cost_usd=total_untagged_cost,
                baseline_cost_usd=0.0,
                cost_delta_usd=total_untagged_cost,
                cost_delta_pct=100.0,
            )
        return None

    def _classify_spike(self, cost_window: List[CostRecord], ratio: float) -> AnomalyType:
        """
        Classify spike anomaly type using heuristics aligned with TF2 data.
        Order matters: check most specific patterns first.
        """
        # --- runaway_usage: expensive compute (GPU, large instances) running continuously ---
        expensive_compute = [
            item for item in cost_window
            if item.cost_usd > 50
            and any(kw in (item.usage_type or "").lower() for kw in [
                "p3.", "p4.", "g4.", "g5.", "ml.", "gpu", "boxusage:p", "boxusage:g",
            ])
        ]
        if expensive_compute:
            return AnomalyType.RUNAWAY_USAGE

        # --- sudden_spike: high ratio but likely short-lived ---
        if ratio >= 3.0:
            return AnomalyType.SUDDEN_SPIKE

        # --- Check for potential idle by environment (dev/sandbox only) ---
        envs = {item.environment.value.lower() for item in cost_window}
        if envs.issubset({"dev", "sandbox", "test", "unknown"}):
            return AnomalyType.IDLE_RESOURCE

        return AnomalyType.SUDDEN_SPIKE
