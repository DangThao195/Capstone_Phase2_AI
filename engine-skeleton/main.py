import os
import sys
import uuid
import time
import json
import sqlite3
import hashlib
from datetime import datetime, timezone
from fastapi import FastAPI, Request, Response, Header, HTTPException, BackgroundTasks, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import List, Optional

# Add data folder to path for importing AnomalyDetectorService
sys.path.append(r"d:\Xbrain\Capstone-AIOps-02\data")
from detector_service import AnomalyDetectorService

app = FastAPI(title="TF2 FinOps Watch AI Engine", version="1.4.0")

# --- Configuration & State DB Setup ---
DB_DIR = r"C:\Users\ACER\.gemini\antigravity\brain\f3326b07-07fe-4c67-b5b1-607981769f04\scratch"
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "state_store.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Idempotency table (PK: idempotency_key)
    c.execute("""
        CREATE TABLE IF NOT EXISTS idempotency (
            idempotency_key TEXT PRIMARY KEY,
            status TEXT,
            response_body TEXT,
            payload_sha256 TEXT,
            ttl_expiry INTEGER
        )
    """)
    # Rollback Cache (PK: audit_id)
    c.execute("""
        CREATE TABLE IF NOT EXISTS rollback_cache (
            audit_id TEXT PRIMARY KEY,
            resource_id TEXT,
            action_type TEXT,
            aws_cli_rollback_command TEXT,
            boto3_equivalent TEXT,
            status TEXT,
            requested_by_user TEXT,
            justification_on_rollback TEXT,
            created_at TEXT
        )
    """)
    # Remediation Status tracking
    c.execute("""
        CREATE TABLE IF NOT EXISTS remediation_status (
            audit_id TEXT PRIMARY KEY,
            resource_id TEXT,
            status TEXT,
            error_message TEXT,
            message TEXT,
            created_at TEXT
        )
    """)
    # Error Budget / Containment locks
    c.execute("""
        CREATE TABLE IF NOT EXISTS error_budget (
            env TEXT PRIMARY KEY,
            rollback_count INTEGER,
            total_actions INTEGER,
            is_locked INTEGER
        )
    """)
    # Seed default error budgets
    c.execute("INSERT OR IGNORE INTO error_budget VALUES ('prod', 0, 100, 0)")
    c.execute("INSERT OR IGNORE INTO error_budget VALUES ('staging', 0, 100, 0)")
    
    conn.commit()
    conn.close()

init_db()

# Initialize anomaly detector
detector = AnomalyDetectorService()

# --- Middleware / Helper validation checks ---
def get_db_connection():
    return sqlite3.connect(DB_PATH)

def check_idempotency(key: str, body_bytes: bytes):
    payload_hash = hashlib.sha256(body_bytes).hexdigest()
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT status, response_body, payload_sha256, ttl_expiry FROM idempotency WHERE idempotency_key = ?", (key,))
    row = c.fetchone()
    
    if row:
        status_val, resp_body, cached_hash, ttl = row
        current_time = int(time.time())
        # Check TTL expiry
        if current_time > ttl:
            c.execute("DELETE FROM idempotency WHERE idempotency_key = ?", (key,))
            conn.commit()
            conn.close()
            return "NEW", None
            
        if status_val == "IN_PROGRESS":
            conn.close()
            return "IN_PROGRESS", None
        elif status_val == "COMPLETED":
            conn.close()
            if cached_hash == payload_hash:
                return "COMPLETED", json.loads(resp_body)
            else:
                return "MISMATCH", None
    
    # Register in-progress
    ttl_expiry = int(time.time()) + 86400 # 24 hours TTL
    c.execute("INSERT INTO idempotency (idempotency_key, status, payload_sha256, ttl_expiry) VALUES (?, 'IN_PROGRESS', ?, ?)", (key, payload_hash, ttl_expiry))
    conn.commit()
    conn.close()
    return "NEW", None

def update_idempotency_complete(key: str, response_dict: dict):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE idempotency SET status = 'COMPLETED', response_body = ? WHERE idempotency_key = ?", (json.dumps(response_dict), key))
    conn.commit()
    conn.close()

