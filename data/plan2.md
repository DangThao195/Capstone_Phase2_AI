# Plan 2: LLM-Powered RCA & Mitigation Engine (Amazon Nova via Bedrock)

## Tong quan

Pipeline nay la tang thu 2 chay SAU khi XGBoost (plan.md) da detect duoc anomaly.
XGBoost chi biet "day la anomaly" — Nova biet "tai sao" va "phai lam gi".

```
XGBoost (plan.md)
  |  output: anomaly_record (resource_id, env, cost, features, confidence)
  v
[Stage 1] Nova RCA Engine     -> root_cause (Finance-friendly text)
  |
  v
[Stage 2] Nova Mitigation     -> action payload (CLI / tag / quota)
  |
  v
[Stage 3] Action Executor     -> dry_run | live (phu thuoc env + DRY_RUN flag)
  |
  v
[Stage 4] Audit Logger        -> DynamoDB Audit Store (retention >= 90 ngay)
  |
  v
[Stage 5] Notification        -> Finance Dashboard JSON + Slack Alert
```

---

## Input tu XGBoost Pipeline

Moi anomaly record truyen vao RCA Engine co dang:

```python
anomaly_record = {
    "resource_id":               "arn:aws:rds:us-east-1:...:db-staging-01",
    "environment":               "staging",           # prod|staging|dev|ml-research|data-analytics
    "confidence_score":          0.94,                # tu XGBoost predict_proba
    "line_item_product_code":    "AmazonRDS",
    "line_item_unblended_cost":  27.84,
    "cost_ratio_to_7d_avg":      12.4,
    "usage_density_24h":         1.0,                 # = usage_amount / 24
    "cpu_mean":                  2.1,
    "resource_tags_user_owner":  None,                # NaN -> Mis-tagged Spend
    "resource_tags_user_team":   "data-eng",
    "resource_tags_user_environment": "staging",
    "rolling_7d_avg":            2.24,
    "absolute_cost_spike":       15.6,
}
```

---

## Stage 1 — LLM Root Cause Analysis (Amazon Nova Pro)

### Muc tieu

Giai thich anomaly bang ngon ngu tai chinh (Finance-friendly).
Che giau thuat ngu toan hoc (robust_z, rolling_7d_avg...).
Tu dong ket luan Mis-tagged Spend neu owner = NaN.

### Bedrock API Call

```python
import boto3, json

bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")

def call_nova_rca(record: dict) -> dict:
    owner_missing = record.get("resource_tags_user_owner") is None

    system_prompt = """Ban la chuyen gia FinOps cap cao.
Nhiem vu: Phan tich du lieu chi phi AWS va dua ra nguyen nhan goc re (Root Cause)
bang ngon ngu tai chinh ro rang, tranh thuat ngu ky thuat.
Neu owner = null, BAT BUOC ket luan: Mis-tagged Spend (vi pham luat Tag doanh nghiep).
Tra ve JSON theo dung schema da cho."""

    user_prompt = f"""
Du lieu anomaly:
- Resource: {record['resource_id']}
- Service: {record['line_item_product_code']}
- Moi truong: {record['environment']}
- Chi phi 24h: ${record['line_item_unblended_cost']:.2f}
- Ti le so voi baseline 7 ngay: {record['cost_ratio_to_7d_avg']:.1f}x
- Usage density 24h: {record['usage_density_24h']:.2f} (1.0 = chay 100% 24/24)
- CPU trung binh: {record['cpu_mean']:.1f}%
- Owner tag: {"MISSING - vi pham tag policy" if owner_missing else record['resource_tags_user_owner']}
- Team: {record.get('resource_tags_user_team', 'unknown')}

Hay phan tich va tra ve JSON:
{{
  "primary_driver_feature": "<ten feature chinh gay ra anomaly>",
  "root_cause_category": "<Idle Resource | Mis-tagged Spend | Cost Spike | Runaway Job | Other>",
  "finance_summary": "<1-2 cau tom tat cho CFO/Finance team>",
  "technical_reason": "<giai thich ky thuat cho engineering team>",
  "missing_mandatory_tags": ["<list cac tag bi thieu>"],
  "risk_level": "<Low|Medium|High|Critical>"
}}
"""

    response = bedrock.invoke_model(
        modelId="amazon.nova-pro-v1:0",
        body=json.dumps({
            "system":   [{"text": system_prompt}],
            "messages": [{"role": "user", "content": [{"text": user_prompt}]}],
            "inferenceConfig": {"maxTokens": 512, "temperature": 0.1}
        }),
        contentType="application/json",
        accept="application/json"
    )

    body   = json.loads(response["body"].read())
    text   = body["output"]["message"]["content"][0]["text"]
    # Extract JSON from response
    import re
    match  = re.search(r'\{.*\}', text, re.DOTALL)
    return json.loads(match.group()) if match else {"error": text}
```

