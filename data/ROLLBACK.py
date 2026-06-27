# ROLLBACK.py
# Xem audit log tu DynamoDB va thuc thi rollback command
# Chay:
#   python ROLLBACK.py --list                        # xem 20 audit records moi nhat
#   python ROLLBACK.py --audit-id <id>               # xem chi tiet 1 record
#   python ROLLBACK.py --audit-id <id> --dry-run     # kiem tra rollback command
#   python ROLLBACK.py --audit-id <id> --execute     # thuc thi rollback that
#   python ROLLBACK.py --resource <resource_id>      # xem lich su 1 resource

import argparse
import json
import subprocess
import sys
import os
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key, Attr

REGION       = os.environ.get("DYNAMO_REGION", "us-east-1")
DYNAMO_TABLE = os.environ.get("FINOPS_AUDIT_TABLE", "FinOps_Audit_Store")
USE_MOCK     = os.environ.get("BEDROCK_MOCK", "false").lower() == "true"


def get_table():
    ddb = boto3.resource("dynamodb", region_name=REGION)
    return ddb.Table(DYNAMO_TABLE)


# ── Mock audit store khi test khong co DynamoDB ───────────────────────────────
MOCK_AUDIT = []


def _load_mock_from_json():
    """Load audit records tu pipeline_results.json neu co."""
    global MOCK_AUDIT
    if os.path.exists("pipeline_results.json"):
        with open("pipeline_results.json", encoding="utf-8") as f:
            data = json.load(f)
        MOCK_AUDIT = [r for r in data if r.get("status") == "ok"]


# ── List recent audit records ─────────────────────────────────────────────────
def list_audits(limit: int = 20):
    """Hien thi N audit record moi nhat."""
    use_mock_now = USE_MOCK or os.environ.get("BEDROCK_MOCK", "false").lower() == "true"
    if use_mock_now:
        _load_mock_from_json()
        print(f"\n[MOCK] Audit records from pipeline_results.json ({len(MOCK_AUDIT)} total):\n")
        print(f"{'#':<4} {'audit_id':<38} {'env':<15} {'action':<25} {'root_cause':<22} {'resource'}")
        print("-" * 130)
        for i, r in enumerate(MOCK_AUDIT[:limit], 1):
            print(f"{i:<4} {r.get('audit_id','N/A'):<38} "
                  f"{r.get('environment','?'):<15} "
                  f"{r.get('action','?'):<25} "
                  f"{r.get('root_cause','?'):<22} "
                  f"{r.get('resource_id','?')[:50]}")
        return

    try:
        tbl  = get_table()
        resp = tbl.scan(Limit=limit,
                        FilterExpression=Attr("_test").not_exists())
        items = sorted(resp.get("Items", []),
                       key=lambda x: x.get("timestamp", ""), reverse=True)[:limit]

        print(f"\nAudit records (latest {len(items)}):\n")
        print(f"{'#':<4} {'audit_id':<38} {'env':<15} {'action':<25} {'root_cause':<22} {'resource'}")
        print("-" * 130)
        for i, item in enumerate(items, 1):
            print(f"{i:<4} {item.get('audit_id','N/A'):<38} "
                  f"{item.get('environment','?'):<15} "
                  f"{item.get('action_taken','?'):<25} "
                  f"{item.get('root_cause_category','?'):<22} "
                  f"{item.get('resource_id','?')[:50]}")
    except Exception as e:
        print(f"[ERROR] Cannot read DynamoDB: {e}")
        print("  -> Run: python bedrock_test.py  to check connection")
        sys.exit(1)


# ── Get single audit record ───────────────────────────────────────────────────
def get_audit(audit_id: str) -> dict:
    if USE_MOCK:
        _load_mock_from_json()
        for r in MOCK_AUDIT:
            if r.get("audit_id") == audit_id:
                return r
        return {}

    try:
        tbl  = get_table()
        resp = tbl.scan(FilterExpression=Attr("audit_id").eq(audit_id))
        items = resp.get("Items", [])
        return items[0] if items else {}
    except Exception as e:
        print(f"[ERROR] {e}")
        return {}


def show_detail(audit_id: str):
    item = get_audit(audit_id)
    if not item:
        print(f"[NOT FOUND] audit_id={audit_id}")
        sys.exit(1)

    print(f"\n{'='*65}")
    print(f"Audit Record: {audit_id}")
    print(f"{'='*65}")
    for k, v in sorted(item.items()):
        if k == "ttl":
            ts = datetime.fromtimestamp(int(v), tz=timezone.utc).isoformat()
            print(f"  {k:<25}: {v}  (expires {ts})")
        elif k in ("cli_commands", "missing_tags"):
            try:
                parsed = json.loads(v) if isinstance(v, str) else v
                print(f"  {k:<25}: {parsed}")
            except Exception:
                print(f"  {k:<25}: {v}")
        else:
            print(f"  {k:<25}: {v}")

    # Show rollback command
    rollback = item.get("rollback_command", "")
    if rollback:
        print(f"\n{'='*65}")
        print(f"ROLLBACK COMMAND:")
        print(f"  {rollback}")
        print(f"{'='*65}")
        print(f"\nTo execute rollback:")
        print(f"  python ROLLBACK.py --audit-id {audit_id} --dry-run   # preview")
        print(f"  python ROLLBACK.py --audit-id {audit_id} --execute   # run it")
    else:
        print("\n[INFO] No rollback command stored for this action.")


