# ROOT_CAUSE.py
# Stage 1: LLM Root Cause Analysis Engine (Amazon Nova Pro via Bedrock)
# Ref: data/plan2.md
#
# Nhan anomaly_record tu XGBoost DETECT.py
# Goi Amazon Nova Pro -> phan tich root cause bang ngon ngu tai chinh
# Tu dong ket luan Mis-tagged Spend neu owner tag = NaN
#
# Usage:
#   from ROOT_CAUSE import analyse_root_cause
#   rca = analyse_root_cause(anomaly_record)

import re
import json
import logging
import os
from typing import Optional

import boto3
from botocore.exceptions import ClientError, EndpointResolutionError

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ── Constants ─────────────────────────────────────────────────────────────────
NOVA_PRO_MODEL   = "amazon.nova-pro-v1:0"
BEDROCK_REGION   = os.environ.get("BEDROCK_REGION", "us-east-1")
MAX_TOKENS       = 512
TEMPERATURE      = 0.1   # thap de dam bao ket qua nhat quan

VALID_ROOT_CAUSES = {
    "Idle Resource",
    "Mis-tagged Spend",
    "Cost Spike",
    "Runaway Job",
    "Other",
}

VALID_RISK_LEVELS = {"Low", "Medium", "High", "Critical"}

# ── Bedrock client (lazy init de tranh loi khi test offline) ──────────────────
_bedrock_client = None

def _get_bedrock_client():
    global _bedrock_client
    if _bedrock_client is None:
        _bedrock_client = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
    return _bedrock_client


# ── Stage 1a: Build prompts ───────────────────────────────────────────────────
def _build_system_prompt() -> str:
    return (
        "Ban la chuyen gia FinOps cap cao voi 10 nam kinh nghiem quan ly chi phi dam may AWS.\n"
        "Nhiem vu: Phan tich du lieu chi phi AWS va xac dinh nguyen nhan goc re (Root Cause)\n"
        "bang ngon ngu tai chinh ro rang, de hieu cho CFO va Finance team.\n"
        "TUYET DOI khong dung cac thuat ngu toan hoc nhu: robust_z, rolling window, gradient.\n"
        "Khi owner tag bi MISSING: can xem xet day la dau hieu vi pham Tag Policy cua doanh nghiep,\n"
        "nhung van nen phan tich them cac signal khac (usage_density, cost_ratio) de xac dinh root cause chinh xac nhat.\n"
        "Vi du: neu resource vua missing owner tag vua idle -> root_cause = 'Idle Resource', missing_tags = ['owner'].\n"
        "Chi tra ve JSON thuan tuy, khong them markdown, khong them giai thich ngoai JSON."
    )


def _build_user_prompt(record: dict) -> str:
    owner_val   = record.get("resource_tags_user_owner")
    owner_missing = (owner_val is None or str(owner_val).strip().upper() in ("", "NAN", "NONE", "MISSING"))
    owner_display = "MISSING - vi pham Tag Policy cong ty" if owner_missing else str(owner_val)

    cost_24h      = record.get("line_item_unblended_cost", 0)
    cost_ratio    = record.get("cost_ratio_to_7d_avg", 1.0)
    usage_density = record.get("usage_density_24h", 0)
    cpu_mean      = record.get("cpu_mean", 0)
    spike         = record.get("absolute_cost_spike", 0)
    monthly_proj  = round(cost_24h * 30, 2)

    prompt = f"""Du lieu anomaly can phan tich:
- Resource ID   : {record.get('resource_id', 'unknown')}
- AWS Service   : {record.get('line_item_product_code', 'unknown')}
- Moi truong    : {record.get('environment', 'unknown')}
- Chi phi 24h   : ${cost_24h:.2f} USD
- Du bao/thang  : ${monthly_proj:.2f} USD
- So baseline   : {cost_ratio:.1f}x so voi trung binh 7 ngay truoc
- Chi phi tang dot bien : ${spike:.2f} USD (so voi muc binh thuong)
- Usage density : {usage_density:.2f}  (0 = khong chay, 1.0 = chay 24/24)
- CPU trung binh: {cpu_mean:.1f}%
- Owner tag     : {owner_display}
- Team tag      : {record.get('resource_tags_user_team', 'unknown')}

Hay phan tich va tra ve CHINH XAC JSON sau (khong them gi ngoai JSON):
{{
  "primary_driver_feature": "<ten signal chinh gay ra anomaly, vi du: usage_density_24h>",
  "root_cause_category": "<mot trong: Idle Resource | Mis-tagged Spend | Cost Spike | Runaway Job | Other>",
  "finance_summary": "<1-2 cau tom tat cho CFO, dung ngon ngu tai chinh, kem con so cu the>",
  "technical_reason": "<giai thich ky thuat chi tiet cho Engineering team>",
  "missing_mandatory_tags": ["<cac tag bi thieu, vi du: resource_tags_user_owner>"],
  "risk_level": "<Low | Medium | High | Critical>"
}}"""
    return prompt


