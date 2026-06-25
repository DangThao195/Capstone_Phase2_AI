"""
Application configuration via environment variables.
Uses pydantic-settings for validation and type coercion.
All sensitive values come from env (Secrets Manager in prod).
"""

from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """
    Configuration knobs — override via environment variables.
    Defaults are safe for local dev; production values injected by ECS task definition.
    """

    # --- App identity ---
    app_version: str = Field(default="0.1.0-skeleton", description="Semantic version")
    environment: str = Field(default="development", description="development | staging | production")

    # --- Server ---
    host: str = "0.0.0.0"
    port: int = 8080

    # --- Detection tuning (easy to change for curveball) ---
    confidence_threshold: float = Field(
        default=0.6,
        description="Below this confidence, action is forced to INVESTIGATE",
    )
    default_cadence_hours: int = Field(
        default=24,
        description="Default detection cadence: 12 / 24 / 48 — defended in ADR",
    )
    cost_spike_multiplier: float = Field(
        default=2.0,
        description="Multiplier vs baseline to flag anomaly (e.g. 2.0 = 200%)",
    )

    # --- Untagged spend detection ---
    untagged_cost_threshold_usd: float = Field(
        default=100.0,
        description="Min cost per item to consider for untagged spend check",
    )
    untagged_ratio_threshold: float = Field(
        default=0.5,
        description="Ratio of untagged items in window to flag (0.5 = 50%)",
    )

    # --- Idle resource detection ---
    idle_usage_threshold: float = Field(
        default=0.1,
        description="usage_amount below this → considered idle",
    )

    # --- Gradual drift detection ---
    drift_window_days: int = Field(
        default=14,
        description="Window in days to check for gradual cost increase",
    )
    drift_increase_pct: float = Field(
        default=20.0,
        description="Percent increase over drift window to flag (20 = 20%)",
    )

    # --- Safety boundaries (NEVER overridable in runtime) ---
    never_terminate_prod: bool = Field(default=True, description="Hard boundary")
    never_delete_data: bool = Field(default=True, description="Hard boundary")
    never_modify_iam: bool = Field(default=True, description="Hard boundary")
    allowed_containment_envs: List[str] = Field(
        default=["dev", "sandbox", "ml-research", "staging", "data-analytics"],
        description="Environments where auto-containment is permitted (Contract §7)",
    )

    # --- Audit ---
    audit_retention_days: int = Field(default=90, description="SOC2 minimum")

    # --- Multi-tenant ---
    max_tenants: int = Field(default=10, description="Max concurrent tenants")

    # --- Rate limiting ---
    rate_limit_per_tenant_rpm: int = Field(default=100, description="Requests per minute per tenant (Contract §3)")
    rate_limit_global_rpm: int = Field(default=300, description="Global requests per minute")

    # --- External services (placeholders for W12 real integration) ---
    bedrock_model_id: str = Field(
        default="amazon.nova-pro-v1:0",
        description="Bedrock model ID for LLM-assisted analysis (Contract: Nova Pro)",
    )

    # --- Contract v1.3.0 parameters ---
    error_budget_lock_threshold_pct: float = Field(
        default=1.0,
        description="Rollback rate threshold for PROD (Contract §3.3 — CDO-P3)",
    )
    error_budget_lock_staging_pct: float = Field(
        default=10.0,
        description="Rollback rate threshold for STAGING (Contract §3.3 — CDO-P3)",
    )
    error_budget_lock_dev_pct: float = Field(
        default=100.0,
        description="Dev/Sandbox: never lock (Contract §3.3 — CDO-P3). 100% = effectively disabled.",
    )
    clock_skew_max_seconds: int = Field(
        default=300,
        description="Max allowed request clock skew (Contract §3.1 — 5 min for SigV4)",
    )
    data_timestamp_max_hours: int = Field(
        default=36,
        description="Max allowed CUR data age in hours (Contract §3.1 — CUR natural delay 8-24h)",
    )
    bedrock_region: str = Field(default="ap-southeast-1")
    aws_region: str = Field(default="ap-southeast-1")

    # --- CORS ---
    allowed_origins: List[str] = Field(default=["*"])

    # --- Logging ---
    log_level: str = Field(default="INFO")

    # --- Feature flags (curveball-ready) ---
    enable_llm_analysis: bool = Field(
        default=False,
        description="Toggle LLM-based analysis vs pure rule-based. Off for skeleton.",
    )
    enable_auto_containment: bool = Field(
        default=False,
        description="Toggle auto containment execution. Off = dry-run only.",
    )
    dry_run_mode: bool = Field(
        default=True,
        description="Global dry-run: all containment actions are simulated only.",
    )

    model_config = {"env_prefix": "FINOPS_", "env_file": ".env", "extra": "ignore"}


@lru_cache()
def get_settings() -> Settings:
    """Cached singleton — reload requires process restart."""
    return Settings()