### Logic Mis-tagged Spend

```python
def enrich_rca_with_tag_check(record: dict, rca: dict) -> dict:
    if record.get("resource_tags_user_owner") is None:
        rca["root_cause_category"]    = "Mis-tagged Spend"
        rca["missing_mandatory_tags"] = rca.get("missing_mandatory_tags", [])
        if "resource_tags_user_owner" not in rca["missing_mandatory_tags"]:
            rca["missing_mandatory_tags"].append("resource_tags_user_owner")
    return rca
```

**Output Stage 1:**

```json
{
  "primary_driver_feature": "usage_density_24h",
  "root_cause_category": "Idle Resource",
  "finance_summary": "Instance RDS db.r5.2xlarge tieu ton $27.84/ngay trong khi khong co ket noi hoat dong. Du bao lang phi $835/thang.",
  "technical_reason": "RDS instance chay lien tuc 24/24 (usage_density=1.0) nhung Active Connections ~ 0 trong 10 tuan. Bi bo hoang sau dot kiem thu.",
  "missing_mandatory_tags": ["resource_tags_user_owner"],
  "risk_level": "High"
}
```

---

## Stage 2 — LLM Mitigation Action Engine (Amazon Nova Lite)

### Muc tieu

Doc `environment` + `rca` + `confidence_score` -> chon dung action theo ma tran 5 moi truong.
Dung Nova Lite (nhanh hon, re hon) vi logic nay co cau truc ro rang.

### Ma tran quyet dinh

| Environment     | Confidence  | Action                  | Executor Mode |
|-----------------|-------------|-------------------------|---------------|
| prod            | bat ky      | tag-for-review + Slack  | human-in-loop |
| staging         | bat ky      | tag + time-lock 4h      | semi-auto     |
| dev / sandbox   | >= 0.80     | stop instance           | dry_run / live|
| ml-research     | >= 0.80     | stop notebook/instance  | dry_run / live|
| data-analytics  | bat ky      | quota-cap               | dry_run / live|

### Bedrock API Call

```python
def call_nova_mitigation(record: dict, rca: dict) -> dict:
    env        = record["environment"]
    confidence = record["confidence_score"]
    service    = record["line_item_product_code"]
    resource   = record["resource_id"]

    system_prompt = """Ban la FinOps Automation Engineer.
Nhiem vu: Dua ra hanh dong xu ly chinh xac dua tren moi truong AWS va ket qua RCA.
Tuan thu nghiem ngat ma tran an toan: prod chi duoc tag, khong bao gio tu dong tat may.
Tra ve JSON theo dung schema."""

    user_prompt = f"""
Thong tin:
- Resource ID: {resource}
- Service: {service}
- Moi truong: {env}
- Confidence score: {confidence}
- Root cause: {rca['root_cause_category']}
- Risk level: {rca['risk_level']}

Ma tran hanh dong:
- prod: ONLY tag-for-review + slack escalation. TUYET DOI khong stop.
- staging: tag-for-review + time-lock 4h (14400s) + fallback stop.
- dev/sandbox: neu confidence >= 0.80 -> stop instance. Neu < 0.80 -> tag only.
- ml-research: neu confidence >= 0.80 -> stop sagemaker/ec2. Neu < 0.80 -> tag only.
- data-analytics: quota-cap thong qua Service Quotas API. Khong stop.

Tra ve JSON:
{{
  "strategy": "<ten chien luoc>",
  "immediate_action": "<tag-for-review|stop-instance|stop-notebook|quota-cap>",
  "cli_commands": ["<aws cli command 1>", "<aws cli command 2>"],
  "rollback_command": "<aws cli command de khoi phuc neu can>",
  "slack_message": "<noi dung thong bao Slack>",
  "enforcement_countdown": {{
    "enabled": <true|false>,
    "time_lock_seconds": <0|14400>,
    "fallback_action": "<none|schedule-shutdown>"
  }},
  "requires_human_approval": <true|false>
}}
"""

    response = bedrock.invoke_model(
        modelId="amazon.nova-lite-v1:0",
        body=json.dumps({
            "system":   [{"text": system_prompt}],
            "messages": [{"role": "user", "content": [{"text": user_prompt}]}],
            "inferenceConfig": {"maxTokens": 512, "temperature": 0.0}
        }),
        contentType="application/json",
        accept="application/json"
    )

    body  = json.loads(response["body"].read())
    text  = body["output"]["message"]["content"][0]["text"]
    import re
    match = re.search(r'\{.*\}', text, re.DOTALL)
    return json.loads(match.group()) if match else {"error": text}
```

