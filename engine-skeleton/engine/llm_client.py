import json
import logging
import boto3
from typing import Dict, Any, List
from config.settings import get_settings

logger = logging.getLogger("finops-engine.llm")

class BedrockLLMClient:
    """
    AWS Bedrock client for generating Root Cause Analysis (RCA) and executive summaries.
    Uses amazon.nova-pro-v1:0.
    Falls back to structured templates if Bedrock is disabled, unreachable, or fails.
    """

    def __init__(self):
        self.settings = get_settings()
        self._bedrock_client = None

    def _get_client(self):
        if not self.settings.enable_llm_analysis:
            return None
        if self._bedrock_client is None:
            try:
                # Initialize bedrock-runtime client with the configured region
                self._bedrock_client = boto3.client(
                    "bedrock-runtime",
                    region_name=self.settings.bedrock_region
                )
            except Exception as e:
                logger.error(f"Failed to initialize AWS Bedrock client: {e}")
                self._bedrock_client = None
        return self._bedrock_client

    def generate_rca(self, anomaly_context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate RCA and summary.
        Returns: {
            "technical_reason": str,
            "executive_summary": str,
            "primary_driver_feature": str,
            "missing_mandatory_tags": List[str]
        }
        """
        resource_id = anomaly_context.get("resource_id", "unknown-resource")
        anomaly_type = anomaly_context.get("anomaly_type", "unknown")
        environment = anomaly_context.get("environment", "unknown")
        cost = anomaly_context.get("unblended_cost_24h_usd", 0.0)
        ratio = anomaly_context.get("cost_ratio_to_7d_avg", 1.0)
        team = anomaly_context.get("responsible_team") or "N/A"
        cost_center = anomaly_context.get("cost_center_code") or "N/A"

        # Try Bedrock if enabled
        client = self._get_client()
        if client:
            try:
                prompt = (
                    "You are a FinOps AI assistant specializing in AWS cost optimization and root cause analysis.\n"
                    f"Analyze the following cost anomaly context:\n"
                    f"- Resource ID: {resource_id}\n"
                    f"- Anomaly Type: {anomaly_type}\n"
                    f"- Environment: {environment}\n"
                    f"- 24-Hour Cost: ${cost:.2f} USD ({ratio:.1f}x baseline)\n"
                    f"- Responsible Team: {team}\n"
                    f"- Cost Center Code: {cost_center}\n\n"
                    "Generate a JSON response with the following fields:\n"
                    "1. \"technical_reason\": A detailed, professional engineering explanation of why this anomaly might have occurred. Be specific to the anomaly type.\n"
                    "2. \"executive_summary\": A high-level, business-friendly summary for the CFO explaining the cost increase, projected monthly impact, and why it requires attention or can be safely contained.\n"
                    "3. \"primary_driver_feature\": The main technical metric or feature driving the alert (e.g. cost_deviation_ratio, sudden_cpu_spike, untagged_spend, idle_drift).\n"
                    "4. \"missing_mandatory_tags\": List any typical FinOps tags that are likely missing or need review (e.g. Owner, CostCenter, Project, ExpirationDate).\n\n"
                    "Your response MUST be ONLY valid JSON matching this schema:\n"
                    "{\n"
                    '  "technical_reason": "...",\n'
                    '  "executive_summary": "...",\n'
                    '  "primary_driver_feature": "...",\n'
                    '  "missing_mandatory_tags": ["tag1", "tag2"]\n'
                    "}"
                )

                logger.info(f"Invoking Bedrock LLM ({self.settings.bedrock_model_id}) for resource {resource_id}...")
                
                response = client.converse(
                    modelId=self.settings.bedrock_model_id,
                    messages=[
                        {
                            "role": "user",
                            "content": [{"text": prompt}]
                        }
                    ],
                    inferenceConfig={
                        "temperature": 0.1,
                        "maxTokens": 1000,
                    }
                )

                response_text = response["output"]["message"]["content"][0]["text"]
                # Try parsing the JSON
                # Clean up any potential markdown code blocks returned by LLM
                clean_text = response_text.strip()
                if clean_text.startswith("```json"):
                    clean_text = clean_text[7:]
                if clean_text.endswith("```"):
                    clean_text = clean_text[:-3]
                clean_text = clean_text.strip()

                parsed = json.loads(clean_text)
                
                # Validate required keys
                required = ["technical_reason", "executive_summary", "primary_driver_feature", "missing_mandatory_tags"]
                if all(k in parsed for k in required) and isinstance(parsed["missing_mandatory_tags"], list):
                    logger.info("Successfully generated RCA via Bedrock LLM.")
                    return parsed
                else:
                    logger.warning("Bedrock LLM response missing required JSON fields. Falling back to template.")

            except Exception as e:
                logger.error(f"Error calling Bedrock LLM: {e}. Falling back to template.")

        # Fallback to deterministic templates
        return self._generate_template_rca(anomaly_type, resource_id, environment, cost, ratio, team, cost_center)

    def _generate_template_rca(
        self,
        anomaly_type: str,
        resource_id: str,
        environment: str,
        cost: float,
        ratio: float,
        team: str,
        cost_center: str
    ) -> Dict[str, Any]:
        """Generate structured default templates for RCA & summaries based on anomaly type."""
        projected = cost * 30
        
        if anomaly_type == "runaway_usage":
            return {
                "technical_reason": (
                    f"Sudden runaway usage detected on {resource_id} in {environment}. "
                    f"Resource cost surged to {ratio:.1f}x baseline, indicating a potential runaway processing loop, "
                    f"massive data ingestion, or runaway batch job."
                ),
                "executive_summary": (
                    f"Runaway usage on resource {resource_id} is costing ${cost:.2f}/day. "
                    f"Immediate containment is recommended to prevent a projected monthly waste of ${projected:.2f}."
                ),
                "primary_driver_feature": "cost_deviation_ratio_7d",
                "missing_mandatory_tags": ["Owner", "Project"]
            }
            
        elif anomaly_type == "idle_resource":
            return {
                "technical_reason": (
                    f"Resource {resource_id} in {environment} is idle, showing extremely low CPU/memory utilization "
                    f"despite incurring continuous daily costs of ${cost:.2f}/day."
                ),
                "executive_summary": (
                    f"Active waste detected. Idle resource {resource_id} is incurring unnecessary costs. "
                    f"Recommended to stop or downscale the resource to save up to ${projected:.2f}/month."
                ),
                "primary_driver_feature": "idle_usage_threshold",
                "missing_mandatory_tags": ["Lifecycle"]
            }
            
        elif anomaly_type == "gradual_drift":
            return {
                "technical_reason": (
                    f"Gradual cost drift detected on {resource_id} in {environment}. "
                    f"Costs have increased slowly over the 14-day window, suggesting database growth, "
                    f"resource leaks, or gradual scale-up without tag modifications."
                ),
                "executive_summary": (
                    f"Gradual cost increase on {resource_id} detected. Monthly run rate has drifted "
                    f"upwards to ${projected:.2f}/month. Recommend auditing the resource capacity."
                ),
                "primary_driver_feature": "gradual_drift_increase",
                "missing_mandatory_tags": ["CostCenter"]
            }
            
        elif anomaly_type == "untagged_spend":
            return {
                "technical_reason": (
                    f"Cost exceeding threshold of ${self.settings.untagged_cost_threshold_usd} USD "
                    f"detected on resource {resource_id} which does not possess the mandatory tags."
                ),
                "executive_summary": (
                    f"Untagged resource {resource_id} is costing ${cost:.2f}/day. "
                    f"Violates organizational tagging compliance. Corrective action is needed for cost allocation."
                ),
                "primary_driver_feature": "untagged_spend_ratio",
                "missing_mandatory_tags": ["Owner", "CostCenter", "Environment"]
            }
            
        else:
            # Default fallback for other/general anomalies
            return {
                "technical_reason": (
                    f"General cost anomaly detected on {resource_id} in {environment}. "
                    f"Daily cost of ${cost:.2f} is {ratio:.1f}x baseline, triggering a standard anomaly alert."
                ),
                "executive_summary": (
                    f"Anomaly alert for {resource_id}. Daily spend is ${cost:.2f} ({ratio:.1f}x baseline). "
                    f"Review recommended to determine if the spend is business-justified."
                ),
                "primary_driver_feature": "general_deviation",
                "missing_mandatory_tags": ["Owner"]
            }
