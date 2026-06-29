from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
ENGINE_DIR = ROOT / "engine-skeleton"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from detect_core import build_result
from llm_rca import RcaGeneration


class StubGenerator:
    provider = "stub"
    model_id = "stub-model"
    enabled = True

    def generate(self, event, fallback):
        return RcaGeneration(
            executive_summary="LLM executive summary",
            technical_reason="LLM technical reason",
            primary_driver_feature="xgb_score",
            recommended_follow_up="Review the log retention and debug flag settings.",
            status="generated",
            provider=self.provider,
            model_id=self.model_id,
            latency_ms=12,
        )


class LlmRcaTests(unittest.TestCase):
    def test_build_result_uses_llm_generator(self):
        events = pd.DataFrame(
            [
                {
                    "anomaly_type": "sudden_spike",
                    "resource_id": "log-group-debug-runaway",
                    "primary_resource_id": "log-group-debug-runaway",
                    "impacted_resource_ids": ["log-group-debug-runaway"],
                    "impacted_resource_count": 1,
                    "incident_scope": "single_resource",
                    "resource_family": "debug",
                    "account_id": 200000000013,
                    "account_name": "dev",
                    "environment": "dev",
                    "service_code": "AmazonCloudWatch",
                    "usage_type": "DataProcessing-Bytes",
                    "pricing_unit": "GB",
                    "team": "platform",
                    "owner": None,
                    "cost_center": "CC-1002",
                    "event_start": pd.Timestamp("2026-05-01"),
                    "event_end": pd.Timestamp("2026-05-04"),
                    "event_days": 4,
                    "event_total_cost": 1055.54,
                    "avg_daily_cost": 263.89,
                    "latest_daily_cost": 269.24,
                    "usage_amount_24h": 1.0,
                    "usage_density_24h": 1.0,
                    "cost_ratio_to_7d_avg": 1.03,
                    "resource_cost_ratio_to_7d_avg": 1.03,
                    "resource_usage_ratio_to_7d_avg": 1.01,
                    "resource_network_ratio_to_7d_avg": None,
                    "resource_cost_mean_ratio_28vprev28": None,
                    "resource_usage_mean_ratio_28vprev28": None,
                    "detector_score": 0.85,
                    "confidence_score": 0.99,
                    "ranking_score": 0.99,
                    "type_hint_confidence": 0.74,
                    "is_estimated": False,
                    "xgb_support": True,
                    "xgb_score": 0.63,
                    "candidate_sources": ["xgboost_supervised", "heuristic_type_mapper"],
                    "cpu_percent": None,
                    "gpu_utilization": None,
                    "database_connections": None,
                    "network_out_bytes": None,
                    "migration_flag": False,
                    "load_test_flag": False,
                    "campaign_flag": False,
                    "benign_growth_signal": False,
                    "estimated_penalty": 0.0,
                    "benign_context_penalty": 0.0,
                }
            ]
        )

        result = build_result(events, audit_id="audit-1", rca_generator=StubGenerator())
        anomaly = result["anomalies_list"][0]
        root = anomaly["engineering_dashboard_data"]["root_cause_analysis"]
        self.assertEqual(anomaly["finance_dashboard_data"]["executive_summary"], "LLM executive summary")
        self.assertEqual(root["technical_reason"], "LLM technical reason")
        self.assertEqual(root["primary_driver_feature"], "xgb_score")
        self.assertEqual(root["recommended_follow_up"], "Review the log retention and debug flag settings.")
        self.assertEqual(root["llm_provider"], "stub")
        self.assertEqual(root["llm_model_id"], "stub-model")
        self.assertEqual(root["llm_rca_status"], "generated")


if __name__ == "__main__":
    unittest.main()
