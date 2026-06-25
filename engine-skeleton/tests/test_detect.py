"""
Tests for Contract v1.3.0 API endpoints.
==========================================
Tests full flow: detect(200 sync) → status → decide(boto3_equivalent) → verify → rollback(audit).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)

# All non-health requests require X-Tenant-Id header (middleware)
HEADERS = {"X-Tenant-Id": "test-tenant-001"}


# ---------------------------------------------------------------------------
# Test data fixtures — v1.3.0 aligned
# ---------------------------------------------------------------------------

def _detect_payload() -> dict:
    """Minimal valid detect request — CUR primary mode (v1.3.0)."""
    return {
        "data_source_type": "RAW_JSON",
        "is_ad_hoc": False,
        "telemetry_delay_event": False,
        "aws_cur_line_items": [
            {
                "line_item_usage_start_date": "2026-06-23T00:00:00Z",
                "line_item_usage_account_id": "200000000012",
                "line_item_usage_type": "BoxUsage:g4dn.xlarge",
                "line_item_resource_id": "i-0abcd1234efgh5678",
                "line_item_usage_amount": 24.0,
                "pricing_unit": "Hrs",
                "line_item_unblended_cost": 427.50,
                "usage_density_24h": 1.0,
                "resource_tags_user_environment": "ml-research",
                "resource_tags_user_team": "squad-ml-core",
            }
        ],
        "resource_utilization_metrics": [
            {
                "resource_id": "i-0abcd1234efgh5678",
                "cpu_percent": 95.0,
                "cpu_utilization_hourly": [
                    91, 93, 90, 92, 94, 91, 93, 90, 92, 94,
                    91, 93, 90, 92, 94, 91, 93, 90, 92, 94,
                    91, 93, 90, 92,
                ],
                "network_in_bytes": 1048576,
                "network_out_bytes": 2048576,
            }
        ],
    }


def _detect_payload_ce_fallback() -> dict:
    """Detect request in CE fallback mode (telemetry_delay_event=true)."""
    return {
        "data_source_type": "RAW_JSON",
        "is_ad_hoc": False,
        "telemetry_delay_event": True,
        "aws_cost_explorer_daily": [
            {
                "date": "2026-06-23",
                "linked_account_id": "200000000012",
                "linked_account_name": "squad-ml-research",
                "service_code": "AmazonEC2",
                "service": "Amazon Elastic Compute Cloud - Compute",
                "region": "ap-southeast-1",
                "unblended_cost": 427.50,
                "cost_ratio_to_7d_avg": 18.2,
                "day_of_week": 1,
                "is_weekend": False,
                "is_estimated": True,
            }
        ],
    }


def _decide_payload(correlation_id: str) -> dict:
    """Valid decide request."""
    return {
        "correlation_id": correlation_id,
        "idempotency_key": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "dry_run_mode": True,
        "anomaly_context": {
            "anomaly_id": "ANM-2026-0623A",
            "anomaly_type": "runaway_usage",
            "resource_id": "i-0abcd1234efgh5678",
            "environment": "ml-research",
            "unblended_cost_24h_usd": 427.50,
            "cost_ratio_to_7d_avg": 18.2,
            "responsible_team": "squad-ml-core",
            "cost_center_code": "CC-9001",
        },
    }


def _verify_payload(correlation_id: str) -> dict:
    """Valid verify request — action completed, CUR-based post telemetry."""
    return {
        "correlation_id": correlation_id,
        "idempotency_key": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
        "dry_run_mode": True,
        "action_executed": {
            "action": "tag-for-review",
            "target": "i-0abcd1234efgh5678",
            "status": "COMPLETED",
            "execution_time_seconds": 3,
        },
        "post_telemetry_window": {
            "data_source_type": "RAW_JSON",
            "telemetry_delay_event": False,
            "aws_cur_line_items": [
                {
                    "line_item_usage_start_date": "2026-06-24T00:00:00Z",
                    "line_item_usage_account_id": "200000000012",
                    "line_item_usage_type": "BoxUsage:g4dn.xlarge",
                    "line_item_resource_id": "i-0abcd1234efgh5678",
                    "line_item_usage_amount": 0.0,
                    "pricing_unit": "Hrs",
                    "line_item_unblended_cost": 0.00,
                    "usage_density_24h": 0.0,
                    "resource_tags_user_environment": "ml-research",
                }
            ],
        },
    }


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

class TestHealthCheck:
    def test_health_returns_200(self):
        r = client.get("/health")
        assert r.status_code == 200

    def test_health_has_services(self):
        r = client.get("/health")
        body = r.json()
        assert body["status"] == "healthy"
        assert "services" in body
        assert body["services"]["s3_audit_bucket"] == "connected"
        assert body["services"]["bedrock_api"] == "accessible"
        assert body["services"]["s3_cur_bucket"] == "reachable"

    def test_health_has_timestamp(self):
        r = client.get("/health")
        body = r.json()
        assert "timestamp" in body


# ---------------------------------------------------------------------------
# POST /v1/detect — 200 sync (v1.3.0)
# ---------------------------------------------------------------------------

class TestDetect:
    def test_detect_returns_200(self):
        """v1.3.0: detect is synchronous, returns 200 (not 202)."""
        r = client.post("/v1/detect", json=_detect_payload(), headers=HEADERS)
        assert r.status_code == 200

    def test_detect_returns_full_response(self):
        """v1.3.0: response includes anomalies_list and data_confidence."""
        r = client.post("/v1/detect", json=_detect_payload(), headers=HEADERS)
        body = r.json()
        assert body["success"] is True
        assert "correlation_id" in body
        assert "anomalies_detected" in body
        assert "data_confidence" in body
        assert "anomalies_list" in body
        assert isinstance(body["anomalies_list"], list)

    def test_detect_cur_primary_data_confidence_high(self):
        """When CUR data is available, data_confidence should be HIGH."""
        r = client.post("/v1/detect", json=_detect_payload(), headers=HEADERS)
        body = r.json()
        assert body["data_confidence"] == "HIGH"

    def test_detect_ce_fallback_data_confidence_low(self):
        """When telemetry_delay_event=true (CE fallback), data_confidence should be LOW."""
        r = client.post("/v1/detect", json=_detect_payload_ce_fallback(), headers=HEADERS)
        body = r.json()
        assert body["data_confidence"] == "LOW"

    def test_detect_cur_mode_no_ce_required(self):
        """v1.3.0: CUR mode does NOT require aws_cost_explorer_daily."""
        payload = _detect_payload()
        assert "aws_cost_explorer_daily" not in payload
        r = client.post("/v1/detect", json=payload, headers=HEADERS)
        assert r.status_code == 200

    def test_detect_anomaly_response_has_required_fields(self):
        """Each anomaly in anomalies_list must have all contract-required fields."""
        r = client.post("/v1/detect", json=_detect_payload(), headers=HEADERS)
        body = r.json()
        if body["anomalies_detected"] and body["anomalies_list"]:
            anomaly = body["anomalies_list"][0]
            assert "anomaly_id" in anomaly
            assert "anomaly_type" in anomaly
            assert "severity" in anomaly
            assert "confidence_score" in anomaly
            assert "resource_id" in anomaly
            assert "environment" in anomaly
            assert "ai_model_used" in anomaly
            assert "alert_routing" in anomaly
            assert "finance" in anomaly["alert_routing"]
            assert "engineering" in anomaly["alert_routing"]

    def test_detect_invalid_schema_returns_422(self):
        r = client.post("/v1/detect", json={"data_source_type": "INVALID"}, headers=HEADERS)
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# GET /v1/status/{id} — Case A (detection)
# ---------------------------------------------------------------------------

class TestStatusDetection:
    def test_status_completed_after_detect(self):
        # Step 1: detect
        r = client.post("/v1/detect", json=_detect_payload(), headers=HEADERS)
        cid = r.json()["correlation_id"]

        # Step 2: poll status
        r = client.get(f"/v1/status/{cid}", headers=HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "COMPLETED"
        assert body["correlation_id"] == cid

    def test_status_has_anomalies_list(self):
        r = client.post("/v1/detect", json=_detect_payload(), headers=HEADERS)
        cid = r.json()["correlation_id"]

        r = client.get(f"/v1/status/{cid}", headers=HEADERS)
        body = r.json()
        assert "anomalies_detected" in body
        assert "anomalies_list" in body

    def test_status_not_found(self):
        r = client.get("/v1/status/00000000-0000-0000-0000-000000000000", headers=HEADERS)
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /v1/status/{id} — Case B (remediation)
# ---------------------------------------------------------------------------

class TestStatusRemediation:
    def test_remediation_status_returns_stub(self):
        r = client.get("/v1/status/ANM-2026-0623A", headers=HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["audit_id"] == "ANM-2026-0623A"
        assert body["status"] == "PENDING_APPROVAL"
        assert "error_budget_remaining_pct" in body


# ---------------------------------------------------------------------------
# POST /v1/decide
# ---------------------------------------------------------------------------

class TestDecide:
    def test_decide_returns_action_plan(self):
        r = client.post("/v1/detect", json=_detect_payload(), headers=HEADERS)
        cid = r.json()["correlation_id"]

        r = client.post("/v1/decide", json=_decide_payload(cid), headers=HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert "matched_runbook" in body
        assert "action_plan" in body
        assert len(body["action_plan"]) >= 1
        assert body["action_plan"][0]["action"] == "tag-for-review"

    def test_decide_has_payloads_with_boto3(self):
        """v1.3.0: rollback_payload MUST include boto3_equivalent."""
        r = client.post("/v1/detect", json=_detect_payload(), headers=HEADERS)
        cid = r.json()["correlation_id"]

        r = client.post("/v1/decide", json=_decide_payload(cid), headers=HEADERS)
        body = r.json()
        assert "applied_payload" in body
        assert "rollback_payload" in body
        assert "aws_cli_command" in body["applied_payload"]
        assert "aws_cli_rollback_command" in body["rollback_payload"]
        # v1.3.0: boto3_equivalent is required
        assert "boto3_equivalent" in body["rollback_payload"]
        boto3_eq = body["rollback_payload"]["boto3_equivalent"]
        assert "service" in boto3_eq
        assert "method" in boto3_eq
        assert "parameters" in boto3_eq

    def test_decide_has_dashboard_data(self):
        r = client.post("/v1/detect", json=_detect_payload(), headers=HEADERS)
        cid = r.json()["correlation_id"]

        r = client.post("/v1/decide", json=_decide_payload(cid), headers=HEADERS)
        body = r.json()
        assert "finance_dashboard_data" in body
        assert "engineering_dashboard_data" in body
        assert "executive_summary" in body["finance_dashboard_data"]
        assert "root_cause_analysis" in body["engineering_dashboard_data"]

    def test_decide_echoes_dry_run(self):
        r = client.post("/v1/detect", json=_detect_payload(), headers=HEADERS)
        cid = r.json()["correlation_id"]

        r = client.post("/v1/decide", json=_decide_payload(cid), headers=HEADERS)
        body = r.json()
        assert body["dry_run_mode"] is True
        assert body["correlation_id"] == cid

    def test_decide_runaway_has_countdown_step(self):
        r = client.post("/v1/detect", json=_detect_payload(), headers=HEADERS)
        cid = r.json()["correlation_id"]

        r = client.post("/v1/decide", json=_decide_payload(cid), headers=HEADERS)
        body = r.json()
        # runaway_usage should have 2 steps: tag + countdown
        assert len(body["action_plan"]) == 2
        assert body["action_plan"][1]["action"] == "time-gated-countdown"


# ---------------------------------------------------------------------------
# POST /v1/verify
# ---------------------------------------------------------------------------

class TestVerify:
    def test_verify_done_when_cost_drops(self):
        r = client.post("/v1/detect", json=_detect_payload(), headers=HEADERS)
        cid = r.json()["correlation_id"]

        r = client.post("/v1/verify", json=_verify_payload(cid), headers=HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is True
        assert body["next_action"] == "DONE"

    def test_verify_escalate_when_cost_high(self):
        r = client.post("/v1/detect", json=_detect_payload(), headers=HEADERS)
        cid = r.json()["correlation_id"]

        payload = _verify_payload(cid)
        payload["post_telemetry_window"]["aws_cur_line_items"][0]["line_item_unblended_cost"] = 500.0

        r = client.post("/v1/verify", json=payload, headers=HEADERS)
        body = r.json()
        assert body["success"] is False
        assert body["next_action"] == "ESCALATE"
        assert "escalation_bundle" in body

    def test_verify_rollback_when_action_failed(self):
        r = client.post("/v1/detect", json=_detect_payload(), headers=HEADERS)
        cid = r.json()["correlation_id"]

        payload = _verify_payload(cid)
        payload["action_executed"]["status"] = "FAILED"

        r = client.post("/v1/verify", json=payload, headers=HEADERS)
        body = r.json()
        assert body["next_action"] == "ROLLBACK"


# ---------------------------------------------------------------------------
# POST /v1/audit/{id}/rollback — v1.3.0 (CDO-P1)
# ---------------------------------------------------------------------------

class TestRollback:
    def test_rollback_returns_audit_recorded(self):
        """v1.3.0: CDO sends notification after executing rollback via boto3."""
        r = client.post(
            "/v1/audit/ANM-2026-0623A/rollback",
            json={
                "reason": "False positive — approved experiment",
                "rolled_back_by": "engineer@company.com",
                "rollback_executed_at": "2026-06-23T18:30:00Z",
                "rollback_status": "SUCCESS",
            },
            headers=HEADERS,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["audit_recorded"] is True
        assert body["false_positive_count_updated"] is True
        assert body["new_error_budget_burned_pct"] > 0

    def test_rollback_with_boto3_result(self):
        """CDO can optionally send raw boto3 response for audit logging."""
        r = client.post(
            "/v1/audit/ANM-2026-0623B/rollback",
            json={
                "reason": "Cost regression detected",
                "rolled_back_by": "sre@company.com",
                "rollback_executed_at": "2026-06-23T19:00:00Z",
                "rollback_status": "SUCCESS",
                "boto3_result": {
                    "ResponseMetadata": {"HTTPStatusCode": 200}
                },
            },
            headers=HEADERS,
        )
        assert r.status_code == 200

    def test_rollback_locks_after_threshold(self):
        # Use a separate tenant to avoid pollution from other tests
        lock_headers = {"X-Tenant-Id": "test-tenant-lock-v13"}
        for _ in range(3):
            client.post(
                "/v1/audit/ANM-2026-0623X/rollback",
                json={
                    "reason": "Testing budget lock",
                    "rolled_back_by": "test@company.com",
                    "rollback_executed_at": "2026-06-23T20:00:00Z",
                    "rollback_status": "SUCCESS",
                },
                headers=lock_headers,
            )

        # After 3 rollbacks (0.5% each = 1.5%), should be locked (threshold 1%)
        r = client.post(
            "/v1/audit/ANM-2026-0623X/rollback",
            json={
                "reason": "One more",
                "rolled_back_by": "test@company.com",
                "rollback_executed_at": "2026-06-23T21:00:00Z",
                "rollback_status": "SUCCESS",
            },
            headers=lock_headers,
        )
        body = r.json()
        assert body["containment_locked"] is True


# ---------------------------------------------------------------------------
# Full E2E flow
# ---------------------------------------------------------------------------

class TestE2EFlow:
    def test_detect_status_decide_verify_flow(self):
        """Full closed-loop: detect(200) → status → decide(boto3_eq) → verify."""
        # 1. Detect (sync 200)
        r = client.post("/v1/detect", json=_detect_payload(), headers=HEADERS)
        assert r.status_code == 200
        detect_body = r.json()
        cid = detect_body["correlation_id"]
        assert detect_body["data_confidence"] == "HIGH"

        # 2. Status (poll — should already be COMPLETED)
        r = client.get(f"/v1/status/{cid}", headers=HEADERS)
        assert r.status_code == 200
        assert r.json()["status"] == "COMPLETED"

        # 3. Decide (includes boto3_equivalent)
        r = client.post("/v1/decide", json=_decide_payload(cid), headers=HEADERS)
        assert r.status_code == 200
        decide_body = r.json()
        assert decide_body["matched_runbook"] is not None
        assert "boto3_equivalent" in decide_body["rollback_payload"]

        # 4. Verify
        r = client.post("/v1/verify", json=_verify_payload(cid), headers=HEADERS)
        assert r.status_code == 200
        assert r.json()["next_action"] == "DONE"
