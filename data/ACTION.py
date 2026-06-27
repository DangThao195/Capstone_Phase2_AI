# ACTION.py
# Stage 2: LLM Mitigation Engine (Amazon Nova Lite via Bedrock)
# Stage 3: Action Executor (dry_run / live)
# Stage 4: Audit Logger (DynamoDB, retention 90 days)
# Stage 5: Output JSON builder + Slack notifier
# Ref: data/plan2.md
#
# Usage:
#   from ACTION import run_action_pipeline
#   result = run_action_pipeline(record, rca, dry_run=True)

import json
import logging
import os
import re
import subprocess
import threading
import uuid
from datetime import datetime, timezone

import boto3
import requests
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ── Constants ─────────────────────────────────────────────────────────────────
NOVA_LITE_MODEL  = "amazon.nova-lite-v1:0"
BEDROCK_REGION   = os.environ.get("BEDROCK_REGION", "us-east-1")
DYNAMO_TABLE     = os.environ.get("FINOPS_AUDIT_TABLE", "FinOps_Audit_Store")
DYNAMO_REGION    = os.environ.get("DYNAMO_REGION", "us-east-1")
AUDIT_TTL_DAYS   = 90
STAGING_LOCK_SEC = 14400   # 4 hours

SAFE_ENVS = {"dev", "sandbox", "ml-research", "data-analytics"}

ENV_SLACK_COLORS = {
    "prod":           "#FF0000",
    "staging":        "#FF9800",
    "dev":            "#2196F3",
    "sandbox":        "#2196F3",
    "ml-research":    "#9C27B0",
    "data-analytics": "#4CAF50",
}

_bedrock  = None
_dynamodb = None


def _get_bedrock():
    global _bedrock
    if _bedrock is None:
        _bedrock = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
    return _bedrock


def _get_dynamo_table():
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.resource("dynamodb", region_name=DYNAMO_REGION)
    return _dynamodb.Table(DYNAMO_TABLE)

# =============================================================================
# STAGE 2 - LLM Mitigation Engine (Nova Lite)
# =============================================================================

def _build_mitigation_prompt(record: dict, rca: dict) -> str:
    env        = record.get("environment", "dev")
    confidence = record.get("confidence_score", 0)
    service    = record.get("line_item_product_code", "unknown")
    resource   = record.get("resource_id", "unknown")
    root_cause = rca.get("root_cause_category", "Other")
    risk       = rca.get("risk_level", "Medium")

    return f"""Thong tin anomaly:
- Resource ID   : {resource}
- AWS Service   : {service}
- Moi truong    : {env}
- Confidence    : {confidence:.2f}
- Root Cause    : {root_cause}
- Risk Level    : {risk}

Ma tran hanh dong bat buoc (KHONG duoc sai lech):
- prod           : chi tag-for-review + slack. TUYET DOI khong stop/terminate.
- staging        : tag + time-lock 4h (14400s). Fallback stop sau 4h neu khong co phan hoi.
- dev/sandbox    : neu confidence >= 0.80 -> stop instance. Neu < 0.80 -> tag only.
- ml-research    : neu confidence >= 0.80 -> stop sagemaker notebook. Neu < 0.80 -> tag only.
- data-analytics : quota-cap qua Service Quotas API. KHONG stop/terminate.

Rollback: moi action stop phai kem rollback command tuong ung (start/resume).

Tra ve CHINH XAC JSON sau, khong them gi ngoai JSON:
{{
  "strategy": "<ten chien luoc ngan gon>",
  "immediate_action": "<tag-for-review | stop-instance | stop-notebook | quota-cap | tag-only>",
  "cli_commands": ["<aws cli command 1>", "<aws cli command 2 neu can>"],
  "rollback_command": "<aws cli command de undo action tren>",
  "slack_message": "<noi dung thong bao Slack ngan gon cho team>",
  "enforcement_countdown": {{
    "enabled": <true hoac false>,
    "time_lock_seconds": <0 hoac 14400>,
    "fallback_action": "<none hoac schedule-shutdown>"
  }},
  "requires_human_approval": <true hoac false>
}}"""