---

## Stage 3 — Action Executor (Dry Run vs Live)

### Nguyen tac DRY_RUN

```
DRY_RUN = True  (mac dinh khi test)
  -> In ra lenh CLI se chay, KHONG thuc thi that
  -> Log ket qua vao DynamoDB voi status = "dry_run"

DRY_RUN = False (chi bat tren production system sau khi da validated)
  -> Thuc thi AWS API that
  -> Log vao DynamoDB voi status = "executed"
```

**Bat buoc DRY_RUN = True voi moi truong: dev, ml-research, data-analytics khi dang test.**

### Code Executor

```python
import subprocess, os

DRY_RUN = os.environ.get("FINOPS_DRY_RUN", "true").lower() == "true"

SAFE_ENVS_REQUIRE_DRY_RUN = {"dev", "sandbox", "ml-research", "data-analytics"}

def execute_action(record: dict, mitigation: dict) -> dict:
    env      = record["environment"]
    commands = mitigation.get("cli_commands", [])
    results  = []

    # Force dry_run cho cac moi truong co rui ro thap khi dang test
    is_dry = DRY_RUN or (env in SAFE_ENVS_REQUIRE_DRY_RUN and DRY_RUN)

    # prod: LUON require human approval, khong thuc thi tu dong
    if env == "prod":
        return {
            "status":  "pending_human_approval",
            "message": "prod environment - waiting for SRE engineer action",
            "commands_queued": commands
        }

    # staging: tag ngay, schedule enforcement sau 4h
    if env == "staging":
        tag_cmd = commands[0] if commands else ""
        result  = _run_or_dry(tag_cmd, is_dry)
        results.append(result)
        if not is_dry:
            _schedule_enforcement(record, mitigation)
        return {"status": "staged", "tag_result": result, "countdown": 14400}

    # dev / ml-research / data-analytics
    if record["confidence_score"] >= 0.80 or env == "data-analytics":
        for cmd in commands:
            results.append(_run_or_dry(cmd, is_dry))
        return {
            "status":   "dry_run_complete" if is_dry else "executed",
            "commands": results
        }

    # confidence < 0.80 -> chi tag
    tag_cmd = next((c for c in commands if "create-tags" in c), "")
    return {"status": "tag_only", "result": _run_or_dry(tag_cmd, is_dry)}


def _run_or_dry(command: str, dry_run: bool) -> dict:
    if dry_run:
        print(f"[DRY RUN] Would execute: {command}")
        return {"dry_run": True, "command": command, "output": "simulated"}
    try:
        out = subprocess.run(command.split(), capture_output=True, text=True, timeout=30)
        return {"dry_run": False, "command": command,
                "stdout": out.stdout, "returncode": out.returncode}
    except Exception as e:
        return {"dry_run": False, "command": command, "error": str(e)}


def _schedule_enforcement(record: dict, mitigation: dict):
    """Dat timer 4h cho staging. Trong thuc te dung Lambda scheduled event."""
    import threading
    def enforce():
        import time; time.sleep(14400)
        fallback = mitigation.get("enforcement_countdown", {}).get("fallback_action")
        if fallback == "schedule-shutdown":
            stop_cmd = f"aws rds stop-db-instance --db-instance-identifier {record['resource_id']}"
            _run_or_dry(stop_cmd, dry_run=False)
    threading.Thread(target=enforce, daemon=True).start()
```

