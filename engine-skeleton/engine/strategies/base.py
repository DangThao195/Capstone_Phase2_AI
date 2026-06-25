"""
Detection strategy interface (Strategy Pattern).
==================================================
All detection algorithms implement this ABC.
Swap strategies via config or feature flag — no code change in router.

W11 skeleton: DummyStrategy (hardcoded responses)
W12 real:     StatisticalStrategy / LLMStrategy (plug in seamlessly)
Curveball:    CompositeStrategy (chain multiple strategies)

Updated for Contract v1.1: strategies now receive internal CostRecord objects
instead of API schema objects. Router handles the schema → domain conversion.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from models.domain import AnomalyResult, CostRecord


class DetectionStrategy(ABC):
    """Abstract base for all anomaly detection strategies."""

    @abstractmethod
    def detect(
        self,
        cost_window: List[CostRecord],
        baseline: Optional[object],
        tenant_id: str,
    ) -> AnomalyResult:
        """
        Analyse cost data and return an anomaly result.

        Args:
            cost_window: Current cost data points (internal CostRecord format).
            baseline: Historical baseline metadata for comparison (optional).
            tenant_id: Tenant identifier for multi-tenant isolation.

        Returns:
            AnomalyResult with detection findings.
        """
        ...

    @property
    @abstractmethod
    def strategy_name(self) -> str:
        """Human-readable name for audit logging."""
        ...