def _call_nova_lite(user_prompt: str) -> str:
    system = (
        "Ban la FinOps Automation Engineer. "
        "Nhiem vu: chon dung hanh dong xu ly theo ma tran 5 moi truong AWS. "
        "Tuan thu nghiem ngat: prod chi duoc tag, khong bao gio tu dong tat may. "
        "Chi tra ve JSON thuan tuy."
    )
    body = json.dumps({
        "system":   [{"text": system}],
        "messages": [{"role": "user", "content": [{"text": user_prompt}]}],
        "inferenceConfig": {"maxTokens": 512, "temperature": 0.0},
    })
    resp = _get_bedrock().invoke_model(
        modelId=NOVA_LITE_MODEL,
        body=body,
        contentType="application/json",
        accept="application/json",
    )
    body_out = json.loads(resp["body"].read())
    return body_out["output"]["message"]["content"][0]["text"]


def _parse_mitigation_response(raw: str) -> dict:
    fence = re.search(r"`(?:json)?\s*(\{.*?\})\s*`", raw, re.DOTALL)
    json_str = fence.group(1) if fence else (re.search(r"\{.*\}", raw, re.DOTALL) or type("", (), {"group": lambda s, n: None})()).group(0)
    if not json_str:
        return _fallback_mitigation("Nova Lite response has no JSON")
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        return _fallback_mitigation(f"JSON error: {e}")


def _fallback_mitigation(reason: str) -> dict:
    return {
        "strategy":         "tag-only fallback",
        "immediate_action": "tag-for-review",
        "cli_commands":     [],
        "rollback_command": "",
        "slack_message":    f"FinOps alert - fallback action: {reason}",
        "enforcement_countdown": {"enabled": False, "time_lock_seconds": 0, "fallback_action": "none"},
        "requires_human_approval": True,
        "_fallback": True,
    }


