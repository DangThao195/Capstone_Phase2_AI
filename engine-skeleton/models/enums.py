"""
Domain enumerations for the FinOps Watch AI Engine.
Centralised here so every module speaks the same vocabulary.
Easy to extend when curveballs add new anomaly types or actions.

Updated for Contract v1.1 — Detect → Decide → Verify closed-loop.
"""

from enum import Enum


# ---------------------------------------------------------------------------
# Anomaly detection
# ---------------------------------------------------------------------------

class AnomalyType(str, Enum):
    """Categories of cost anomalies the engine can detect.
    Aligned with AI API Contract §6 Anomaly Types Enum Reference.
    """
    RUNAWAY_USAGE = "runaway_usage"          # Compute quên tắt, chạy 24/7
    IDLE_RESOURCE = "idle_resource"          # Provisioned nhưng ~0 usage, kéo dài
    UNTAGGED_SPEND = "untagged_spend"        # Thiếu tag team → không phân bổ được
    SUDDEN_SPIKE = "sudden_spike"            # Tăng vọt ngắn ngày do misconfig
    GRADUAL_DRIFT = "gradual_drift"          # Bò lên từ từ nhiều tuần
    # -- Extend here for curveball new types --
    OTHER = "other"


class Severity(str, Enum):
    """Anomaly severity level. Contract §5.5 anomalies_list."""
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


# ---------------------------------------------------------------------------
# Alert routing
# ---------------------------------------------------------------------------

class AlertRoute(str, Enum):
    """Who should receive the alert."""
    FINANCE = "finance"
    ENGINEERING = "engineering"
    BOTH = "both"


# ---------------------------------------------------------------------------
# Data ingestion
# ---------------------------------------------------------------------------

class DataSourceType(str, Enum):
    """How CDO sends CUR data. Contract §5.1 detect request."""
    RAW_JSON = "RAW_JSON"
    S3_POINTER = "S3_POINTER"


# ---------------------------------------------------------------------------
# Environment classification
# ---------------------------------------------------------------------------

class Environment(str, Enum):
    """Resource environment classification.
    Expanded for contract v1.1 — includes prod variants and specialized envs.
    """
    PROD = "prod"
    PROD_CORE = "prod-core"
    PROD_PAYMENTS = "prod-payments"
    STAGING = "staging"
    DEV = "dev"
    SANDBOX = "sandbox"
    ML_RESEARCH = "ml-research"
    DATA_ANALYTICS = "data-analytics"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Containment actions (contract §7)
# ---------------------------------------------------------------------------

class ContainmentAction(str, Enum):
    """Safe actions the engine can recommend.
    Aligned with AI API Contract §7 Containment Actions Enum Reference.
    """
    TAG_FOR_REVIEW = "tag-for-review"
    TIME_GATED_COUNTDOWN = "time-gated-countdown"
    AUTO_SHUTDOWN = "auto-shutdown"
    QUOTA_CAP = "quota-cap"


class SuggestedAction(str, Enum):
    """Internal engine suggested actions (superset of ContainmentAction).
    Used by engine/containment.py for decision logic.
    """
    ALERT_ONLY = "alert_only"
    TAG_FOR_REVIEW = "tag_for_review"
    SCHEDULE_SHUTDOWN = "schedule_shutdown"
    QUOTA_CAP = "quota_cap"
    INVESTIGATE = "investigate"
    # -- Extend here for curveball --


class ContainmentStatus(str, Enum):
    """Status of a containment action (internal tracking)."""
    DRY_RUN = "dry_run"
    EXECUTED = "executed"
    SKIPPED_PROD = "skipped_prod"
    ESCALATED = "escalated"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Async processing status
# ---------------------------------------------------------------------------

class DetectionStatus(str, Enum):
    """Status of an async detection job. Contract §5.5 Case A."""
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class RemediationStatus(str, Enum):
    """Status of a remediation lifecycle. Contract §5.5 Case B."""
    PENDING_APPROVAL = "PENDING_APPROVAL"
    IN_PROGRESS = "IN_PROGRESS"
    SUCCESS = "SUCCESS"
    ROLLED_BACK = "ROLLED_BACK"
    ESCALATED = "ESCALATED"


class VerifyNextAction(str, Enum):
    """Next action after verify. Contract §5.3 response."""
    DONE = "DONE"
    RETRY = "RETRY"
    ROLLBACK = "ROLLBACK"
    ESCALATE = "ESCALATE"