def delete_idempotency_key(key: str):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM idempotency WHERE idempotency_key = ?", (key,))
    conn.commit()
    conn.close()

def is_env_locked(env: str) -> bool:
    if env not in ['prod', 'staging']:
        return False
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT is_locked FROM error_budget WHERE env = ?", (env,))
    row = c.fetchone()
    conn.close()
    return bool(row[0]) if row else False

def update_error_budget(env: str, is_rollback: bool):
    if env not in ['prod', 'staging']:
        return
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT rollback_count, total_actions FROM error_budget WHERE env = ?", (env,))
    row = c.fetchone()
    if row:
        r_cnt, t_act = row
        t_act += 1
        if is_rollback:
            r_cnt += 1
        
        # Calculate rate
        rate = r_cnt / max(t_act, 1)
        limit = 0.01 if env == 'prod' else 0.10
        is_locked = 1 if rate > limit else 0
        
        c.execute("UPDATE error_budget SET rollback_count = ?, total_actions = ?, is_locked = ? WHERE env = ?", (r_cnt, t_act, is_locked, env))
        conn.commit()
    conn.close()


# --- Endpoint Models ---
class BusinessContext(BaseModel):
    linked_account_id: int
    traffic_volume: float
    traffic_source: str
    campaign_flag: Optional[bool] = False
    load_test_flag: Optional[bool] = False
    migration_flag: Optional[bool] = False

class DetectRequest(BaseModel):
    data_source_type: str = Field(..., description="RAW_JSON or S3_POINTER")
    is_ad_hoc: Optional[bool] = False
    telemetry_delay_event: Optional[bool] = False
    missing_resources: Optional[List[str]] = []
    current_ce_cost_gap_usd: Optional[float] = 0.0
    comparison_window: Optional[dict] = None
    aws_cost_explorer_daily: Optional[List[dict]] = []
    aws_cur_line_items: Optional[List[dict]] = []
    s3_bucket_uri: Optional[str] = None
    s3_object_checksum: Optional[str] = None
    business_context: Optional[List[BusinessContext]] = []

class DecideRequest(BaseModel):
    audit_id: str
    anomaly_id: str
    resource_id: str
    environment: str
    product_code: str
    unblended_cost_24h: float
    missing_tags: List[str]

class VerifyRequest(BaseModel):
    audit_id: str
    verification_mode: str = Field(..., description="DRY_RUN or LIVE")

class RollbackRequest(BaseModel):
    requested_by_user: str
    justification_on_rollback: str


# --- FastAPI Routing ---

# 5.4 GET /health — Health Check
@app.get("/health", status_code=200)
def health_check():
    # Verify DB connectivity
    db_ok = "connected"
    try:
        conn = get_db_connection()
        conn.execute("SELECT 1")
        conn.close()
    except Exception:
        db_ok = "disconnected"
        
    return {
        "status": "healthy" if db_ok == "connected" else "degraded",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "services": {
            "dynamodb": db_ok,
            "bedrock_api": "accessible"
        }
    }