# ── Mock mitigation (khong can Bedrock) ──────────────────────────────────────
def _mock_mitigation(record: dict, rca: dict) -> dict:
    env        = record.get("environment", "dev")
    confidence = record.get("confidence_score", 0)
    resource   = record.get("resource_id", "unknown")
    service    = record.get("line_item_product_code", "AmazonEC2")
    cost       = record.get("line_item_unblended_cost", 0)

    if env == "prod":
        return {
            "strategy":         "Human-in-the-loop (prod safety)",
            "immediate_action": "tag-for-review",
            "cli_commands":     [f"aws ec2 create-tags --resources {resource} --tags Key=FinOps_Alert,Value=Review_Required"],
            "rollback_command": f"aws ec2 delete-tags --resources {resource} --tags Key=FinOps_Alert",
            "slack_message":    f"[PROD ALERT] Resource {resource} flagged for review. Cost: /day. SRE action required.",
            "enforcement_countdown": {"enabled": False, "time_lock_seconds": 0, "fallback_action": "none"},
            "requires_human_approval": True,
        }
    if env == "staging":
        return {
            "strategy":         "Time-gated Containment (staging)",
            "immediate_action": "tag-for-review",
            "cli_commands":     [
                f"aws ec2 create-tags --resources {resource} --tags Key=FinOps_Alert,Value=Staging_Review_Countdown",
            ],
            "rollback_command": f"aws ec2 delete-tags --resources {resource} --tags Key=FinOps_Alert",
            "slack_message":    f"[STAGING] Resource {resource} tagged. 4h countdown started. Cost: /day.",
            "enforcement_countdown": {"enabled": True, "time_lock_seconds": 14400, "fallback_action": "schedule-shutdown"},
            "requires_human_approval": False,
        }
    if env in ("dev", "sandbox"):
        if confidence >= 0.80:
            return {
                "strategy":         "Auto-Containment (dev low-risk)",
                "immediate_action": "stop-instance",
                "cli_commands":     [f"aws ec2 stop-instances --instance-ids {resource}"],
                "rollback_command": f"aws ec2 start-instances --instance-ids {resource}",
                "slack_message":    f"[DEV] Instance {resource} stopped. Confidence={confidence:.2f}. Cost saved: /day.",
                "enforcement_countdown": {"enabled": False, "time_lock_seconds": 0, "fallback_action": "none"},
                "requires_human_approval": False,
            }
        return {
            "strategy":         "Tag-only (low confidence)",
            "immediate_action": "tag-only",
            "cli_commands":     [f"aws ec2 create-tags --resources {resource} --tags Key=FinOps_Alert,Value=Low_Confidence_Review"],
            "rollback_command": f"aws ec2 delete-tags --resources {resource} --tags Key=FinOps_Alert",
            "slack_message":    f"[DEV] Low confidence ({confidence:.2f}). Resource tagged for review.",
            "enforcement_countdown": {"enabled": False, "time_lock_seconds": 0, "fallback_action": "none"},
            "requires_human_approval": True,
        }
    if env == "ml-research":
        if confidence >= 0.80:
            return {
                "strategy":         "Auto-Containment (ml-research GPU idle)",
                "immediate_action": "stop-notebook",
                "cli_commands":     [f"aws sagemaker stop-notebook-instance --notebook-instance-name {resource}"],
                "rollback_command": f"aws sagemaker start-notebook-instance --notebook-instance-name {resource}",
                "slack_message":    f"[ML-RESEARCH] Notebook {resource} stopped. Idle GPU waste eliminated. Confidence={confidence:.2f}.",
                "enforcement_countdown": {"enabled": False, "time_lock_seconds": 0, "fallback_action": "none"},
                "requires_human_approval": False,
            }
        return _fallback_mitigation(f"ml-research confidence {confidence:.2f} < 0.80")
    if env == "data-analytics":
        return {
            "strategy":         "Quota-Cap (data-analytics safe throttle)",
            "immediate_action": "quota-cap",
            "cli_commands":     [
                f"aws service-quotas request-service-quota-increase --service-code {service.lower()} --quota-code L-PLACEHOLDER --desired-value 10"
            ],
            "rollback_command": f"aws service-quotas request-service-quota-increase --service-code {service.lower()} --quota-code L-PLACEHOLDER --desired-value 1000",
            "slack_message":    f"[DATA-ANALYTICS] Quota cap applied to {resource}. Runaway query protection active. Cost: /day.",
            "enforcement_countdown": {"enabled": False, "time_lock_seconds": 0, "fallback_action": "none"},
            "requires_human_approval": False,
        }
    return _fallback_mitigation(f"Unknown environment: {env}")


def decide_mitigation(record: dict, rca: dict) -> dict:
    """Stage 2 entry point: goi Nova Lite de chon action."""
    use_mock = os.environ.get("BEDROCK_MOCK", "false").lower() == "true"
    env      = record.get("environment", "dev")

    logger.info("[MITIGATION] env=%s | confidence=%.2f | root_cause=%s",
                env, record.get("confidence_score", 0), rca.get("root_cause_category"))

    if use_mock:
        logger.info("[MITIGATION] MOCK mode")
        return _mock_mitigation(record, rca)
    try:
        prompt = _build_mitigation_prompt(record, rca)
        raw    = _call_nova_lite(prompt)
        logger.debug("[MITIGATION] Nova Lite raw: %s", raw[:300])
        return _parse_mitigation_response(raw)
    except (ClientError, Exception) as e:
        logger.warning("[MITIGATION] Bedrock error (%s), using mock", e)
        return _mock_mitigation(record, rca)

# =============================================================================
# STAGE 3 - Action Executor (dry_run / live)
# =============================================================================

def _run_or_dry(command: str, dry_run: bool) -> dict:
    """Thuc thi hoac gia lap 1 CLI command."""
    if not command.strip():
        return {"dry_run": dry_run, "command": "", "output": "empty command skipped"}
    if dry_run:
        logger.info("[DRY RUN] Would execute: %s", command)
        return {"dry_run": True, "command": command, "output": "simulated - not executed"}
    try:
        result = subprocess.run(
            command.split(), capture_output=True, text=True, timeout=30
        )
        status = "ok" if result.returncode == 0 else "error"
        logger.info("[LIVE] %s | rc=%d | %s", command[:60], result.returncode, status)
        return {
            "dry_run":    False,
            "command":    command,
            "stdout":     result.stdout.strip(),
            "stderr":     result.stderr.strip(),
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"dry_run": False, "command": command, "error": "timeout after 30s"}
    except Exception as e:
        return {"dry_run": False, "command": command, "error": str(e)}


