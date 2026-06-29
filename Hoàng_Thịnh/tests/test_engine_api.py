from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import tempfile
import unittest

import pandas as pd
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]


def normalize(value):
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, dict):
        return {key: normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize(item) for item in value]
    return value


def load_app_module():
    spec = importlib.util.spec_from_file_location("detector_main", ROOT / "engine-skeleton" / "main.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class DetectorApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_app_module()
        cls.client = TestClient(cls.module.app)
        cls.payload = normalize(
            {
                "data_source_type": "RAW_JSON",
                "aws_cost_explorer_daily": pd.read_csv(ROOT / "data" / "cost_explorer_daily.csv").to_dict(orient="records"),
                "aws_cur_line_items": pd.read_csv(ROOT / "data" / "cur_line_items.csv").to_dict(orient="records"),
            }
        )
        raw_response = cls.client.post(
            "/v1/detect",
            headers={"X-Tenant-Id": "demo-tenant", "X-Idempotency-Key": "demo-tenant:2026-06-26"},
            json=cls.payload,
        )
        cls.raw_detect = raw_response.json()
        cls.raw_result = cls.client.get(
            f"/v1/detect/result/{cls.raw_detect['audit_id']}",
            headers={"X-Tenant-Id": "demo-tenant"},
        ).json()
        ce_filtered = pd.read_csv(ROOT / "data" / "cost_explorer_daily.csv")
        ce_filtered = ce_filtered[pd.to_datetime(ce_filtered["date"]) < pd.Timestamp("2026-05-30")]
        cur_filtered = pd.read_csv(ROOT / "data" / "cur_line_items.csv")
        cur_filtered = cur_filtered[pd.to_datetime(cur_filtered["line_item_usage_start_date"]) < pd.Timestamp("2026-05-30", tz="UTC")]
        temp_file = tempfile.NamedTemporaryFile(prefix="cur_pre_est_", suffix=".csv", dir=ROOT / "tests", delete=False)
        cls.pointer_path = Path(temp_file.name)
        temp_file.close()
        cur_filtered.to_csv(cls.pointer_path, index=False)
        s3_response = cls.client.post(
            "/v1/detect",
            headers={"X-Tenant-Id": "tenant-s3"},
            json={
                "data_source_type": "S3_POINTER",
                "aws_cost_explorer_daily": normalize(ce_filtered.to_dict(orient="records")),
                "s3_bucket_uri": str(cls.pointer_path.relative_to(ROOT)),
            },
        )
        cls.s3_detect = s3_response.json()
        cls.s3_result = cls.client.get(
            f"/v1/detect/result/{cls.s3_detect['audit_id']}",
            headers={"X-Tenant-Id": "tenant-s3"},
        ).json()

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "pointer_path") and cls.pointer_path.exists():
            cls.pointer_path.unlink()

    def test_detect_raw_json_and_result(self):
        result_body = self.raw_result
        self.assertGreater(result_body["total_anomalies_found"], 0)
        self.assertIn("telemetry_quality", result_body)
        self.assertIn("temporal_split", result_body)
        self.assertIn("evaluation_context", result_body)
        self.assertEqual(result_body["temporal_split"]["model_type"], "xgboost_supervised_binary")
        self.assertEqual(result_body["temporal_split"]["feature_scaler"], "not_required_tree_model")
        self.assertEqual(result_body["temporal_split"]["cross_validation_strategy"], "walk_forward_expanding_window")
        self.assertGreaterEqual(result_body["temporal_split"]["training_summary"]["cv_folds_completed"], 1)
        self.assertIn(result_body["temporal_split"]["metrics_variant"], ["legacy_split", "unified_hourly", "none"])
        actions = [item["engineering_dashboard_data"]["mitigation_action"]["applied_payload"] for item in result_body["anomalies_list"]]
        self.assertTrue(all("aws_cli_command" not in action for action in actions))

    def test_detect_s3_pointer(self):
        self.assertEqual(self.s3_result["processing_context"]["data_source_type"], "S3_POINTER")

    def test_extend_and_rollback(self):
        audit_id = self.s3_detect["audit_id"]
        result = self.s3_result
        extendable = [
            item
            for item in result["anomalies_list"]
            if item["engineering_dashboard_data"]["mitigation_action"]["enforcement_countdown"]["time_lock_seconds"] > 0
        ]
        target = extendable[0] if extendable else result["anomalies_list"][0]
        anomaly_id = target["anomaly_metadata"]["anomaly_id"]

        extend = self.client.post(
            "/v1/action/extend",
            headers={"X-Tenant-Id": "tenant-s3"},
            json={
                "audit_id": audit_id,
                "anomaly_id": anomaly_id,
                "extend_seconds": 3600,
                "reason": "Need extra review time",
            },
        )
        self.assertEqual(extend.status_code, 200)
        self.assertIn(anomaly_id, extend.json()["affected_anomaly_ids"])

        rollback = self.client.post(
            "/v1/action/rollback",
            headers={"X-Tenant-Id": "tenant-s3"},
            json={
                "audit_id": audit_id,
                "anomaly_id": anomaly_id,
                "requested_by_user": "reviewer@example.com",
                "justification_on_rollback": "Sandbox verification",
            },
        )
        self.assertEqual(rollback.status_code, 200)
        self.assertEqual(rollback.json()["rollback_payload"]["resource_id"], target["anomaly_metadata"]["resource_id"])


if __name__ == "__main__":
    unittest.main()