# ── Execute rollback ──────────────────────────────────────────────────────────
def execute_rollback(audit_id: str, dry_run: bool = True):
    item = get_audit(audit_id)
    if not item:
        print(f"[NOT FOUND] audit_id={audit_id}")
        sys.exit(1)

    rollback_cmd = item.get("rollback_command", "").strip()
    resource     = item.get("resource_id", "unknown")
    env          = item.get("environment", "unknown")
    action       = item.get("action_taken", item.get("action", "unknown"))

    print(f"\n{'='*65}")
    print(f"ROLLBACK for audit_id: {audit_id}")
    print(f"  Resource   : {resource}")
    print(f"  Environment: {env}")
    print(f"  Action taken was: {action}")
    print(f"  Rollback cmd    : {rollback_cmd}")
    print(f"  Mode            : {'DRY RUN' if dry_run else 'LIVE EXECUTION'}")
    print(f"{'='*65}")

    if not rollback_cmd:
        print("[SKIP] No rollback command stored. Nothing to undo.")
        return

    # Safety: prod resources tuyet doi khong rollback tu dong
    if env == "prod" and not dry_run:
        print("[BLOCKED] Prod environment: manual rollback required.")
        print(f"  Please run manually: {rollback_cmd}")
        return

    if dry_run:
        print(f"\n[DRY RUN] Would execute:\n  {rollback_cmd}")
        print("\nTo actually execute, add --execute flag")
        return

    # Live execution
    print(f"\n[LIVE] Executing: {rollback_cmd}")
    try:
        result = subprocess.run(
            rollback_cmd.split(),
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            print(f"[SUCCESS] returncode=0")
            if result.stdout:
                print(f"  stdout: {result.stdout.strip()[:200]}")
        else:
            print(f"[ERROR] returncode={result.returncode}")
            print(f"  stderr: {result.stderr.strip()[:200]}")

        # Log rollback action vao DynamoDB
        _log_rollback(audit_id, rollback_cmd, result.returncode, dry_run=False)

    except subprocess.TimeoutExpired:
        print("[ERROR] Command timed out after 30s")
    except Exception as e:
        print(f"[ERROR] {e}")


def _log_rollback(original_audit_id: str, cmd: str, returncode: int, dry_run: bool):
    """Ghi rollback event vao DynamoDB audit store."""
    import uuid
    if USE_MOCK:
        print(f"[MOCK] Would log rollback to DynamoDB: {original_audit_id}")
        return
    try:
        tbl = get_table()
        now = datetime.now(timezone.utc)
        tbl.put_item(Item={
            "audit_id":            f"ROLLBACK-{uuid.uuid4().hex[:8]}",
            "timestamp":           now.isoformat(),
            "original_audit_id":   original_audit_id,
            "action_taken":        "rollback",
            "rollback_command":    cmd,
            "returncode":          str(returncode),
            "dry_run":             str(dry_run),
            "ttl":                 int(now.timestamp()) + 90 * 86400,
        })
        print("[AUDIT] Rollback event logged to DynamoDB.")
    except Exception as e:
        print(f"[WARN] Could not log rollback: {e}")


# ── Query by resource ─────────────────────────────────────────────────────────
def query_by_resource(resource_id: str, limit: int = 10):
    if USE_MOCK:
        _load_mock_from_json()
        matches = [r for r in MOCK_AUDIT if resource_id in r.get("resource_id", "")]
        print(f"\n[MOCK] History for resource containing '{resource_id}':")
        for r in matches[:limit]:
            print(f"  {r.get('audit_id')} | {r.get('action')} | {r.get('root_cause')}")
        return

    try:
        tbl  = get_table()
        resp = tbl.scan(FilterExpression=Attr("resource_id").contains(resource_id))
        items = sorted(resp.get("Items", []),
                       key=lambda x: x.get("timestamp", ""), reverse=True)[:limit]
        print(f"\nHistory for resource '{resource_id}' ({len(items)} records):\n")
        print(f"{'timestamp':<30} {'action':<25} {'status':<15} {'audit_id'}")
        print("-" * 100)
        for item in items:
            ts = item.get("timestamp", "")[:19]
            print(f"{ts:<30} {item.get('action_taken','?'):<25} "
                  f"{item.get('execution_status','?'):<15} {item.get('audit_id','?')}")
    except Exception as e:
        print(f"[ERROR] {e}")


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    p = argparse.ArgumentParser(description="FinOps Audit Log Viewer & Rollback Tool")
    p.add_argument("--list",       action="store_true",  help="Hien thi 20 audit records moi nhat")
    p.add_argument("--audit-id",   type=str, default="", help="Xem chi tiet hoac rollback 1 audit record")
    p.add_argument("--resource",   type=str, default="", help="Xem lich su 1 resource ID")
    p.add_argument("--dry-run",    action="store_true",  help="Xem rollback command (khong chay)")
    p.add_argument("--execute",    action="store_true",  help="Thuc thi rollback that")
    p.add_argument("--limit",      type=int, default=20, help="So records hien thi")
    p.add_argument("--mock",       action="store_true",  help="Dung mock data tu pipeline_results.json")
    args = p.parse_args()

    if args.mock:
        os.environ["BEDROCK_MOCK"] = "true"

    if args.list or (not args.audit_id and not args.resource):
        list_audits(limit=args.limit)

    elif args.resource:
        query_by_resource(args.resource, limit=args.limit)

    elif args.audit_id:
        if args.execute:
            execute_rollback(args.audit_id, dry_run=False)
        elif args.dry_run:
            execute_rollback(args.audit_id, dry_run=True)
        else:
            show_detail(args.audit_id)