# ── Stage 1b: Call Bedrock Nova Pro ──────────────────────────────────────────
def _call_nova_pro(system_prompt: str, user_prompt: str) -> str:
    """Goi Bedrock API va tra ve raw text response."""
    client = _get_bedrock_client()

    body = json.dumps({
        "system":   [{"text": system_prompt}],
        "messages": [{"role": "user", "content": [{"text": user_prompt}]}],
        "inferenceConfig": {
            "maxTokens":   MAX_TOKENS,
            "temperature": TEMPERATURE,
        },
    })

    response = client.invoke_model(
        modelId=NOVA_PRO_MODEL,
        body=body,
        contentType="application/json",
        accept="application/json",
    )

    resp_body = json.loads(response["body"].read())
    return resp_body["output"]["message"]["content"][0]["text"]


# ── Stage 1c: Parse + validate JSON from LLM response ────────────────────────
def _parse_rca_response(raw_text: str) -> dict:
    """Extract va validate JSON tu LLM output."""
    # Uu tien lay JSON block trong markdown code fence
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_text, re.DOTALL)
    if fence_match:
        json_str = fence_match.group(1)
    else:
        # fallback: lay JSON lon nhat trong response
        match = re.search(r"\{.*\}", raw_text, re.DOTALL)
        if not match:
            logger.error("Nova Pro tra ve response khong co JSON: %s", raw_text[:200])
            return _fallback_rca("Nova response khong chua JSON hop le")
        json_str = match.group()

    try:
        rca = json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.error("JSON decode error: %s | raw: %s", e, json_str[:200])
        return _fallback_rca(f"JSON parse error: {e}")

    # Validate va normalize
    if rca.get("root_cause_category") not in VALID_ROOT_CAUSES:
        rca["root_cause_category"] = "Other"
    if rca.get("risk_level") not in VALID_RISK_LEVELS:
        rca["risk_level"] = "Medium"
    if not isinstance(rca.get("missing_mandatory_tags"), list):
        rca["missing_mandatory_tags"] = []

    return rca


def _fallback_rca(reason: str) -> dict:
    """Tra ve RCA mac dinh khi Bedrock khong kha dung hoac loi."""
    return {
        "primary_driver_feature": "line_item_unblended_cost",
        "root_cause_category":    "Other",
        "finance_summary":        f"Khong the phan tich tu dong: {reason}",
        "technical_reason":       reason,
        "missing_mandatory_tags": [],
        "risk_level":             "Medium",
        "_fallback":              True,
    }


# ── Stage 1d: Hardcoded Mis-tagged Spend override ────────────────────────────
def _enforce_tag_policy(record: dict, rca: dict) -> dict:
    """
    Neu LLM chua phat hien Mis-tagged Spend khi owner tag bi thieu,
    bo sung vao missing_tags nhung KHONG override root_cause_category.
    LLM co the ket luan ca 2: "Idle Resource + missing owner tag"
    -> chi append tag vao list, giu nguyen RCA cua LLM.
    """
    owner_val = record.get("resource_tags_user_owner")
    owner_missing = (
        owner_val is None
        or str(owner_val).strip().upper() in ("", "NAN", "NONE", "MISSING")
    )

    if owner_missing:
        tags = rca.get("missing_mandatory_tags", [])
        if "resource_tags_user_owner" not in tags:
            tags.append("resource_tags_user_owner")
        rca["missing_mandatory_tags"] = tags
        # Chi upgrade risk neu LLM cho Low/Medium nhung owner missing
        if rca.get("risk_level") in ("Low", "Medium"):
            rca["risk_level"] = "High"
            logger.info(
                "[TAG POLICY] Resource %s: owner missing -> risk upgraded to High, tag appended",
                record.get("resource_id", "unknown"),
            )
    return rca


