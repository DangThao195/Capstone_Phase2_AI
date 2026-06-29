"""
DynamoDB Client — Idempotency Store + Feature Store
=====================================================
Kết nối 2 bảng DynamoDB theo deployment-contract v1.4.0:
  - finops-idempotency-{env}: chống trùng request, cache response (AI R/W)
  - finops-feature-store-{env}: feature vectors cho XGBoost hot path (AI Read-only)

Graceful degradation: nếu DynamoDB không khả dụng → fallback in-memory.
"""

import json
import time
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger("finops-engine.storage.dynamodb")

# Try importing boto3
try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError, EndpointConnectionError
    _HAS_BOTO3 = True
except ImportError:
    _HAS_BOTO3 = False
    logger.warning("boto3 not installed — DynamoDB client will use in-memory fallback.")


class DynamoDBClient:
    """
    Manages connections to finops-idempotency-{env} and finops-feature-store-{env}.
    """

    def __init__(self, idempotency_table: str, feature_store_table: str, region: str = "ap-southeast-1", enabled: bool = False):
        self.idempotency_table = idempotency_table
        self.feature_store_table = feature_store_table
        self.enabled = enabled and _HAS_BOTO3
        self._client = None
        self._fallback_store: Dict[str, dict] = {}  # in-memory fallback

        if self.enabled:
            try:
                self._client = boto3.resource("dynamodb", region_name=region)
                # Validate connection by describing tables (lazy — only on first call)
                logger.info(f"DynamoDB client initialized. Tables: {idempotency_table}, {feature_store_table}")
            except (NoCredentialsError, EndpointConnectionError) as e:
                logger.warning(f"DynamoDB connection failed: {e}. Falling back to in-memory.")
                self.enabled = False
        else:
            logger.info("DynamoDB disabled via config. Using in-memory store.")

    # =========================================================================
    # Idempotency Store (finops-idempotency-{env})
    # =========================================================================

    def put_idempotency(self, idempotency_key: str, payload_sha256: str, response_data: dict) -> bool:
        """
        Conditional write: chỉ ghi nếu key chưa tồn tại.
        Returns True nếu ghi thành công (request mới), False nếu key đã tồn tại (duplicate).
        """
        if not self.enabled:
            if idempotency_key in self._fallback_store:
                return False
            self._fallback_store[idempotency_key] = {
                "status": "COMPLETED",
                "payload_sha256": payload_sha256,
                "response_cache": response_data,
                "created_at": int(time.time()),
                "ttl_expiry": int(time.time()) + 86400,  # 24h TTL
            }
            return True

        try:
            table = self._client.Table(self.idempotency_table)
            table.put_item(
                Item={
                    "idempotency_key": idempotency_key,
                    "status": "COMPLETED",
                    "payload_sha256": payload_sha256,
                    "response_cache": json.dumps(response_data, default=str),
                    "created_at": int(time.time()),
                    "ttl_expiry": int(time.time()) + 86400,
                },
                ConditionExpression="attribute_not_exists(idempotency_key)",
            )
            logger.debug(f"Idempotency write OK: {idempotency_key}")
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                logger.debug(f"Idempotency duplicate: {idempotency_key}")
                return False
            logger.error(f"DynamoDB put_idempotency error: {e}")
            # Fallback to in-memory on transient errors
            self._fallback_store[idempotency_key] = {
                "status": "COMPLETED",
                "payload_sha256": payload_sha256,
                "response_cache": response_data,
            }
            return True
        except Exception as e:
            logger.error(f"DynamoDB unexpected error: {e}")
            return True  # Allow request to proceed

    def get_idempotency(self, idempotency_key: str) -> Optional[dict]:
        """
        Đọc cached response cho idempotency key.
        Returns dict response_cache nếu có, None nếu không tìm thấy.
        """
        if not self.enabled:
            item = self._fallback_store.get(idempotency_key)
            if item and item.get("status") == "COMPLETED":
                return item.get("response_cache")
            return None

        try:
            table = self._client.Table(self.idempotency_table)
            result = table.get_item(Key={"idempotency_key": idempotency_key})
            item = result.get("Item")
            if item and item.get("status") == "COMPLETED":
                cached = item.get("response_cache")
                if isinstance(cached, str):
                    return json.loads(cached)
                return cached
            return None
        except Exception as e:
            logger.error(f"DynamoDB get_idempotency error: {e}")
            return None

    # =========================================================================
    # Feature Store (finops-feature-store-{env}) — Read-only
    # =========================================================================

    def get_feature_vector(self, resource_id: str, date: str) -> Optional[Dict[str, Any]]:
        """
        Đọc feature vector cho resource_id tại ngày date (YYYY-MM-DD).
        Returns dict chứa features (mảng 24h) hoặc None.
        CDO chịu trách nhiệm ghi dữ liệu vào bảng này.
        """
        if not self.enabled:
            logger.debug("Feature store not available (DynamoDB disabled).")
            return None

        try:
            table = self._client.Table(self.feature_store_table)
            result = table.get_item(
                Key={
                    "resource_id": resource_id,
                    "date": date,
                }
            )
            item = result.get("Item")
            if item:
                logger.debug(f"Feature vector found: {resource_id}/{date}")
                features = item.get("features")
                if isinstance(features, str):
                    return json.loads(features)
                return features
            logger.debug(f"Feature vector not found: {resource_id}/{date}")
            return None
        except Exception as e:
            logger.error(f"DynamoDB get_feature_vector error: {e}")
            return None

    def is_healthy(self) -> bool:
        """Health check: kiểm tra kết nối DynamoDB."""
        if not self.enabled:
            return True  # In-memory fallback luôn healthy

        try:
            table = self._client.Table(self.idempotency_table)
            table.table_status  # Triggers describe_table
            return True
        except Exception:
            return False