def _schedule_staging_enforcement(record: dict, mitigation: dict, dry_run: bool):
    """
    Dat timer 4h cho staging.
    Trong production: thay bang Lambda scheduled event / EventBridge rule.
    """
    resource  = record.get("resource_id", "unknown")
    countdown = mitigation.get("enforcement_countdown", {})
    if not countdown.get("enabled") or countdown.get("fallback_action") != "schedule-shutdown":
        return

    def _enforce():
        import time
        logger.info("[STAGING TIMER] 4h countdown started for %s", resource)
        time.sleep(STAGING_LOCK_SEC)
        stop_cmd = f"aws rds stop-db-instance --db-instance-identifier {resource}"
        logger.warning("[STAGING ENFORCEMENT] Time expired, executing: %s", stop_cmd)
        _run_or_dry(stop_cmd, dry_run=dry_run)

    t = threading.Thread(target=_enforce, daemon=True, name=f"staging-lock-{resource[:20]}")
    t.start()
    logger.info("[STAGING TIMER] Thread started (dry_run=%s). Resource: %s", dry_run, resource)


def execute_action(record: dict, mitigation: dict, dry_run: bool = True) -> dict:
    """
    Stage 3: Thuc thi action theo moi truong.
    
    Rules:
    - prod            : khong bao gio tu dong thuc thi, tra ve pending_human_approval
    - staging         : tag ngay + schedule enforcement timer 4h
    - dev/ml-research : neu confidence >= 0.80 -> thuc thi CLI (dry or live)
    - data-analytics  : quota-cap (dry or live)
    - confidence < 0.80 trong dev/ml: chi tag, chua action
    
    dry_run=True (mac dinh) -> in lenh, khong thuc thi that
    """
    env        = record.get("environment", "dev")
    confidence = record.get("confidence_score", 0)
    commands   = mitigation.get("cli_commands", [])

    # Buoc kiem tra an toan: SAFE_ENVS luon dung dry_run khi test
    # Chi tat dry_run khi ENV var FINOPS_DRY_RUN=false ro rang
    global_dry = os.environ.get("FINOPS_DRY_RUN", "true").lower() != "false"
    is_dry     = dry_run or global_dry

    logger.info("[EXECUTOR] env=%s | confidence=%.2f | action=%s | dry_run=%s",
                env, confidence, mitigation.get("immediate_action"), is_dry)

    # prod: TUYET DOI khong tu dong thuc thi
    if env == "prod":
        logger.warning("[EXECUTOR] PROD env - queuing for human approval")
        return {
            "status":            "pending_human_approval",
            "message":           "Production resource - requires SRE manual action on dashboard",
            "commands_queued":   commands,
            "rollback_command":  mitigation.get("rollback_command", ""),
            "dry_run":           False,
        }

    # staging: tag ngay + schedule enforcement
    if env == "staging":
        tag_cmd = commands[0] if commands else ""
        tag_result = _run_or_dry(tag_cmd, is_dry)
        _schedule_staging_enforcement(record, mitigation, is_dry)
        return {
            "status":           "staged",
            "tag_result":       tag_result,
            "countdown_seconds": STAGING_LOCK_SEC,
            "enforcement":      "scheduled" if not is_dry else "dry_run_scheduled",
            "dry_run":          is_dry,
        }

    # dev / sandbox / ml-research: confidence gate
    if env in ("dev", "sandbox", "ml-research"):
        if confidence >= 0.80:
            results = [_run_or_dry(cmd, is_dry) for cmd in commands]
            return {
                "status":   "dry_run_complete" if is_dry else "executed",
                "commands": results,
                "dry_run":  is_dry,
            }
        # confidence < 0.80: chi tag
        tag_cmd = next((c for c in commands if "create-tags" in c), (commands[0] if commands else ""))
        return {
            "status":   "tag_only_low_confidence",
            "result":   _run_or_dry(tag_cmd, is_dry),
            "reason":   f"confidence {confidence:.2f} < 0.80 threshold",
            "dry_run":  is_dry,
        }

    # data-analytics: quota-cap (khong can confidence gate)
    if env == "data-analytics":
        results = [_run_or_dry(cmd, is_dry) for cmd in commands]
        return {
            "status":   "quota_cap_applied" if not is_dry else "dry_run_complete",
            "commands": results,
            "dry_run":  is_dry,
        }

    # Unknown env fallback
    return {"status": "skipped", "reason": f"Unknown environment: {env}", "dry_run": is_dry}