---

## Stage 4 — Audit Logger (DynamoDB)

Moi action (du dry_run hay live) deu phai ghi vao DynamoDB Audit Store.
Retention >= 90 ngay. Rollback command duoc luu de engineer co the hoan tac.

```python
import boto3, uuid
from datetime import datetime, timezone

dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
TABLE    = dynamodb.Table("FinOps_Audit_Store")

def log_audit(record: dict, rca: dict, mitigation: dict, execution: dict):
    TABLE.put_item(Item={
        "audit_id":         str(uuid.uuid4()),
        "anomaly_id":       f"ANM-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        "timestamp":        datetime.now(timezone.utc).isoformat(),
        "resource_id":      record["resource_id"],
        "environment":      record["environment"],
        "confidence_score": str(record["confidence_score"]),
        "root_cause":       rca.get("root_cause_category", "unknown"),
        "risk_level":       rca.get("risk_level", "unknown"),
        "action_taken":     mitigation.get("immediate_action", "none"),
        "cli_commands":     str(mitigation.get("cli_commands", [])),
        "rollback_command": mitigation.get("rollback_command", ""),
        "execution_status": execution.get("status", "unknown"),
        "dry_run":          str(execution.get("status", "").startswith("dry")),
        "ttl":              int((datetime.now(timezone.utc).timestamp()) + 90 * 86400)
    })
```

**DynamoDB Table Schema:**

```
Table name : FinOps_Audit_Store
Partition  : audit_id (String)
Sort key   : timestamp (String)
TTL attr   : ttl (Number, Unix epoch, auto-delete after 90 days)
GSI        : resource_id-index  (de query theo resource)
GSI        : environment-index  (de query theo moi truong)
```

---

## Stage 5 — Output JSON & Notification

### JSON Output (theo spec Muc 5)

```python
def build_output_json(record: dict, rca: dict, mitigation: dict) -> dict:
    from datetime import datetime, timezone
    return {
        "anomaly_metadata": {
            "anomaly_id":       f"ANM-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            "timestamp":        datetime.now(timezone.utc).isoformat(),
            "resource_id":      record["resource_id"],
            "environment":      record["environment"],
            "confidence_score": record["confidence_score"],
            "ai_model_used":    "amazon.nova-pro-v1:0"
        },
        "finance_dashboard_data": {
            "target_recipient": "Finance Team & CFO Dashboard",
            "metrics": {
                "unblended_cost_24h_usd":       record["line_item_unblended_cost"],
                "cost_ratio_to_7d_avg":          record["cost_ratio_to_7d_avg"],
                "projected_monthly_waste_usd":   round(record["line_item_unblended_cost"] * 30, 2)
            },
            "allocation": {
                "responsible_team": record.get("resource_tags_user_team", "unknown"),
                "cost_center_code": record.get("cost_center_code", "CC-UNKNOWN")
            },
            "executive_summary": rca.get("finance_summary", "")
        },
        "engineering_dashboard_data": {
            "target_recipient": "Engineering Console & Slack Alert",
            "technical_context": {
                "aws_service":        record["line_item_product_code"],
                "usage_density_24h":  record["usage_density_24h"],
                "cpu_mean":           record["cpu_mean"]
            },
            "root_cause_analysis": {
                "primary_driver_feature": rca.get("primary_driver_feature"),
                "technical_reason":       rca.get("technical_reason"),
                "missing_mandatory_tags": rca.get("missing_mandatory_tags", [])
            },
            "mitigation_action": {
                "strategy":         mitigation.get("strategy"),
                "immediate_action": mitigation.get("immediate_action"),
                "applied_payload": {
                    "action_type": "inject_aws_tag",
                    "cli_commands": mitigation.get("cli_commands", [])
                },
                "enforcement_countdown": mitigation.get("enforcement_countdown", {})
            }
        }
    }
```

### Slack Notification

