"""
S3 Client — CUR File Reader
==============================
Đọc file CUR từ S3 khi CDO gửi data_source_type = S3_POINTER.
Theo telemetry-contract v3.2.0 §5:
  - Bucket: company-cdo-{account_id}-telemetry
  - Format: .json.gz
  - Checksum: SHA256 verify trước khi xử lý
"""

import gzip
import hashlib
import io
import json
import logging
from typing import List, Optional

logger = logging.getLogger("finops-engine.storage.s3")

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
    _HAS_BOTO3 = True
except ImportError:
    _HAS_BOTO3 = False
    logger.warning("boto3 not installed — S3 client disabled.")


class S3Client:
    """
    Đọc file CUR .json.gz từ S3 bucket của CDO.
    """

    def __init__(self, region: str = "ap-southeast-1", enabled: bool = False):
        self.enabled = enabled and _HAS_BOTO3
        self._client = None

        if self.enabled:
            try:
                self._client = boto3.client("s3", region_name=region)
                logger.info("S3 client initialized.")
            except (NoCredentialsError, Exception) as e:
                logger.warning(f"S3 client init failed: {e}. S3_POINTER will be rejected.")
                self.enabled = False
        else:
            logger.info("S3 client disabled via config or missing boto3.")

    def download_cur_records(
        self,
        bucket: str,
        key: str,
        expected_checksum: Optional[str] = None,
    ) -> Optional[List[dict]]:
        """
        Tải file CUR .json.gz từ S3, verify checksum, parse JSON.

        Args:
            bucket: Tên S3 bucket (e.g., company-cdo-093490087544-telemetry)
            key: Object key (e.g., cdo-01/cur/2026-06-29.json.gz)
            expected_checksum: SHA256 hex string để verify integrity (optional)

        Returns:
            List of CUR line item dicts, hoặc None nếu lỗi.
        """
        if not self.enabled:
            logger.warning("S3 client not enabled. Cannot process S3_POINTER.")
            return None

        try:
            logger.info(f"Downloading CUR from s3://{bucket}/{key}")
            response = self._client.get_object(Bucket=bucket, Key=key)
            raw_bytes = response["Body"].read()

            # Verify checksum nếu có
            if expected_checksum:
                actual_hash = hashlib.sha256(raw_bytes).hexdigest()
                if actual_hash != expected_checksum:
                    logger.error(
                        f"Checksum mismatch! Expected={expected_checksum}, "
                        f"Actual={actual_hash}. Rejecting CUR file."
                    )
                    return None
                logger.debug("Checksum verified OK.")

            # Decompress .json.gz
            if key.endswith(".gz"):
                decompressed = gzip.decompress(raw_bytes)
            else:
                decompressed = raw_bytes

            # Parse JSON (có thể là JSON array hoặc JSONL)
            text = decompressed.decode("utf-8")
            try:
                data = json.loads(text)
                if isinstance(data, list):
                    logger.info(f"Parsed {len(data)} CUR records from S3.")
                    return data
                elif isinstance(data, dict):
                    # Nếu là single object, wrap thành list
                    return [data]
            except json.JSONDecodeError:
                # Thử parse JSONL (mỗi dòng là 1 JSON object)
                records = []
                for line in text.strip().split("\n"):
                    if line.strip():
                        records.append(json.loads(line))
                logger.info(f"Parsed {len(records)} CUR records (JSONL) from S3.")
                return records

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "NoSuchBucket":
                logger.error(f"S3 bucket not found: {bucket}")
            elif error_code == "NoSuchKey":
                logger.error(f"S3 object not found: {key}")
            elif error_code == "AccessDenied":
                logger.error(f"Access denied to s3://{bucket}/{key}. Check IAM cross-account role.")
            else:
                logger.error(f"S3 ClientError: {e}")
            return None
        except Exception as e:
            logger.error(f"S3 unexpected error: {e}")
            return None

    @staticmethod
    def parse_s3_uri(uri: str) -> tuple:
        """
        Parse s3://bucket/key URI → (bucket, key).
        Theo telemetry-contract §5: s3_bucket_uri format.
        """
        if not uri.startswith("s3://"):
            raise ValueError(f"Invalid S3 URI: {uri}")
        parts = uri[5:].split("/", 1)
        bucket = parts[0]
        key = parts[1] if len(parts) > 1 else ""
        return bucket, key

    def is_healthy(self) -> bool:
        """Health check: kiểm tra S3 client khả dụng."""
        if not self.enabled:
            return True  # Disabled = not a dependency
        try:
            self._client.list_buckets()
            return True
        except Exception:
            return False