# =============================================================================
# STAGE 4 - Audit Logger (DynamoDB)
# =============================================================================

def log_audit(record: dict, rca: dict, mitigation: dict, execution: dict) -> str:
    """
    Ghi toan bo pipeline result vao DynamoDB Audit Store.
    Retention: 90 ngay (TTL tu dong xoa).
    Rollback command duoc luu de engineer co the undo.
    Returns audit_id.
    """
    now       = datetime.now(timezone.utc)
    audit_id  = str(uuid.uuid4())
    anomaly_id = f"ANM-{now.strftime('%Y%m%d%H%M%S')}-{record.get('environment','?')[:3].upper()}"
    ttl_ts    = int(now.timestamp()) + AUDIT_TTL_DAYS * 86400

    item = {
        "audit_id":            audit_id,
        "anomaly_id":          anomaly_id,
        "timestamp":           now.isoformat(),
        "resource_id":         record.get("resource_id", "unknown"),
        "environment":         record.get("environment", "unknown"),
        "aws_service":         record.get("line_item_product_code", "unknown"),
        "confidence_score":    str(round(record.get("confidence_score", 0), 4)),
        "root_cause_category": rca.get("root_cause_category", "unknown"),
        "risk_level":          rca.get("risk_level", "unknown"),
        "missing_tags":        json.dumps(rca.get("missing_mandatory_tags", [])),
        "action_taken":        mitigation.get("immediate_action", "none"),
        "cli_commands":        json.dumps(mitigation.get("cli_commands", [])),
        "rollback_command":    mitigation.get("rollback_command", ""),
        "execution_status":    execution.get("status", "unknown"),
        "dry_run":             str(execution.get("dry_run", True)),
        "cost_24h_usd":        str(round(record.get("line_item_unblended_cost", 0), 4)),
        "cost_ratio_7d":       str(round(record.get("cost_ratio_to_7d_avg", 1), 2)),
        "ttl":                 ttl_ts,
    }

    use_mock = os.environ.get("BEDROCK_MOCK", "false").lower() == "true"
    if use_mock:
        logger.info("[AUDIT MOCK] Would write to DynamoDB: audit_id=%s anomaly_id=%s", audit_id, anomaly_id)
        return audit_id

    try:
        _get_dynamo_table().put_item(Item=item)
        logger.info("[AUDIT] Written: audit_id=%s | action=%s | env=%s",
                    audit_id, item["action_taken"], item["environment"])
    except ClientError as e:
        logger.error("[AUDIT] DynamoDB write failed: %s", e)

    return audit_id

# =============================================================================
# STAGE 5 - Output JSON Builder + Slack Notifier
# =============================================================================