# 5.1 POST /v1/detect — Anomaly Detection (Synchronous)
@app.post("/v1/detect", status_code=200)
async def post_detect(
    request: Request,
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
    x_idempotency_key: str = Header(..., alias="X-Idempotency-Key"),
    x_request_timestamp: str = Header(..., alias="X-Request-Timestamp"),
    x_payload_sha256: str = Header(..., alias="X-Payload-SHA256")
):
    body_bytes = await request.body()
    
    # 1. Clock Skew Check (Request Timestamp skew <= 300 seconds)
    try:
        req_dt = datetime.fromisoformat(x_request_timestamp.replace("Z", "+00:00"))
        now_dt = datetime.now(timezone.utc)
        skew = abs((now_dt - req_dt).total_seconds())
        if skew > 300:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"internal_code": "ERR_CLOCK_SKEW", "message": "Request timestamp skew exceeds 300 seconds."}
            )
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"internal_code": "ERR_INVALID_HEADERS", "message": f"Invalid X-Request-Timestamp format: {e}"}
        )

    # Parse request JSON body
    try:
        body_str = body_bytes.decode('utf-8')
        req_data = DetectRequest.parse_raw(body_str)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"internal_code": "ERR_INVALID_SCHEMA", "message": f"Request body schema validation failed: {e}"}
        )

    # 2. Idempotency Key Evaluation (DynamoDB conditional write emulation)
    if not req_data.is_ad_hoc:
        idem_status, cached_response = check_idempotency(x_idempotency_key, body_bytes)
        if idem_status == "IN_PROGRESS":
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                headers={"Retry-After": "30"},
                content={"internal_code": "ERR_DUP_IDEMPOTENCY", "message": "Idempotent request is already in progress. Please poll status or retry."}
            )
        elif idem_status == "MISMATCH":
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"internal_code": "ERR_IDEMPOTENCY_MISMATCH", "message": "The idempotency key is already registered with a different payload."}
            )
        elif idem_status == "COMPLETED":
            return cached_response
    else:
        print(f"Skipping idempotency checks because is_ad_hoc = True")

    # 3. Load input data (Supports hybrid raw JSON vs S3 Pointer redirection)
    cur_items = []
    if req_data.data_source_type == "RAW_JSON":
        cur_items = req_data.aws_cur_line_items or []
    elif req_data.data_source_type == "S3_POINTER":
        # Emulates fetching CUR logs compressed from the S3 bucket
        # In this prototype, we fallback to loading from the local cur_line_items.csv matching dates
        print(f"S3 Pointer mode. Fetching logs from S3 bucket: {req_data.s3_bucket_uri}")
        # Validate bucket naming convention
        import re
        bucket_pattern = r"^s3://company-cdo-[0-9]{12}-telemetry/.+\.json\.gz$"
        if not re.match(bucket_pattern, req_data.s3_bucket_uri):
            if not req_data.is_ad_hoc:
                delete_idempotency_key(x_idempotency_key)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"internal_code": "ERR_INVALID_S3_URI", "message": "S3 URI must follow the globally unique convention: s3://company-cdo-{account_id}-telemetry/"}
            )
            
        cur_file = r"d:\Xbrain\Capstone-AIOps-02\data\cur_line_items.csv"
        df_all = pd.read_csv(cur_file)
        cur_items = df_all.to_dict('records')

    # 4. Perform anomaly scoring (Sync scoring on XGBoost < 300ms)
    business_contexts_dict = [c.dict() for c in req_data.business_context] if req_data.business_context else []
    
    anomalies_detected = detector.detect_anomalies(
        cur_items,
        business_contexts=business_contexts_dict,
        telemetry_delay_event=req_data.telemetry_delay_event,
        current_ce_cost_gap_usd=req_data.current_ce_cost_gap_usd
    )

    # 5. Format response list
    anomalies_list = []
    # Generation prefix to assign unique anomaly IDs matching contract schema ANM-YYYY-MMDD[A-Z]
    batch_date_str = datetime.now(timezone.utc).strftime("%Y-%m%d")
    
    for idx, anom in enumerate(anomalies_detected):
        anom_char = chr(65 + (idx % 26)) # A, B, C...
        anomaly_id = f"ANM-{batch_date_str}{anom_char}"
        
        # Save placeholder in remediation database table
        conn = get_db_connection()
        conn.execute("INSERT OR REPLACE INTO remediation_status VALUES (?, ?, 'pending', '', 'Anomaly registered, waiting for /v1/decide', ?)", (anomaly_id, anom['resource_id'], datetime.now(timezone.utc).isoformat()))
        conn.commit()
        conn.close()

        anomalies_list.append({
            "anomaly_id": anomaly_id,
            "resource_id": anom['resource_id'],
            "environment": anom['account_name'], # Account name acts as environment tag
            "product_code": anom['product_code'],
            "unblended_cost_24h": anom['unblended_cost_24h'],
            "confidence_score": anom['confidence_score'],
            "missing_tags": anom['missing_tags']
        })

    response_payload = {
        "status": "completed",
        "anomalies_detected": len(anomalies_list) > 0,
        "total_anomalies_found": len(anomalies_list),
        "anomalies_list": anomalies_list
    }

    if not req_data.is_ad_hoc:
        update_idempotency_complete(x_idempotency_key, response_payload)

    return response_payload


