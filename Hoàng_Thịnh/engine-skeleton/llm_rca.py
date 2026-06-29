from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "finops_rca_system_prompt.txt"
DEFAULT_PROMPT = """You are a FinOps incident analyst.
Return only valid JSON.
Write concise RCA text for a cost anomaly using the supplied structured facts.
Do not invent AWS actions or data not present in the input.
Keep executive_summary under 220 characters.
Keep technical_reason under 320 characters.
Keep recommended_follow_up under 160 characters.
Return this JSON object:
{
  "executive_summary": "...",
  "technical_reason": "...",
  "primary_driver_feature": "...",
  "recommended_follow_up": "..."
}
"""


def load_prompt() -> str:
    if PROMPT_PATH.exists():
        return PROMPT_PATH.read_text(encoding="utf-8")
    return DEFAULT_PROMPT


def coerce_text(value: Any, limit: int) -> str:
    text = "" if value is None else str(value).strip()
    return text[:limit]


@dataclass
class RcaGeneration:
    executive_summary: str
    technical_reason: str
    primary_driver_feature: str
    recommended_follow_up: str
    status: str
    provider: str
    model_id: str
    latency_ms: int | None = None
    error: str | None = None


class RcaGenerator(Protocol):
    provider: str
    model_id: str
    enabled: bool

    def generate(self, event: dict[str, Any], fallback: dict[str, str]) -> RcaGeneration:
        ...


class DisabledRcaGenerator:
    provider = "disabled"
    model_id = "none"
    enabled = False

    def generate(self, event: dict[str, Any], fallback: dict[str, str]) -> RcaGeneration:
        return RcaGeneration(
            executive_summary=fallback["executive_summary"],
            technical_reason=fallback["technical_reason"],
            primary_driver_feature=fallback["primary_driver_feature"],
            recommended_follow_up=fallback["recommended_follow_up"],
            status="disabled",
            provider=self.provider,
            model_id=self.model_id,
        )


class BedrockRcaGenerator:
    provider = "bedrock"
    enabled = True

    def __init__(self, model_id: str, region: str, max_tokens: int = 240, temperature: float = 0.1):
        import boto3

        self.model_id = model_id
        self.region = region
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.prompt = load_prompt()
        self.client = boto3.client("bedrock-runtime", region_name=region)

    def _build_payload(self, event: dict[str, Any], fallback: dict[str, str]) -> dict[str, Any]:
        return {
            "anomaly_type": event["anomaly_type"],
            "resource_id": event["resource_id"],
            "account_name": event["account_name"],
            "environment": event["environment"],
            "service_code": event["service_code"],
            "event_days": event["event_days"],
            "avg_daily_cost": event["avg_daily_cost"],
            "event_total_cost": event["event_total_cost"],
            "confidence_score": event["confidence_score"],
            "driver_metrics": {
                "usage_density_24h": event.get("usage_density_24h"),
                "cost_ratio_to_7d_avg": event.get("cost_ratio_to_7d_avg"),
                "resource_cost_mean_ratio_28vprev28": event.get("resource_cost_mean_ratio_28vprev28"),
                "cpu_percent": event.get("cpu_percent"),
                "gpu_utilization": event.get("gpu_utilization"),
                "database_connections": event.get("database_connections"),
                "xgb_score": event.get("xgb_score"),
                "type_hint_confidence": event.get("type_hint_confidence"),
            },
            "team": event.get("team"),
            "owner": event.get("owner"),
            "cost_center": event.get("cost_center"),
            "fallback": fallback,
        }

    def _extract_text(self, response: dict[str, Any]) -> str:
        parts = response.get("output", {}).get("message", {}).get("content", [])
        text_parts = [part.get("text", "") for part in parts if isinstance(part, dict) and "text" in part]
        return "\n".join(text_parts).strip()

    def _parse_json(self, text: str, fallback: dict[str, str]) -> RcaGeneration:
        try:
            start = text.find("{")
            end = text.rfind("}")
            data = json.loads(text[start : end + 1] if start >= 0 and end >= 0 else text)
        except Exception as exc:
            return RcaGeneration(
                executive_summary=fallback["executive_summary"],
                technical_reason=fallback["technical_reason"],
                primary_driver_feature=fallback["primary_driver_feature"],
                recommended_follow_up=fallback["recommended_follow_up"],
                status="fallback_parse_error",
                provider=self.provider,
                model_id=self.model_id,
                error=type(exc).__name__,
            )

        return RcaGeneration(
            executive_summary=coerce_text(data.get("executive_summary") or fallback["executive_summary"], 220),
            technical_reason=coerce_text(data.get("technical_reason") or fallback["technical_reason"], 320),
            primary_driver_feature=coerce_text(data.get("primary_driver_feature") or fallback["primary_driver_feature"], 80),
            recommended_follow_up=coerce_text(data.get("recommended_follow_up") or fallback["recommended_follow_up"], 160),
            status="generated",
            provider=self.provider,
            model_id=self.model_id,
        )

    def generate(self, event: dict[str, Any], fallback: dict[str, str]) -> RcaGeneration:
        started = time.perf_counter()
        try:
            response = self.client.converse(
                modelId=self.model_id,
                system=[{"text": self.prompt}],
                messages=[
                    {
                        "role": "user",
                        "content": [{"text": json.dumps(self._build_payload(event, fallback), ensure_ascii=False)}],
                    }
                ],
                inferenceConfig={"maxTokens": self.max_tokens, "temperature": self.temperature},
            )
            result = self._parse_json(self._extract_text(response), fallback)
        except Exception as exc:
            result = RcaGeneration(
                executive_summary=fallback["executive_summary"],
                technical_reason=fallback["technical_reason"],
                primary_driver_feature=fallback["primary_driver_feature"],
                recommended_follow_up=fallback["recommended_follow_up"],
                status="fallback_invoke_error",
                provider=self.provider,
                model_id=self.model_id,
                error=type(exc).__name__,
            )
        result.latency_ms = int((time.perf_counter() - started) * 1000)
        return result


def load_rca_generator() -> RcaGenerator:
    provider = os.getenv("FINOPS_LLM_PROVIDER", "").strip().lower()
    if provider != "bedrock":
        return DisabledRcaGenerator()

    model_id = os.getenv("FINOPS_BEDROCK_MODEL_ID", "").strip()
    region = os.getenv("FINOPS_BEDROCK_REGION", "").strip() or os.getenv("AWS_REGION", "").strip()
    if not model_id or not region:
        return DisabledRcaGenerator()

    max_tokens = int(os.getenv("FINOPS_LLM_MAX_TOKENS", "240"))
    temperature = float(os.getenv("FINOPS_LLM_TEMPERATURE", "0.1"))
    return BedrockRcaGenerator(model_id=model_id, region=region, max_tokens=max_tokens, temperature=temperature)