def build_output_json(record: dict, rca: dict, mitigation: dict, audit_id: str) -> dict:
    """
    Xay dung output JSON 2 block theo spec plan2.md Muc 5:
    - finance_dashboard_data   : danh cho CFO / Finance team
    - engineering_dashboard_data: danh cho Engineering Console / Slack
    """
    now          = datetime.now(timezone.utc)
    cost_24h     = record.get("line_item_unblended_cost", 0)
    monthly_proj = round(cost_24h * 30, 2)

    return {
        "anomaly_metadata": {
            "anomaly_id":       f"ANM-{now.strftime('%Y%m%d%H%M%S')}",
            "audit_id":         audit_id,
            "timestamp":        now.isoformat(),
            "resource_id":      record.get("resource_id", "unknown"),
            "environment":      record.get("environment", "unknown"),
            "confidence_score": round(record.get("confidence_score", 0), 4),
            "ai_model_used":    NOVA_LITE_MODEL,
        },
        "finance_dashboard_data": {
            "target_recipient": "Finance Team & CFO Dashboard",
            "metrics": {
                "unblended_cost_24h_usd":     round(cost_24h, 2),
                "cost_ratio_to_7d_avg":       round(record.get("cost_ratio_to_7d_avg", 1), 2),
                "projected_monthly_waste_usd": monthly_proj,
            },
            "allocation": {
                "responsible_team": record.get("resource_tags_user_team", "unknown"),
                "cost_center_code": record.get("cost_center_code", "CC-UNKNOWN"),
            },
            "executive_summary": rca.get("finance_summary", ""),
        },
        "engineering_dashboard_data": {
            "target_recipient": "Engineering Console & Slack Alert",
            "technical_context": {
                "aws_service":       record.get("line_item_product_code", "unknown"),
                "usage_density_24h": round(record.get("usage_density_24h", 0), 3),
                "cpu_mean":          round(record.get("cpu_mean", 0), 2),
            },
            "root_cause_analysis": {
                "primary_driver_feature": rca.get("primary_driver_feature", ""),
                "technical_reason":       rca.get("technical_reason", ""),
                "missing_mandatory_tags": rca.get("missing_mandatory_tags", []),
            },
            "mitigation_action": {
                "strategy":         mitigation.get("strategy", ""),
                "immediate_action": mitigation.get("immediate_action", ""),
                "applied_payload": {
                    "cli_commands":  mitigation.get("cli_commands", []),
                },
                "rollback_command":      mitigation.get("rollback_command", ""),
                "enforcement_countdown": mitigation.get("enforcement_countdown", {}),
                "requires_human_approval": mitigation.get("requires_human_approval", True),
            },
        },
    }


def send_slack_alert(webhook_url: str, output_json: dict) -> bool:
    """
    Gui thong bao Slack voi mau sac phan biet theo moi truong.
    Tra ve True neu thanh cong.
    """
    if not webhook_url:
        logger.info("[SLACK] No webhook URL - skipping")
        return False

    meta    = output_json["anomaly_metadata"]
    fin     = output_json["finance_dashboard_data"]
    eng     = output_json["engineering_dashboard_data"]

    env     = meta["environment"]
    color   = ENV_SLACK_COLORS.get(env, "#607D8B")
    action  = eng["mitigation_action"]["immediate_action"]
    cost    = fin["metrics"]["unblended_cost_24h_usd"]
    monthly = fin["metrics"]["projected_monthly_waste_usd"]
    summary = fin["executive_summary"]

    payload = {
        "attachments": [{
            "color":  color,
            "title":  f"FinOps Alert: {env.upper()} | {action.upper()}",
            "text":   summary,
            "fields": [
                {"title": "Resource",         "value": meta["resource_id"],  "short": False},
                {"title": "Cost 24h",         "value": f"",       "short": True},
                {"title": "Projected/Month",  "value": f"",    "short": True},
                {"title": "Root Cause",       "value": eng["root_cause_analysis"].get("primary_driver_feature", "N/A"), "short": True},
                {"title": "Confidence",       "value": str(meta["confidence_score"]), "short": True},
                {"title": "Action",           "value": action,               "short": True},
                {"title": "Audit ID",         "value": meta.get("audit_id", "N/A"), "short": True},
            ],
            "footer": f"FinOps AI Engine | {meta['timestamp'][:10]}",
        }]
    }

    try:
        resp = requests.post(webhook_url, json=payload, timeout=5)
        ok   = resp.status_code == 200
        logger.info("[SLACK] Sent to %s | status=%d", webhook_url[:40], resp.status_code)
        return ok
    except Exception as e:
        logger.error("[SLACK] Failed: %s", e)
        return False