# 5.2 POST /v1/decide — Root Cause Analysis & Plan Generation
@app.post("/v1/decide", status_code=200)
def post_decide(
    req: DecideRequest,
    x_tenant_id: str = Header(..., alias="X-Tenant-Id")
):
    audit_id = req.audit_id
    res_id = req.resource_id
    env = req.environment
    prod_code = req.product_code
    cost = req.unblended_cost_24h
    missing_tags = req.missing_tags
    
    # 1. Check Error Budget Locks (prod 1% rate, staging 10% rate)
    is_locked = is_env_locked(env)
    
    # 2. Simulated Bedrock GenAI (Amazon Nova Stage 1 & 2 RCA Engine)
    # Depending on environment we enforce different strategies
    # prod: tag-for-review
    # staging: time-gated count down (4 hours)
    # dev/sandbox: immediate stop-instance
    # ml-research: stop GPU notebook
    # data-analytics: service quota limit
    
    strategy = "Auto-Containment"
    immediate_action = "auto-shutdown"
    action_type = "stop_instance"
    cli_command = f"aws ec2 stop-instances --instance-ids {res_id}"
    rollback_type = "start_instance"
    cli_rollback = f"aws ec2 start-instances --instance-ids {res_id}"
    boto3_equiv = f"ec2.start_instances(InstanceIds=['{res_id}'])"
    
    time_lock = 0
    fallback_action = ""
    
    # Override based on Environment Rules
    if env in ['prod', 'prod-core', 'prod-payments']:
        strategy = "Alert-only (Prod Boundaries)"
        immediate_action = "tag-for-review"
        action_type = "inject_aws_tag"
        cli_command = f"aws ec2 create-tags --resources {res_id} --tags Key=FinOps_Alert,Value=Review_Required"
        rollback_type = "remove_aws_tag"
        cli_rollback = f"aws ec2 delete-tags --resources {res_id} --tags Key=FinOps_Alert"
        boto3_equiv = f"ec2.delete_tags(Resources=['{res_id}'], Tags=[{{'Key': 'FinOps_Alert'}}])"
        
    elif env == 'staging':
        strategy = "Time-gated Containment (Staging Rules)"
        immediate_action = "tag-for-review"
        action_type = "inject_aws_tag"
        cli_command = f"aws rds add-tags-to-resource --resource-name {res_id} --tags Key=FinOps_Alert,Value=Staging_Review_Required"
        time_lock = 14400 # 4 hours
        fallback_action = "schedule-shutdown"
        rollback_type = "remove_aws_tag"
        cli_rollback = f"aws rds remove-tags-from-resource --resource-name {res_id} --tag-keys FinOps_Alert"
        boto3_equiv = f"rds.remove_tags_from_resource(ResourceName='{res_id}', TagKeys=['FinOps_Alert'])"
        
    elif env == 'ml-research':
        strategy = "Auto-Containment GPU (ml-research Rules)"
        immediate_action = "auto-shutdown"
        action_type = "stop_sagemaker_notebook"
        cli_command = f"aws sagemaker stop-notebook-instance --notebook-instance-name {res_id}"
        rollback_type = "start_sagemaker_notebook"
        cli_rollback = f"aws sagemaker start-notebook-instance --notebook-instance-name {res_id}"
        boto3_equiv = f"sagemaker.start_notebook_instance(NotebookInstanceName='{res_id}')"
        
    elif env == 'data-analytics':
        strategy = "Quota-Cap (data-analytics Rules)"
        immediate_action = "quota-cap"
        action_type = "restrict_quota"
        cli_command = f"aws service-quotas request-service-quota-increase --service-code {prod_code} --quota-code L-XXXX --desired-value 1.0"
        rollback_type = "restore_quota"
        cli_rollback = f"aws service-quotas request-service-quota-increase --service-code {prod_code} --quota-code L-XXXX --desired-value 10.0"
        boto3_equiv = f"servicequotas.request_service_quota_increase(ServiceCode='{prod_code}', QuotaCode='L-XXXX', DesiredValue=10.0)"

    # If the environment is locked, force dry_run_mode = True
    dry_run_mode = True if is_locked else False

    # Generate RCA text (Finance friendly summaries)
    if team_missing := ("resource_tags_user_team" in missing_tags):
        rca_text = f"Hệ thống phát hiện tài nguyên {res_id} thuộc dịch vụ {prod_code} phát sinh chi phí ${cost:.2f}/ngày nhưng bị thiếu nhãn tag quản lý (resource_tags_user_team). Đây là lỗi vi phạm chính sách Tagging (Mis-tagged Spend) của doanh nghiệp."
    else:
        rca_text = f"Tài nguyên {res_id} thuộc dịch vụ {prod_code} trên môi trường {env} bị phát hiện tăng chi phí bất thường, tiêu tốn ${cost:.2f}/ngày. Phân tích cho thấy hiệu suất sử dụng thực tế tiệm cận bằng 0 trong chu kỳ kiểm tra."

    # 3. Save rollback payload in Database (Rollback cache)
    conn = get_db_connection()
    conn.execute(
        "INSERT OR REPLACE INTO rollback_cache VALUES (?, ?, ?, ?, ?, 'ACTIVE', '', '', ?)",
        (audit_id, res_id, rollback_type, cli_rollback, boto3_equiv, datetime.now(timezone.utc).isoformat())
    )
    # Update status
    conn.execute(
        "UPDATE remediation_status SET status = 'completed', message = ? WHERE audit_id = ?",
        (f"Containment plan generated: {immediate_action}", audit_id)
    )
    conn.commit()
    conn.close()

    response_payload = {
        "audit_id": audit_id,
        "status": "completed",
        "dry_run_mode": dry_run_mode,
        "finance_dashboard_data": {
            "target_recipient": "Finance Team & CFO Dashboard",
            "metrics": {
                "unblended_cost_24h_usd": cost,
                "cost_ratio_to_7d_avg": 12.4, # Mock
                "projected_monthly_waste_usd": cost * 30
            },
            "allocation": {
                "responsible_team": "data-eng" if env == "staging" else "analytics",
                "cost_center_code": "CC-2002"
            },
            "executive_summary": rca_text
        },
        "engineering_dashboard_data": {
            "target_recipient": "Engineering Console & Slack Alert",
            "technical_context": {
                "aws_service": prod_code,
                "usage_type": "db.r5.2xlarge:ProvisionedStorage" if env == "staging" else "BoxUsage:t3.medium",
                "pricing_unit": "Hrs",
                "usage_amount_24h": 24.0,
                "usage_density_24h": 1.0
            },
            "root_cause_analysis": {
                "primary_driver_feature": "usage_density_24h",
                "technical_reason": "Tài nguyên duy trì trạng thái active liên tục nhưng ghi nhận tải CPU/kết nối bằng 0.",
                "missing_mandatory_tags": missing_tags
            },
            "mitigation_action": {
                "strategy": strategy,
                "immediate_action": immediate_action,
                "applied_payload": {
                    "action_type": action_type,
                    "aws_cli_command": "" if dry_run_mode else cli_command
                },
                "enforcement_countdown": {
                    "time_lock_seconds": time_lock,
                    "fallback_action": fallback_action
                }
            }
        }
    }
    
    return response_payload


