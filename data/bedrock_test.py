# bedrock_test.py
# Test ket noi Bedrock + DynamoDB truoc khi chay pipeline that
# Chay: python bedrock_test.py
#
# Pass het  -> python RUN_PIPELINE.py --no-mock
# Fail step -> xem huong dan sua o cuoi output

import boto3
import json
import sys
from botocore.exceptions import ClientError, NoCredentialsError

REGION       = "us-east-1"
DYNAMO_TABLE = "FinOps_Audit_Store"
NOVA_MODEL   = "amazon.nova-lite-v1:0"

results = []


def check(name, fn):
    try:
        fn()
        print(f"  [PASS] {name}")
        results.append((name, True, ""))
    except Exception as e:
        print(f"  [FAIL] {name}")
        print(f"         Error: {e}")
        results.append((name, False, str(e)))


# ── Step 1: Credentials ───────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("STEP 1: AWS Credentials")
print("=" * 60)


def test_creds():
    sts = boto3.client("sts", region_name=REGION)
    idn = sts.get_caller_identity()
    print(f"         Account: {idn['Account']}")
    print(f"         ARN    : {idn['Arn']}")


check("AWS credentials (STS)", test_creds)

# ── Step 2: Bedrock ───────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("STEP 2: Bedrock Nova")
print("=" * 60)


def test_bedrock_invoke():
    client = boto3.client("bedrock-runtime", region_name=REGION)
    body = json.dumps({
        "messages": [{"role": "user", "content": [{"text": "Reply: OK"}]}],
        "inferenceConfig": {"maxTokens": 5, "temperature": 0.0},
    })
    resp = client.invoke_model(
        modelId=NOVA_MODEL, body=body,
        contentType="application/json", accept="application/json",
    )
    out  = json.loads(resp["body"].read())
    text = out["output"]["message"]["content"][0]["text"]
    print(f"         Nova replied: {text.strip()[:40]}")


check(f"Bedrock invoke {NOVA_MODEL}", test_bedrock_invoke)

# ── Step 3: DynamoDB ──────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("STEP 3: DynamoDB Table")
print("=" * 60)


def test_dynamo_exists():
    ddb  = boto3.client("dynamodb", region_name=REGION)
    resp = ddb.describe_table(TableName=DYNAMO_TABLE)
    st   = resp["Table"]["TableStatus"]
    print(f"         Table status: {st}")
    if st != "ACTIVE":
        raise Exception(f"Table not ACTIVE: {st}")


check(f"Table '{DYNAMO_TABLE}' exists and ACTIVE", test_dynamo_exists)


def test_dynamo_write_read():
    import uuid
    from datetime import datetime, timezone
    tbl = boto3.resource("dynamodb", region_name=REGION).Table(DYNAMO_TABLE)
    aid = f"TEST-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()
    tbl.put_item(Item={
        "audit_id": aid, "timestamp": now,
        "resource_id": "test", "environment": "test",
        "ttl": 9999999999, "_test": "true",
    })
    item = tbl.get_item(Key={"audit_id": aid, "timestamp": now})
    assert item.get("Item"), "Item not found after write"
    print(f"         Write+Read OK: {aid}")


check("DynamoDB write + read", test_dynamo_write_read)

# ── Step 4: EC2/RDS read ──────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("STEP 4: IAM Permissions (read-only check)")
print("=" * 60)


def test_ec2():
    r = boto3.client("ec2", region_name=REGION).describe_instances(MaxResults=5)
    n = sum(len(x["Instances"]) for x in r["Reservations"])
    print(f"         EC2 instances visible: {n}")


def test_rds():
    r = boto3.client("rds", region_name=REGION).describe_db_instances()
    print(f"         RDS instances visible: {len(r['DBInstances'])}")


check("EC2 describe-instances", test_ec2)
check("RDS describe-db-instances", test_rds)

# ── Summary ───────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
passed = [r for r in results if r[1]]
failed = [r for r in results if not r[1]]
print(f"RESULT: {len(passed)}/{len(results)} passed")
print("=" * 60)

if failed:
    print("\nFix guide:")
    for name, ok, err in failed:
        print(f"\n  FAIL: {name}")
        if "credential" in name.lower() or "NoCredentials" in err:
            print("  -> aws configure  (nhap Access Key + Secret + region=us-east-1)")
        elif "Bedrock" in name:
            print("  -> AWS Console > Amazon Bedrock > Model Access > Enable Nova Lite/Pro")
            print("  -> IAM: them quyen bedrock:InvokeModel")
        elif "exists" in name:
            print("  -> Tao bang:")
            print("     aws dynamodb create-table \\")
            print("       --table-name FinOps_Audit_Store \\")
            print("       --attribute-definitions AttributeName=audit_id,AttributeType=S AttributeName=timestamp,AttributeType=S \\")
            print("       --key-schema AttributeName=audit_id,KeyType=HASH AttributeName=timestamp,KeyType=RANGE \\")
            print("       --billing-mode PAY_PER_REQUEST")
            print("     aws dynamodb update-time-to-live \\")
            print("       --table-name FinOps_Audit_Store \\")
            print("       --time-to-live-specification Enabled=true,AttributeName=ttl")
        elif "DynamoDB" in name:
            print("  -> IAM: them quyen dynamodb:PutItem, dynamodb:GetItem")
        elif "EC2" in name or "RDS" in name:
            print("  -> IAM: them quyen ec2:DescribeInstances, rds:DescribeDBInstances")
    sys.exit(1)
else:
    print("\nAll checks passed! Ready:")
    print("  python RUN_PIPELINE.py --no-mock --limit 10       # Bedrock+Dynamo that, CLI dry-run")
    print("  python RUN_PIPELINE.py --no-mock --live --limit 5 # Toan bo that")
    sys.exit(0)
