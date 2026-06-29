import pytest
from engine.llm_client import BedrockLLMClient
from config.settings import get_settings

def test_llm_client_fallback_when_disabled():
    """Verify that BedrockLLMClient returns template fallbacks directly when disabled."""
    settings = get_settings()
    # Ensure it is disabled (default)
    settings.enable_llm_analysis = False
    
    client = BedrockLLMClient()
    
    # Test runaway_usage fallback
    ctx = {
        "resource_id": "i-1234567890abcdef0",
        "anomaly_type": "runaway_usage",
        "environment": "prod",
        "unblended_cost_24h_usd": 150.0,
        "cost_ratio_to_7d_avg": 5.2,
        "responsible_team": "data-platform",
        "cost_center_code": "CC-99"
    }
    
    res = client.generate_rca(ctx)
    
    assert "technical_reason" in res
    assert "executive_summary" in res
    assert "primary_driver_feature" in res
    assert "missing_mandatory_tags" in res
    assert "runaway processing loop" in res["technical_reason"]
    assert "data-platform" not in res["technical_reason"]  # not hardcoded values, uses template
    assert res["primary_driver_feature"] == "cost_deviation_ratio_7d"
    assert "Owner" in res["missing_mandatory_tags"]

def test_llm_client_fallback_idle_resource():
    """Verify that idle_resource generates the correct template values."""
    client = BedrockLLMClient()
    
    ctx = {
        "resource_id": "db-prod-replica",
        "anomaly_type": "idle_resource",
        "environment": "prod",
        "unblended_cost_24h_usd": 45.0,
        "cost_ratio_to_7d_avg": 1.0,
        "responsible_team": "database-team",
        "cost_center_code": "CC-10"
    }
    
    res = client.generate_rca(ctx)
    assert "idle" in res["technical_reason"].lower()
    assert "waste" in res["executive_summary"].lower()
    assert res["primary_driver_feature"] == "idle_usage_threshold"
    assert "Lifecycle" in res["missing_mandatory_tags"]