# 5.3 POST /v1/verify — Verify Containment Effect
@app.post("/v1/verify", status_code=200)
def post_verify(
    req: VerifyRequest,
    x_tenant_id: str = Header(..., alias="X-Tenant-Id")
):
    # Verify containment took effect.
    # In a simulated environment, we check the database and update status to DONE
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT resource_id FROM remediation_status WHERE audit_id = ?", (req.audit_id,))
    row = c.fetchone()
    
    if not row:
        conn.close()
        raise HTTPException(
            status_code=404,
            detail={"internal_code": "ERR_AUDIT_NOT_FOUND", "message": "Verify called on non-existent audit session."}
        )
        
    c.execute("UPDATE remediation_status SET status = 'verified', message = 'Containment verification passed.' WHERE audit_id = ?", (req.audit_id,))
    conn.commit()
    conn.close()
    
    return {
        "audit_id": req.audit_id,
        "verification_status": "verified",
        "next_action": "DONE",
        "message": "Containment verified successfully. Saving audit trail logs."
    }


# 5.5 GET /v1/status/{id} — Check Status
@app.get("/v1/status/{id}", status_code=200)
def get_status(
    id: str,
    x_tenant_id: str = Header(..., alias="X-Tenant-Id")
):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT status, error_message, message, created_at FROM remediation_status WHERE audit_id = ?", (id,))
    row = c.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(
            status_code=404,
            detail={"internal_code": "ERR_AUDIT_NOT_FOUND", "message": "Status called on non-existent audit session."}
        )
        
    status_val, err_msg, msg, created = row
    return {
        "audit_id": id,
        "status": status_val,
        "error_message": err_msg or None,
        "message": msg,
        "created_at": created
    }