# =============================================================================
# PUBLIC ENTRY POINT
# =============================================================================

def run_action_pipeline(
    record: dict,
    rca: dict,
    slack_webhook: str = None,
    dry_run: bool = True,
) -> dict:
    """
    Entry point Stage 2-5.
    Nhan anomaly_record + RCA result tu ROOT_CAUSE.py.
    Tra ve output JSON day du.

    Parameters
    ----------
    record        : dict  - anomaly record tu XGBoost DETECT.py
    rca           : dict  - ket qua tu ROOT_CAUSE.analyse_root_cause()
    slack_webhook : str   - Slack webhook URL (None = bo qua notification)
    dry_run       : bool  - True khi test, False khi deploy that

    Returns
    -------
    dict: output JSON voi anomaly_metadata, finance_dashboard_data,
          engineering_dashboard_data
    """
    # Set global dry_run flag
    os.environ["FINOPS_DRY_RUN"] = "false" if not dry_run else "true"

    resource_id = record.get("resource_id", "unknown")
    env         = record.get("environment", "unknown")
    logger.info("[PIPELINE] START resource=%s env=%s dry_run=%s", resource_id, env, dry_run)

    # Stage 2: decide mitigation via Nova Lite
    mitigation = decide_mitigation(record, rca)
    logger.info("[PIPELINE] Stage2 done: action=%s", mitigation.get("immediate_action"))

    # Stage 3: execute
    execution = execute_action(record, mitigation, dry_run=dry_run)
    logger.info("[PIPELINE] Stage3 done: status=%s", execution.get("status"))

    # Stage 4: audit log
    audit_id = log_audit(record, rca, mitigation, execution)

    # Stage 5: build JSON + notify
    output = build_output_json(record, rca, mitigation, audit_id)
    if slack_webhook:
        send_slack_alert(slack_webhook, output)

    logger.info("[PIPELINE] COMPLETE audit_id=%s", audit_id)
    return output


# =============================================================================
# CLI TEST
# =============================================================================