# ── Stage 1e: Mock mode (khi khong co Bedrock credentials) ───────────────────
def _mock_nova_rca(record: dict) -> dict:
    """
    Sinh RCA gia lap dua tren multi-signal rule (khong hardcode owner=missing->Mis-tagged).
    Thu tu uu tien: Idle Resource > Cost Spike > Runaway Job > Mis-tagged Spend > Other
    """
    cost_ratio    = record.get("cost_ratio_to_7d_avg", 1.0)
    usage_density = record.get("usage_density_24h", 0)
    cpu_mean      = record.get("cpu_mean", 50)
    cost_24h      = record.get("line_item_unblended_cost", 0)
    monthly_proj  = round(cost_24h * 30, 2)
    db_conn       = record.get("database_connections", None)
    owner_val     = record.get("resource_tags_user_owner")
    owner_missing = owner_val is None or str(owner_val).strip().upper() in ("", "NAN", "NONE", "MISSING")

    # Rule 1: Idle resource (cpu thap, dang bo hoang)
    if usage_density <= 0.15 and cost_24h > 5:
        category = "Idle Resource"
        driver   = "cpu_mean"
        reason   = (
            f"Tai nguyen co CPU chi {cpu_mean:.1f}% nhung van tiep tuc phat sinh chi phi. "
            "Khong co workload thuc su, co the da bi bo hoang."
        )
        summary  = (
            f"Tai nguyen tieu ton ${cost_24h:.2f}/ngay (du bao ${monthly_proj:.2f}/thang) "
            "trong khi CPU gan nhu khong hoat dong."
        )
        risk = "Critical" if cost_24h > 100 else ("High" if cost_24h > 20 else "Medium")
        category = "Idle Resource"
        driver   = "usage_density_24h"
        reason   = (
            f"Tai nguyen chay {usage_density:.0%} thoi gian nhung CPU chi {cpu_mean:.1f}%. "
            "Khong co workload thuc su, co the da bi bo hoang."
        )
        summary  = (
            f"Tai nguyen tieu ton ${cost_24h:.2f}/ngay (du bao ${monthly_proj:.2f}/thang) "
            "trong khi khong co hoat dong thuc su."
        )
        risk = "Critical" if cost_24h > 100 else ("High" if cost_ratio > 5 else "Medium")

    # Rule 1b: Runaway Job (cpu rat cao, cost ratio binh thuong = unexpected high CPU)
    elif cpu_mean > 90 and cost_24h > 10:
        category = "Runaway Job"
        driver   = "cpu_mean"
        reason   = (
            f"CPU dang chay {cpu_mean:.1f}% lien tuc - cao bat thuong. "
            "Co the la job bi loop, process zombie, hoac workload chua duoc optimize."
        )
        summary  = (
            f"Tai nguyen dang chay full CPU {cpu_mean:.1f}%, tieu ton ${cost_24h:.2f}/ngay. "
            f"Du bao ${monthly_proj:.2f}/thang neu khong dung lai."
        )
        risk = "Critical" if cpu_mean > 95 else "High"

    # Rule 2: DB idle (co connections ~ 0)
    elif db_conn is not None and db_conn < 2 and usage_density >= 0.5:
        category = "Idle Resource"
        driver   = "database_connections"
        reason   = (
            f"Database chay lien tuc nhung Active Connections chi {db_conn:.0f}. "
            "Instance bi bo hoang sau ket thuc cong viec."
        )
        summary  = (
            f"Database tieu ton ${cost_24h:.2f}/ngay du khong co ket noi hoat dong. "
            f"Du bao ${monthly_proj:.2f}/thang lang phi."
        )
        risk = "High"

    # Rule 3: Cost spike dot bien
    elif cost_ratio > 8:
        category = "Cost Spike"
        driver   = "cost_ratio_to_7d_avg"
        reason   = (
            f"Chi phi tang dot bien {cost_ratio:.1f}x so voi trung binh 7 ngay. "
            "Co the la runaway job, bat thu vien ngoai lenh, hoac thay doi cau hinh bat ngo."
        )
        summary  = (
            f"Chi phi tang {cost_ratio:.1f}x trong 24h qua, tong ${cost_24h:.2f}/ngay. "
            f"Can kiem tra ngay de tranh thiet hai ${monthly_proj:.2f}/thang."
        )
        risk = "Critical" if cost_ratio > 20 else "High"

    # Rule 4: Missing owner tag (chi khi khong co signal ro rang hon)
    elif owner_missing:
        category = "Mis-tagged Spend"
        driver   = "resource_tags_user_owner"
        reason   = (
            "Tai nguyen khong co owner tag bat buoc. "
            "Khong the xac dinh team chiu trach nhiem chi phi."
        )
        summary  = (
            f"Chi phi ${cost_24h:.2f}/ngay khong the quy cho team cu the do thieu owner tag. "
            "Vi pham Tag Policy cong ty."
        )
        risk = "Medium"

    else:
        category = "Other"
        driver   = "line_item_unblended_cost"
        reason   = "Bat thuong chi phi phat hien boi mo hinh XGBoost, can xem xet them."
        summary  = f"Phat hien chi phi bat thuong ${cost_24h:.2f}/ngay. Can review."
        risk = "Medium"

    missing_tags = ["resource_tags_user_owner"] if owner_missing else []

    return {
        "primary_driver_feature": driver,
        "root_cause_category":    category,
        "finance_summary":        summary,
        "technical_reason":       reason,
        "missing_mandatory_tags": missing_tags,
        "risk_level":             risk,
        "_mock":                  True,
    }