```python
import requests

def send_slack_alert(webhook_url: str, output_json: dict):
    env       = output_json["anomaly_metadata"]["environment"]
    resource  = output_json["anomaly_metadata"]["resource_id"]
    cost      = output_json["finance_dashboard_data"]["metrics"]["unblended_cost_24h_usd"]
    summary   = output_json["finance_dashboard_data"]["executive_summary"]
    action    = output_json["engineering_dashboard_data"]["mitigation_action"]["immediate_action"]
    color_map = {"prod": "#FF0000", "staging": "#FF9800", "dev": "#2196F3",
                 "ml-research": "#9C27B0", "data-analytics": "#4CAF50"}

    payload = {
        "attachments": [{
            "color": color_map.get(env, "#607D8B"),
            "title": f"FinOps Alert: {env.upper()} | {action}",
            "text":  summary,
            "fields": [
                {"title": "Resource",   "value": resource, "short": False},
                {"title": "Cost 24h",   "value": f"${cost:.2f}", "short": True},
                {"title": "Action",     "value": action,   "short": True},
            ]
        }]
    }
    requests.post(webhook_url, json=payload, timeout=5)
```

---

## Tong hop luong chay chinh

```python
def run_rca_pipeline(anomaly_record: dict, slack_webhook: str = None,
                     dry_run: bool = True) -> dict:
    """
    Entry point: nhan 1 anomaly record tu XGBoost, tra ve output JSON day du.

    Parameters
    ----------
    anomaly_record : dict  - output tu XGBoost detection pipeline
    slack_webhook  : str   - Slack webhook URL (None = skip notification)
    dry_run        : bool  - True khi test, False khi deploy that
    """
    import os
    os.environ["FINOPS_DRY_RUN"] = "true" if dry_run else "false"

    # Stage 1: RCA
    rca = call_nova_rca(anomaly_record)
    rca = enrich_rca_with_tag_check(anomaly_record, rca)

    # Stage 2: Mitigation
    mitigation = call_nova_mitigation(anomaly_record, rca)

    # Stage 3: Execute (dry_run controlled by env var)
    execution = execute_action(anomaly_record, mitigation)

    # Stage 4: Audit log
    log_audit(anomaly_record, rca, mitigation, execution)

    # Stage 5: Build output + notify
    output = build_output_json(anomaly_record, rca, mitigation)
    if slack_webhook:
        send_slack_alert(slack_webhook, output)

    return output
```

---

## Ket noi voi DETECT.py

```python
# Sau khi DETECT.py tra ve evaluation_df co cot Prediction=1:
anomalies = evaluation_df[evaluation_df["Prediction"] == 1]

for _, row in anomalies.iterrows():
    record = {
        "resource_id":               row.get("line_item_resource_id", "unknown"),
        "environment":               row.get("resource_tags_user_environment", "dev"),
        "confidence_score":          float(row["Probability"]),
        "line_item_product_code":    row.get("line_item_product_code", "unknown"),
        "line_item_unblended_cost":  float(row.get("line_item_unblended_cost", 0)),
        "cost_ratio_to_7d_avg":      float(row.get("cost_ratio_to_7d_avg", 1.0)),
        "usage_density_24h":         float(row.get("usage_density", 0)),
        "cpu_mean":                  float(row.get("cpu_mean", 0)),
        "resource_tags_user_owner":  row.get("owner_missing", 1) == 1 and None or "present",
        "resource_tags_user_team":   row.get("resource_tags_user_team", "unknown"),
    }
    output = run_rca_pipeline(record, dry_run=True)   # DRY RUN khi test
    print(json.dumps(output, indent=2, ensure_ascii=False))
```

---

## Tom tat cac file can tao

| File               | Vai tro                                              |
|--------------------|------------------------------------------------------|
| `FEATURE.py`       | Load, merge, feature engineering                     |
| `DETECT.py`        | XGBoost train + evaluate, tra ra anomaly records     |
| `DRIFT_PLAN.py`    | SHAP + drift detection + retrain decision            |
| `ANALYSIS.py`      | Phan tich sau 7 huong (fold2, drift, calibration...) |
| `RCA_ENGINE.py`    | Stage 1+2: Goi Bedrock Nova RCA + Mitigation         |
| `ACTION_EXECUTOR.py` | Stage 3: Thuc thi CLI (dry_run / live)             |
| `AUDIT_LOGGER.py`  | Stage 4: Ghi DynamoDB Audit Store                    |
| `NOTIFIER.py`      | Stage 5: Build JSON output + Slack                   |

---

*Ref: data/plan2.md - AWS FinOps RCA & Mitigation Engine v1*