if __name__ == "__main__":
    import json as _json

    # Enable mock mode: khong can Bedrock credentials hay DynamoDB
    os.environ["BEDROCK_MOCK"] = "true"

    test_cases = [
        {
            "name": "PROD - RDS idle, owner missing -> human-in-loop",
            "record": {
                "resource_id":               "arn:aws:rds:us-east-1:123:db:db-prod-api-01",
                "environment":               "prod",
                "confidence_score":          0.96,
                "line_item_product_code":    "AmazonRDS",
                "line_item_unblended_cost":  85.20,
                "cost_ratio_to_7d_avg":      14.2,
                "usage_density_24h":         1.0,
                "cpu_mean":                  1.5,
                "resource_tags_user_owner":  None,
                "resource_tags_user_team":   "platform",
                "absolute_cost_spike":       70.0,
            },
            "rca": {
                "root_cause_category":    "Mis-tagged Spend",
                "primary_driver_feature": "usage_density_24h",
                "finance_summary":        "RDS instance tieu ton .20/ngay nhung khong co owner tag. Vi pham Tag Policy.",
                "technical_reason":       "Instance chay 24/24, CPU 1.5%, khong co owner tag.",
                "missing_mandatory_tags": ["resource_tags_user_owner"],
                "risk_level":             "Critical",
            },
        },
        {
            "name": "STAGING - RDS orphan -> time-lock 4h",
            "record": {
                "resource_id":               "arn:aws:rds:us-east-1:200:db:db-staging-orphan-01",
                "environment":               "staging",
                "confidence_score":          0.94,
                "line_item_product_code":    "AmazonRDS",
                "line_item_unblended_cost":  27.84,
                "cost_ratio_to_7d_avg":      12.4,
                "usage_density_24h":         1.0,
                "cpu_mean":                  2.1,
                "resource_tags_user_owner":  None,
                "resource_tags_user_team":   "data-eng",
                "absolute_cost_spike":       15.6,
            },
            "rca": {
                "root_cause_category":    "Idle Resource",
                "primary_driver_feature": "usage_density_24h",
                "finance_summary":        "RDS staging tieu ton .84/ngay. Du bao /thang neu khong xu ly.",
                "technical_reason":       "RDS chay 100% nhung Active Connections ~ 0 trong 10 tuan.",
                "missing_mandatory_tags": ["resource_tags_user_owner"],
                "risk_level":             "High",
            },
        },
        {
            "name": "DEV - EC2 cost spike, high confidence -> auto-stop",
            "record": {
                "resource_id":               "i-0abc123dev456",
                "environment":               "dev",
                "confidence_score":          0.88,
                "line_item_product_code":    "AmazonEC2",
                "line_item_unblended_cost":  45.0,
                "cost_ratio_to_7d_avg":      18.5,
                "usage_density_24h":         0.6,
                "cpu_mean":                  72.0,
                "resource_tags_user_owner":  "alice@company.com",
                "resource_tags_user_team":   "backend",
                "absolute_cost_spike":       38.0,
            },
            "rca": {
                "root_cause_category":    "Cost Spike",
                "primary_driver_feature": "cost_ratio_to_7d_avg",
                "finance_summary":        "EC2 dev tang dot bien 18.5x, /ngay. Can dung ngay.",
                "technical_reason":       "Chi phi tang bat thuong, co the do runaway process.",
                "missing_mandatory_tags": [],
                "risk_level":             "High",
            },
        },
        {
            "name": "ML-RESEARCH - SageMaker GPU idle -> auto-stop",
            "record": {
                "resource_id":               "finops-gpu-notebook-p3",
                "environment":               "ml-research",
                "confidence_score":          0.91,
                "line_item_product_code":    "AmazonSageMaker",
                "line_item_unblended_cost":  120.0,
                "cost_ratio_to_7d_avg":      8.2,
                "usage_density_24h":         0.95,
                "cpu_mean":                  3.0,
                "resource_tags_user_owner":  None,
                "resource_tags_user_team":   "ml-team",
                "absolute_cost_spike":       90.0,
            },
            "rca": {
                "root_cause_category":    "Idle Resource",
                "primary_driver_feature": "cpu_mean",
                "finance_summary":        "GPU notebook /ngay nhung CPU chi 3%. Lang phi /thang.",
                "technical_reason":       "p3.2xlarge idle sau khi training job ket thuc, khong ai tat may.",
                "missing_mandatory_tags": ["resource_tags_user_owner"],
                "risk_level":             "Critical",
            },
        },
        {
            "name": "DATA-ANALYTICS - Quota cap to stop runaway query",
            "record": {
                "resource_id":               "arn:aws:glue:us-east-1:300:job:etl-daily-report",
                "environment":               "data-analytics",
                "confidence_score":          0.85,
                "line_item_product_code":    "AWSGlue",
                "line_item_unblended_cost":  320.0,
                "cost_ratio_to_7d_avg":      25.0,
                "usage_density_24h":         1.0,
                "cpu_mean":                  95.0,
                "resource_tags_user_owner":  "bob@company.com",
                "resource_tags_user_team":   "data-platform",
                "absolute_cost_spike":       300.0,
            },
            "rca": {
                "root_cause_category":    "Runaway Job",
                "primary_driver_feature": "cost_ratio_to_7d_avg",
                "finance_summary":        "Glue ETL job vong lap loi, /ngay. Du bao /thang neu khong chan.",
                "technical_reason":       "ETL job bi loop do loi partition logic, chay lien tuc tao chi phi cao.",
                "missing_mandatory_tags": [],
                "risk_level":             "Critical",
            },
        },
    ]

    # Test toan bo 5 moi truong voi dry_run=True (mac dinh khi test)
    for tc in test_cases:
        print(f"\n{'='*65}")
        print(f"TEST: {tc['name']}")
        print(f"{'='*65}")
        result = run_action_pipeline(
            record        = tc["record"],
            rca           = tc["rca"],
            slack_webhook = None,   # Set Slack URL that nau muon test notification
            dry_run       = True,   # DRY RUN: chi in lenh, khong thuc thi that
        )
        print(_json.dumps(result, indent=2, ensure_ascii=False))