# ── Public API ─────────────────────────────────────────────────────────────────
def analyse_root_cause(record: dict) -> dict:
    """
    Entry point Stage 1.
    Nhan anomaly_record tu XGBoost, tra ve RCA dict.

    Parameters
    ----------
    record : dict
        Anomaly record voi cac truong: resource_id, environment,
        confidence_score, line_item_product_code, line_item_unblended_cost,
        cost_ratio_to_7d_avg, usage_density_24h, cpu_mean,
        resource_tags_user_owner, resource_tags_user_team, absolute_cost_spike

    Returns
    -------
    dict:
        primary_driver_feature, root_cause_category, finance_summary,
        technical_reason, missing_mandatory_tags, risk_level
    """
    use_mock = os.environ.get("BEDROCK_MOCK", "false").lower() == "true"

    resource_id = record.get("resource_id", "unknown")
    logger.info("[RCA] Analysing: %s | env=%s | confidence=%.2f",
                resource_id, record.get("environment"), record.get("confidence_score", 0))

    if use_mock:
        logger.info("[RCA] MOCK mode active")
        rca = _mock_nova_rca(record)
    else:
        try:
            system_prompt = _build_system_prompt()
            user_prompt   = _build_user_prompt(record)
            raw_text      = _call_nova_pro(system_prompt, user_prompt)
            logger.debug("[RCA] Nova raw response: %s", raw_text[:300])
            rca = _parse_rca_response(raw_text)
        except (ClientError, EndpointResolutionError) as e:
            logger.warning("[RCA] Bedrock unavailable (%s), using mock fallback", e)
            rca = _mock_nova_rca(record)
        except Exception as e:
            logger.error("[RCA] Unexpected error: %s", e, exc_info=True)
            rca = _fallback_rca(str(e))

    # Hardcoded override: neu owner missing -> Mis-tagged Spend
    rca = _enforce_tag_policy(record, rca)

    logger.info("[RCA] Result: category=%s | risk=%s | driver=%s",
                rca.get("root_cause_category"),
                rca.get("risk_level"),
                rca.get("primary_driver_feature"))
    return rca


# ── CLI test ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import json as _json

    # Test records
    test_cases = [
        {
            "name": "Staging RDS idle + missing owner",
            "record": {
                "resource_id":               "arn:aws:rds:us-east-1:200000000012:db:db-staging-01",
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
        },
        {
            "name": "Dev EC2 cost spike",
            "record": {
                "resource_id":               "i-0abc123dev456",
                "environment":               "dev",
                "confidence_score":          0.87,
                "line_item_product_code":    "AmazonEC2",
                "line_item_unblended_cost":  45.0,
                "cost_ratio_to_7d_avg":      18.5,
                "usage_density_24h":         0.6,
                "cpu_mean":                  72.0,
                "resource_tags_user_owner":  "alice@company.com",
                "resource_tags_user_team":   "backend",
                "absolute_cost_spike":       38.0,
            },
        },
        {
            "name": "ML Research SageMaker idle GPU",
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
        },
    ]

    # Set mock mode de test khong can Bedrock credentials
    os.environ["BEDROCK_MOCK"] = "true"

    for tc in test_cases:
        print(f"\n{'='*60}")
        print(f"Test: {tc['name']}")
        print(f"{'='*60}")
        result = analyse_root_cause(tc["record"])
        print(_json.dumps(result, indent=2, ensure_ascii=False))