# 5.6 POST /v1/audit/{audit_id}/rollback — Execute Rollback Log
@app.post("/v1/audit/{audit_id}/rollback", status_code=200)
def post_rollback(
    audit_id: str,
    req: RollbackRequest,
    x_tenant_id: str = Header(..., alias="X-Tenant-Id")
):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT resource_id, action_type, aws_cli_rollback_command, boto3_equivalent, status FROM rollback_cache WHERE audit_id = ?", (audit_id,))
    row = c.fetchone()
    
    if not row:
        conn.close()
        raise HTTPException(
            status_code=404,
            detail={"internal_code": "ERR_AUDIT_NOT_FOUND", "message": "Rollback requested on non-existent or expired rollback session."}
        )
        
    res_id, r_type, cli_rollback, boto3_eq, status_val = row
    
    if status_val == "ROLLED_BACK":
        conn.close()
        raise HTTPException(
            status_code=422,
            detail={"internal_code": "ERR_ALREADY_ROLLED_BACK", "message": "Rollback has already been executed for this resource."}
        )

    # Execute rollback (Update cache status & error budget metrics)
    c.execute("UPDATE rollback_cache SET status = 'ROLLED_BACK', requested_by_user = ?, justification_on_rollback = ? WHERE audit_id = ?", (req.requested_by_user, req.justification_on_rollback, audit_id))
    c.execute("UPDATE remediation_status SET status = 'rolled_back', message = 'Resource restored successfully.' WHERE audit_id = ?", (audit_id,))
    conn.commit()
    conn.close()
    
    # Update error budgets (this counts as a rollback action)
    # Staging or prod identification based on resource pattern
    env = "staging" if "staging" in res_id or "db-staging" in res_id else "prod"
    update_error_budget(env, is_rollback=True)

    return {
        "audit_id": audit_id,
        "status": "rollback_initiated",
        "rollback_payload": {
            "action_type": r_type,
            "aws_cli_rollback_command": cli_rollback,
            "boto3_equivalent": boto3_eq,
            "original_resource_id": res_id
        },
        "message": "Rollback logged. Rollback CLI commands generated for CDO worker."
    }

# 5.7 Callback (Optional AI->CDO posting result)
@app.post("/v1/callback-mock-trigger")
async def trigger_callback_mock(audit_id: str, callback_url: str, background_tasks: BackgroundTasks):
    # Triggers an async task representing the webhook payload posting back to CDO
    import httpx
    
    async def post_webhook(url: str, aid: str):
        payload = {
            "audit_id": aid,
            "status": "completed",
            "message": "Async analysis finished successfully.",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        try:
            print(f"Triggering Webhook Callback POST to {url}...")
            async with httpx.AsyncClient() as client:
                await client.post(url, json=payload, timeout=5.0)
        except Exception as e:
            print(f"Failed to deliver callback: {e}")
            
    background_tasks.add_task(post_webhook, callback_url, audit_id)
    return {"message": f"Callback queued to {callback_url}"}
