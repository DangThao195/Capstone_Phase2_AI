# Telemetry Contract — Task Force 2 (FinOps Watch)

<!-- Owner: Nhóm AI 2
     Signed by: AI Lead + CDO Leads × 2 (CDO-01, CDO-02) + Reviewer panel
     Date signed: 2026-06-25 (W11 T5)
     🔒 FREEZE — no change without formal Change Request
     Cross-ref: ai-api-contract.md v1.4.0 · deployment-contract.md v1.3.0 · docs/02_solution_design.md -->

---

## 1. Mục đích và Phạm vi

Hợp đồng này định nghĩa **các tín hiệu (signals) dữ liệu chi phí và hiệu năng** mà nhóm CDO phải thu thập từ AWS Infrastructure → chuẩn hóa → truyền tải cho AI Engine.

**Nguyên tắc cốt lõi**: CDO Platform là **source-of-truth** duy nhất. CDO **PULL** dữ liệu từ AWS CUR (S3), Cost Explorer API, và CloudWatch theo chu kỳ cố định — AI Engine không trực tiếp gọi AWS APIs.

**Phạm vi phát hiện**: Contract phục vụ 5 loại bất thường chính (reference: TF2_FINOPS_LEARNER.md):

| # | Anomaly Type | Tín hiệu chính |
|---|---|---|
| 1 | `runaway_usage` | Compute chạy 24/7, `usage_density_24h ≈ 1.0`, không giảm cuối tuần |
| 2 | `idle_resource` | Cost đều đặn, `cpu_utilization_hourly` có chuỗi < 5% liên tục > 72h (AI Engine tính) |
| 3 | `untagged_spend` | `resource_tags_user_team` rỗng, cost lớn |
| 4 | `sudden_spike` | **`cost_per_request`** nhảy bậc thang (không phải absolute cost) — xem §11.1 |
| 5 | `gradual_drift` | Trend tăng chậm nhiều tuần, chỉ visible trên `rolling_30d_avg` |

---

## 2. Schema Governance Layer

Áp dụng OpenTelemetry Schema System để quản lý tính tương thích khi CDO và AI nâng cấp không đồng bộ.

| Rule | Value |
|---|---|
| **Schema version** | `3.2.0` |
| **Schema URL** | `telemetry://finops-watch/v3.2` |
| **Backward compatible** | `true` — CDO upgrade pipeline trước AI — OK |
| **Deprecation window** | 30 ngày hỗ trợ version cũ song song |
| **Action on expiry** | `reject_request` — Từ chối payload version cũ sau grace period |
| **Change request channel** | Task Force WhatsApp + Meeting |
| **Approval** | AI Lead + CDO Leads đồng thuận |
| **Bump rule** | Breaking change → version major. Add field → minor |

> **WHY backward compatibility**: CDO-01 và CDO-02 có thể deploy pipeline khác nhau. Nếu không backward-compatible, AI Engine buộc phải hỗ trợ 2 schema cùng lúc — phức tạp không cần thiết (reference: ADR-003).

---

## 3. Request Integrity Layer

Bảo vệ chống giả mạo payload (anti-tampering) và chống tấn công phát lại (anti-replay). Khớp với `X-Payload-SHA256` và `X-Request-Timestamp` đã define trong ai-api-contract.md §4.

**JSON Schema**:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "RequestIntegrity",
  "description": "AI Engine sẽ log lại các trường này để verify integrity cho mỗi request từ CDO",
  "type": "object",
  "properties": {
    "payload_sha256": {
      "type": "string",
      "description": "SHA256 hash của JSON body — verify integrity",
      "pattern": "^[a-f0-9]{64}$"
    },
    "request_timestamp": {
      "type": "string",
      "format": "date-time",
      "description": "Thời điểm CDO tạo request (UTC ISO 8601 RFC3339)"
    },
    "signature_verified": {
      "type": "boolean",
      "description": "true nếu AWS IAM SigV4 hợp lệ"
    },
    "replay_window_seconds": {
      "type": "integer",
      "default": 300,
      "description": "Cửa sổ chấp nhận: 5 phút"
    }
  },
  "required": ["payload_sha256", "request_timestamp", "signature_verified"]
}
```

**Rule**: Nếu `abs(now - request_timestamp) > 300s` → Reject `400 Bad Request` + log `ERR_REPLAY_DETECTED`.

> **WHY 300s**: Tradeoff giữa độ trễ CDO pipeline (~30s bình thường) và cửa sổ tấn công replay. 5 phút đủ headroom cho network jitter mà không mở quá rộng cho replay attack.

---

## 4. Tenant Context & Idempotency

Khớp 1:1 với `X-Tenant-Id` và `X-Idempotency-Key` trong ai-api-contract.md §4.

**JSON Schema**:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "TenantContext",
  "description": "CDO PHẢI gửi kèm các trường này trong mỗi request để AI Engine routing multi-tenant",
  "type": "object",
  "properties": {
    "tenant_id": {
      "type": "string",
      "format": "uuid",
      "description": "Linked Account ID ánh xạ → UUID"
    },
    "account_id": {
      "type": "string",
      "pattern": "^[0-9]{12}$",
      "description": "AWS Linked Account ID (e.g. 200000000012)"
    },
    "account_name": {
      "type": "string",
      "description": "e.g. prod-core, staging, ml-research"
    },
    "correlation_id": {
      "type": "string",
      "format": "uuid",
      "description": "Trace ID xuyên suốt E2E request chain"
    },
    "idempotency_key": {
      "type": "string",
      "pattern": "^[a-f0-9-]{36}:[0-9]{4}-[0-9]{2}-[0-9]{2}:[a-z0-9-]+$",
      "description": "Format: tenant_id:billing_period_date:batch_type (reference: API Contract §4)"
    },
    "ttl_expiry": {
      "type": "integer",
      "description": "Unix epoch seconds — DynamoDB TTL tự xóa sau 24h"
    }
  },
  "required": ["tenant_id", "account_id", "account_name", "correlation_id", "idempotency_key"]
}
```

**Idempotency Store — DynamoDB (preferred over S3)**

> [!IMPORTANT]
> **v3.2.0:** Idempotency chuyển từ S3 Put sang **DynamoDB conditional write** để đáp ứng P99 latency `/v1/detect` < 300ms. S3 PutObject (~50–200ms) không phù hợp cho hot path.

| Attribute | Value |
|---|---|
| **Table** | `finops-idempotency-{env}` (per AI Engine deployment) |
| **Partition key** | `idempotency_key` (format §4) |
| **Attributes** | `payload_sha256`, `status` (`IN_PROGRESS` \| `COMPLETED`), `response_cache`, `created_at` |
| **TTL attribute** | `ttl_expiry` — auto-delete sau **24 giờ** |
| **Write pattern** | `ConditionExpression: attribute_not_exists(idempotency_key)` → `IN_PROGRESS`; update khi xong → `COMPLETED` |
| **Conflict** | ConditionalCheckFailedException → `409 Conflict` (đang xử lý) hoặc đọc cache (đã hoàn thành) |

**S3 Bucket Naming — Multi-CDO isolation**

S3 bucket name là **globally unique**. Hai team CDO không thể dùng chung tên bucket cố định.

| Kịch bản | Convention | Ví dụ |
|---|---|---|
| **Khác AWS account** | `company-cdo-{account_id}-telemetry` | `company-cdo-200000000010-telemetry` |
| **Cùng account, nhiều CDO** | Chia **namespace prefix** trong bucket chung | `idempotency/cdo-01/`, `idempotency/cdo-02/`, `cur/cdo-01/`, `features/cdo-02/` |

```
s3://company-cdo-{account_id}-telemetry/
├── idempotency/cdo-01/{idempotency_key}.json   ← fallback audit only; hot path = DynamoDB
├── idempotency/cdo-02/
├── cur/{YYYY-MM-DD}.json.gz
└── features/{resource_id}/{YYYY-MM-DD}.json
```

**Idempotency Rules** (khớp API Contract §3.2):
- Trùng key + cùng hash → `200 OK` kèm kết quả cũ (đọc từ DynamoDB `response_cache`)
- Trùng key + khác hash → `400 ERR_IDEMPOTENCY_MISMATCH`
- Trùng key + đang chạy (`IN_PROGRESS`) → `409 Conflict`

> **WHY composite key**: `tenant_id:YYYY-MM-DD:batch_type` đảm bảo mỗi tenant chỉ có 1 batch/ngày. `is_ad_hoc = true` bypass key này cho quét khẩn cấp (tối đa 5 lần/ngày — xem Deployment Contract §7).

---

## 5. Hybrid Ingestion Contract

Giải quyết giới hạn **10MB** của API Gateway/ALB. Khớp với `data_source_type` trong ai-api-contract.md §5.1.

**JSON Schema**:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "HybridIngestion",
  "description": "CDO chọn data_source_type phù hợp per-batch. RAW_JSON cho CE data nhỏ, S3_POINTER cho CUR data lớn",
  "type": "object",
  "properties": {
    "data_source_type": {
      "type": "string",
      "enum": ["RAW_JSON", "S3_POINTER"],
      "description": "RAW_JSON: gửi trực tiếp JSON body (≤10MB). S3_POINTER: upload file lên S3 rồi gửi URI"
    },
    "s3_bucket_uri": {
      "type": "string",
      "pattern": "^s3://company-cdo-[0-9]{12}-telemetry/.+\\.json\\.gz$",
      "description": "Bắt buộc khi data_source_type = S3_POINTER. Pattern: company-cdo-{account_id}-telemetry"
    },
    "s3_object_checksum": {
      "type": "string",
      "pattern": "^[a-f0-9]{64}$",
      "description": "SHA256 checksum file S3 — verify trước khi extract"
    }
  },
  "required": ["data_source_type"],
  "if": {
    "properties": { "data_source_type": { "const": "S3_POINTER" } }
  },
  "then": {
    "required": ["s3_bucket_uri", "s3_object_checksum"]
  }
}
```

**Constraints**:

| Parameter | Value |
|---|---|
| `raw_json_max_size_mb` | 10 |
| `s3_allowed_buckets` | `company-cdo-{account_id}-telemetry` (globally unique per AWS account) |
| `s3_namespace_prefix` | `cdo-01/`, `cdo-02/` khi nhiều CDO dùng chung account |
| `s3_allowed_extensions` | `.json.gz` |
| `s3_max_object_size_mb` | 500 |
| `s3_encryption` | `aws-kms` |

> **WHY Hybrid**: Cost Explorer data nhỏ (~50KB, 6 cột × 100 records) → RAW_JSON đủ. CUR data lớn (~5-50MB, 24K+ line items) → S3_POINTER tránh timeout. CDO chọn mode phù hợp per-batch.

---

## 6. Signal 1: `aws_cost_explorer_daily` — Macro Layer (Trends / Fallback)

Dữ liệu tổng hợp vĩ mô. Map trực tiếp với schema `cost_explorer_daily.csv` trong dataset TF2.

> [!IMPORTANT]
> **v3.2.0 — Đồng bộ dataset CSV + đề xuất CDO-P5:**
> - `aws_cost_explorer_daily` **không bắt buộc mỗi batch** — chỉ PULL khi `telemetry_delay_event = true`.
> - **Lookback window: 30 ngày** (rolling). CDO gửi toàn bộ 30 ngày mỗi batch fallback, không chỉ 1 ngày.
> - CDO **không** tính `cost_ratio_to_7d_avg`, `day_of_week`, `is_weekend` — AI Engine derive từ `date`.
> - `is_estimated` lấy **trực tiếp** từ trường `Estimated` của Cost Explorer API (`get-cost-and-usage`), không hard-code "2 ngày cuối".
> - `region` **không bắt buộc** — nullable hoặc `"global"` cho S3, CloudFront, IAM, Support.

| Attribute | Value |
|---|---|
| **Type** | Tabular aggregate (daily grain) |
| **Frequency** | **Conditional** — chỉ PULL khi `telemetry_delay_event = true`; ngược lại skip CE API |
| **Lookback window** | **30 ngày** rolling (≈ 6 accounts × ~20 services × 30 days ≈ 3,600 records/batch) |
| **Emit point** | CDO gọi `aws ce get-cost-and-usage` (TimePeriod = 30d, Granularity = DAILY) → normalize → JSON |
| **Retention** | 30 ngày hot (S3 cache), 90 ngày cold (S3) |
| **Used for** | Fallback trend detection khi CUR delay; baseline calculation khi thiếu CUR |
| **Emit SLA** | p99 < 60s từ CE API response → AI consumable |
| **API mapping** | `DetectResponse.data_confidence = LOW` khi batch dùng CE fallback |

### 6.1 JSON Schema — CDO PHẢI trả về đúng format này

Schema khớp **1:1** cột CSV: `date, linked_account_id, linked_account_name, service, service_code, region, unblended_cost, is_estimated`

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "CostExplorerDailySignal",
  "description": "Mỗi record = 1 dịch vụ × 1 ngày × 1 account × (optional) region. Map trực tiếp cost_explorer_daily.csv",
  "type": "object",
  "properties": {
    "date": {
      "type": "string",
      "format": "date",
      "description": "Ngày usage (YYYY-MM-DD)"
    },
    "linked_account_id": {
      "type": "string",
      "pattern": "^[0-9]{12}$",
      "description": "AWS Linked Account ID"
    },
    "linked_account_name": {
      "type": "string",
      "description": "Tên account: prod-core, staging, ml-research..."
    },
    "service": {
      "type": "string",
      "description": "Tên hiển thị Cost Explorer: 'Amazon Elastic Compute Cloud - Compute'"
    },
    "service_code": {
      "type": "string",
      "description": "Mã ngắn CUR: AmazonEC2, AmazonRDS, AmazonSageMaker..."
    },
    "region": {
      "type": ["string", "null"],
      "description": "AWS Region code (us-east-1, ap-southeast-1). null hoặc 'global' cho charge không gắn region (S3, CloudFront)"
    },
    "unblended_cost": {
      "type": "number",
      "minimum": 0,
      "description": "Chi phí ngày (USD) — map từ CE UnblendedCost.Amount"
    },
    "is_estimated": {
      "type": "boolean",
      "description": "Map trực tiếp từ CE ResultsByTime[].Estimated. AI Engine hạ confidence khi true"
    }
  },
  "required": [
    "date", "linked_account_id", "linked_account_name",
    "service", "service_code", "unblended_cost", "is_estimated"
  ]
}
```

**AI Engine derived features** (CDO không gửi):

| Feature | Công thức | Dùng cho |
|---|---|---|
| `day_of_week` | `date.weekday()` | Seasonality / weekend pattern |
| `is_weekend` | `day_of_week ∈ {5,6}` | `runaway_usage` detection |
| `rolling_7d_avg` | mean(`unblended_cost`, 7d) | Baseline |
| `cost_ratio_to_7d_avg` | `unblended_cost / rolling_7d_avg` | `sudden_spike` heuristic (secondary) |

> [!WARNING]
> **Naming Mismatch (đã verify với AWS thật)**: CUR dùng `service_code` (e.g. `AmazonEC2`), Cost Explorer dùng `service` (e.g. `Amazon Elastic Compute Cloud - Compute`). CDO **bắt buộc** cung cấp **cả hai trường** để join CE ↔ CUR — đây là cái bẫy thật ngoài production (reference: TF2 Dataset README line 79).

> [!IMPORTANT]
> **Dữ liệu ước tính**: Khi `is_estimated = true`, AI Engine **PHẢI** hạ confidence score và **KHÔNG** kích hoạt auto-containment. CDO gửi kèm `telemetry_delay_event = true` + `missing_resources` + `current_ce_cost_gap_usd` khi CUR chưa đồng bộ CE (xem §6.2).

> **WHY 30-day window**: Với 1 ngày data, rule "is_estimated cho 2 ngày cuối" vô nghĩa. 30 ngày cho phép AI tính rolling baseline ổn định và `is_estimated` phản ánh đúng CE API per-record.

### 6.2 CUR–CE Mismatch Signals — Bắt buộc khi `telemetry_delay_event = true`

Khi CE đã có dữ liệu nhưng CUR chưa đồng bộ (partial finalize), boolean `telemetry_delay_event = true` **không đủ** — AI cần biết **service nào** bị thiếu và **gap bao nhiêu USD** để quyết định hold detection.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "CurCeMismatchSignals",
  "description": "CDO so sánh CE vs CUR trong comparison_window → gửi kèm DetectRequest khi telemetry_delay_event = true",
  "type": "object",
  "properties": {
    "missing_resources": {
      "type": "array",
      "items": { "type": "string" },
      "description": "Danh sách line_item_product_code có trong CE nhưng chưa có trong CUR. VD: ['AmazonRDS', 'AmazonDynamoDB']"
    },
    "current_ce_cost_gap_usd": {
      "type": "number",
      "minimum": 0,
      "description": "Tổng CE cost (USD) của các service trong missing_resources − phần đã có trong CUR. Gap càng lớn → càng nên hold detection"
    },
    "comparison_window": {
      "type": "object",
      "properties": {
        "start_date": { "type": "string", "format": "date" },
        "end_date":   { "type": "string", "format": "date" }
      },
      "required": ["start_date", "end_date"]
    }
  },
  "required": ["missing_resources", "current_ce_cost_gap_usd", "comparison_window"]
}
```

**Ví dụ payload:**

```json
{
  "telemetry_delay_event": true,
  "missing_resources": ["AmazonRDS", "AmazonDynamoDB", "AmazonSageMaker"],
  "current_ce_cost_gap_usd": 3550.0,
  "comparison_window": {
    "start_date": "2026-06-24",
    "end_date": "2026-06-24"
  },
  "aws_cost_explorer_daily": [ "...30-day CE records..." ]
}
```

**AI Engine hành vi:**

| `current_ce_cost_gap_usd` | Hành vi |
|---|---|
| < 1% tổng CE daily | Tiếp tục detect với `data_confidence: LOW`, exclude `missing_resources` khỏi scoring |
| ≥ 1% tổng CE daily | **Hold detection** cho các service trong `missing_resources`; alert-only cho phần còn lại |
| ≥ 10% tổng CE daily | **Suspend batch** — retry sau 1h |

---

## 7. Signal 2: `aws_cur_line_items` — Micro Layer (Facts)

Dữ liệu vi mô cấp tài nguyên. Map trực tiếp với schema `cur_line_items.csv`. **Đây là nguồn sự thật (source of truth) cho detection** (reference: TF2 Dataset README line 63).

| Attribute | Value |
|---|---|
| **Type** | Tabular CUR 2.0 resource-level (daily grain) |
| **Frequency** | PULL 1 lần/ngày sau CE signal |
| **Emit point** | CDO đọc S3 CUR manifest → Athena query → JSON/gz → truyền qua S3_POINTER |
| **Retention** | 7 ngày hot, 90 ngày cold (compliance) |
| **Used for** | Resource-level anomaly detection, drill-down RCA, containment targeting |
| **Emit SLA** | p99 < 300s (Athena query + compress + upload) |
| **Volume SLA** | ~500-25K records/batch (TF2 dataset: 24,533 line items / 92 days ≈ 267/day) |

### 7.1 JSON Schema — CDO PHẢI trả về đúng format này

Schema khớp **1:1** cột CSV `cur_line_items.csv` + trường derived `usage_density_24h` (CDO tính).

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "CURLineItemSignal",
  "description": "Mỗi record = 1 tài nguyên AWS × 1 ngày. Nguồn sự thật cho anomaly detection",
  "type": "object",
  "properties": {
    "bill_billing_period_start_date": {
      "type": "string",
      "format": "date-time",
      "description": "Đầu billing period (YYYY-MM-01T00:00:00Z)"
    },
    "bill_payer_account_id": {
      "type": "string",
      "pattern": "^[0-9]{12}$",
      "description": "Management/payer account ID"
    },
    "line_item_usage_account_id": {
      "type": "string",
      "pattern": "^[0-9]{12}$",
      "description": "Linked account ID"
    },
    "line_item_usage_account_name": {
      "type": "string",
      "description": "prod-core, staging, ml-research..."
    },
    "line_item_line_item_type": {
      "type": "string",
      "enum": ["Usage", "Tax", "Fee", "Credit", "Refund", "RIFee", "SavingsPlanRecurringFee"],
      "description": "Loại line item CUR 2.0"
    },
    "line_item_usage_start_date": {
      "type": "string",
      "format": "date-time",
      "description": "Ngày phát sinh usage (RFC3339 UTC, daily grain)"
    },
    "line_item_usage_end_date": {
      "type": "string",
      "format": "date-time",
      "description": "Kết thúc usage period"
    },
    "line_item_product_code": {
      "type": "string",
      "description": "Service code: AmazonEC2, AmazonRDS, AmazonS3..."
    },
    "line_item_usage_type": {
      "type": "string",
      "description": "BoxUsage:m5.2xlarge, TimedStorage-ByteHrs..."
    },
    "line_item_operation": {
      "type": "string",
      "description": "RunInstances, CreateDBInstance..."
    },
    "line_item_resource_id": {
      "type": ["string", "null"],
      "description": "ARN/Instance ID. null cho account-level hoặc global charge (Tax, Support, một số DataTransfer)"
    },
    "line_item_usage_amount": {
      "type": "number",
      "minimum": 0,
      "description": "Khối lượng hoạt động (hours, GB, requests)"
    },
    "pricing_unit": {
      "type": "string",
      "enum": ["Hrs", "GB", "GB-Mo", "Requests", "Queries"],
      "description": "Đơn vị tính giá"
    },
    "line_item_unblended_rate": {
      "type": "number",
      "minimum": 0,
      "description": "Đơn giá ($/unit)"
    },
    "line_item_unblended_cost": {
      "type": "number",
      "minimum": 0,
      "description": "*** Nguồn sự thật cho detection *** Chi phí thực tế (USD)"
    },
    "line_item_currency_code": {
      "type": "string",
      "default": "USD"
    },
    "product_product_name": {
      "type": "string",
      "description": "Tên hiển thị sản phẩm"
    },
    "product_region_code": {
      "type": ["string", "null"],
      "description": "Region của resource. null cho global service"
    },
    "product_instance_type": {
      "type": ["string", "null"],
      "description": "m5.2xlarge, db.r5.2xlarge... null nếu không áp dụng"
    },
    "usage_density_24h": {
      "type": "number",
      "minimum": 0.0,
      "maximum": 1.0,
      "description": "CDO derived: usage_hours/24. 1.0 = chạy 24/24. Optional nhưng khuyến khích cho compute"
    },
    "resource_tags_user_environment": {
      "type": "string",
      "enum": ["prod", "prod-core", "prod-payments", "staging", "dev", "sandbox", "ml-research", "data-analytics"],
      "description": "Tag môi trường — quyết định containment strategy"
    },
    "resource_tags_user_owner": {
      "type": ["string", "null"],
      "description": "Người sở hữu. null = chưa gán"
    },
    "resource_tags_user_team": {
      "type": ["string", "null"],
      "description": "Squad quản lý. null/empty = untagged_spend (anomaly type 3)"
    },
    "resource_tags_user_cost_center": {
      "type": ["string", "null"],
      "description": "CC-2001, CC-3001..."
    }
  },
  "required": [
    "line_item_usage_start_date", "line_item_usage_account_id",
    "line_item_product_code", "line_item_usage_type",
    "line_item_usage_amount", "pricing_unit",
    "line_item_unblended_cost", "resource_tags_user_environment"
  ]
}
```

> [!NOTE]
> **`line_item_resource_id` không required**: Nhiều line item là account/service-level (Tax, Support, một phần DataTransfer) — không gắn resource cụ thể. Detection aggregate theo `line_item_product_code` + `line_item_usage_account_id` khi `resource_id = null`.

### 7.2 Athena Query tối ưu (CDO bắt buộc partition + chỉ quét window cần thiết)

```sql
SELECT bill_billing_period_start_date, line_item_usage_start_date,
       line_item_usage_account_id, line_item_product_code,
       line_item_resource_id, line_item_unblended_cost,
       resource_tags_user_team, resource_tags_user_environment
FROM "cur2_database"."cur2_table"
WHERE bill_billing_period_start_date = DATE_FORMAT(CURRENT_DATE, '%Y-%m-01 00:00:00')
  AND line_item_usage_start_date >= DATE_ADD('day', -2, CURRENT_DATE)
```

> **WHY `unblended_cost` not `usage_amount`**: Daily `usage_amount` dao động nhẹ quanh 24h do nhiễu — bình thường trong CUR. `unblended_cost` là tín hiệu ổn định hơn cho detection (reference: README line 68).

---

## 8. Signal 3: `resource_utilization_metrics` — CloudWatch Layer

Tín hiệu hiệu năng vật lý. **Optional** — AI Engine vẫn detect được nếu thiếu (CUR-only mode) nhưng `confidence *= 0.5`.

> [!IMPORTANT]
> **v3.1.0 — Đồng bộ ai-api-contract.md v1.4.0 (CDO-P4):**
> CDO **không còn** tính `idle_hours_continuous`. Gửi `cpu_utilization_hourly` (mảng 24 phần tử). AI Engine tự tính chuỗi idle.

| Attribute | Value |
|---|---|
| **Type** | CloudWatch Metrics |
| **Frequency** | PULL 1 lần/ngày (aggregate 24h period) |
| **Used for** | Xác nhận anomaly, giảm false positive, confidence scoring |
| **Fallback** | Nếu CloudWatch không available → AI Engine vẫn chạy CUR-only detection nhưng `confidence *= 0.5` |

### 8.1 JSON Schema — CDO PHẢI trả về đúng format này

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "ResourceUtilizationMetrics",
  "description": "v3.1.0: CDO gửi cpu_utilization_hourly thô; AI Engine tính idle_hours_continuous",
  "type": "object",
  "properties": {
    "resource_id": {
      "type": "string",
      "description": "Khớp với line_item_resource_id trong CUR signal"
    },
    "cpu_percent": {
      "type": "number",
      "minimum": 0,
      "maximum": 100,
      "description": "CPUUtilization avg 24h (EC2/RDS/ECS)"
    },
    "cpu_utilization_hourly": {
      "type": "array",
      "description": "Mảng 24 phần tử — CPU% trung bình theo giờ UTC (index 0 = 00:00)",
      "items": { "type": "number", "minimum": 0, "maximum": 100 },
      "minItems": 24,
      "maxItems": 24
    },
    "memory_mib": {
      "type": "number",
      "minimum": 0,
      "description": "MemoryUtilization (MiB) — chỉ có nếu CloudWatch Agent installed"
    },
    "network_in_bytes": {
      "type": "number",
      "minimum": 0,
      "description": "NetworkIn tổng 24h (bytes)"
    },
    "network_out_bytes": {
      "type": "number",
      "minimum": 0,
      "description": "NetworkOut tổng 24h (bytes)"
    },
    "disk_io_ops": {
      "type": "number",
      "minimum": 0,
      "description": "DiskReadOps + DiskWriteOps tổng 24h"
    },
    "database_connections": {
      "type": ["integer", "null"],
      "minimum": 0,
      "description": "RDS DatabaseConnections avg 24h. null nếu không phải RDS"
    },
    "gpu_utilization": {
      "type": ["number", "null"],
      "minimum": 0,
      "maximum": 100,
      "description": "GPU Core usage % (SageMaker ml-research). null nếu không có GPU"
    }
  },
  "required": ["resource_id"]
}
```

> [!NOTE]
> **v3.2.0 — Metrics theo resource type**: Chỉ `resource_id` bắt buộc. `cpu_percent`, `cpu_utilization_hourly`, `network_in_bytes`, `network_out_bytes` là **optional** — nhiều resource (S3, Lambda cold, RDS storage) không có CPU/network metrics. AI Engine chạy CUR-only mode với `confidence *= 0.5` khi thiếu metrics cho resource đó.

> **DEPRECATED v3.1.0:** Trường `idle_hours_continuous` đã bị loại khỏi contract. AI Engine tính từ `cpu_utilization_hourly` theo quy tắc: chuỗi con liên tục có giá trị < 5% kéo dài > 72h → signal `idle_resource`.

> **WHY fallback khi mất CloudWatch**: Reliability principle — CUR data là "đủ" để detect, CloudWatch chỉ "tăng confidence". Không block detection vì thiếu metric phụ (reference: 02_solution_design.md §5 Risk mitigation).

---

## 9. Resource Identity Contract

Chuẩn hóa định danh tài nguyên theo OpenTelemetry semantic conventions. Dùng cho multi-tenant routing + RCA drill-down.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "ResourceIdentity",
  "description": "CDO chuẩn hóa resource identity từ CUR + CloudTrail → gửi kèm theo mỗi batch",
  "type": "object",
  "properties": {
    "resource_id":    { "type": "string", "description": "line_item_resource_id (ARN hoặc instance ID)" },
    "resource_type":  { "type": "string", "description": "e.g. aws:ec2:instance, aws:rds:db, aws:sagemaker:notebook" },
    "aws_service":    { "type": "string", "description": "line_item_product_code" },
    "account_id":     { "type": "string", "pattern": "^[0-9]{12}$" },
    "account_name":   { "type": "string" },
    "region":         { "type": "string" },
    "environment":    { "type": "string", "enum": ["prod", "prod-core", "prod-payments", "staging", "dev", "sandbox", "ml-research", "data-analytics"] },
    "owner":          { "type": ["string", "null"] },
    "team":           { "type": ["string", "null"] },
    "cost_center":    { "type": ["string", "null"] }
  },
  "required": ["resource_id", "resource_type", "aws_service", "account_id", "region", "environment"]
}
```

---

## 10. Resource Lineage Contract

Truy vết nguồn gốc tài nguyên. Hỗ trợ RCA nhân quả: `Cost spike → deployment abc123 → commit 84fd2a → team-ml`.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "ResourceLineage",
  "description": "CDO lấy từ CloudTrail/CloudFormation/CI-CD pipeline → optional nhưng tăng chất lượng RCA",
  "type": "object",
  "properties": {
    "deployment_id":    { "type": ["string", "null"], "description": "CloudFormation StackId hoặc ECS deployment ID" },
    "git_sha":          { "type": ["string", "null"], "description": "Commit hash tạo tài nguyên" },
    "pipeline_run_id":  { "type": ["string", "null"], "description": "CI/CD pipeline execution ID" },
    "created_by":       { "type": ["string", "null"], "description": "IAM User/Role ARN" },
    "created_at":       { "type": "string", "format": "date-time", "description": "CloudTrail CreateTime" },
    "ttl_expiry":       { "type": ["string", "null"], "format": "date-time", "description": "Ngày hết hạn theo kế hoạch" }
  },
  "required": ["created_at"]
}
```

> **WHY lineage**: Khi AI Engine phát hiện anomaly, nếu biết `created_by: team-ml, deployment_id: sagemaker-training-job-42`, RCA reasoning sẽ chính xác hơn so với chỉ biết "resource X tốn tiền" (reference: Google Cloud SRE lineage patterns).

---

## 11. Business Context Signals — False Positive Reduction

Loại bỏ **3 bẫy False Positive** trong dataset: flash-sale, migration, load test (reference: TF2 Dataset README §Bẫy FP).

Logic: `cost ↑ + traffic ↑ = normal growth` | `cost ↑ + traffic flat = anomaly`

> [!IMPORTANT]
> **v3.2.0:** `traffic_volume` chuyển từ optional sang **bắt buộc** mỗi batch. Không có traffic → không thể phân biệt benign surge (flash sale) vs true anomaly.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "BusinessContext",
  "description": "CDO gửi kèm mỗi DetectRequest. traffic_volume bắt buộc để normalize cost",
  "type": "object",
  "properties": {
    "linked_account_id": {
      "type": "string",
      "pattern": "^[0-9]{12}$",
      "description": "Scope traffic_volume theo linked account — 1 business_context per account per batch"
    },
    "traffic_volume": {
      "type": "number",
      "minimum": 0,
      "description": "Request volume aggregate 24h. Xem §11.2 cho nguồn dữ liệu."
    },
    "traffic_source": {
      "type": "string",
      "enum": ["ALB", "CloudFront", "ApiGateway", "Synthetic", "Mixed"],
      "description": "Nguồn metric traffic. Synthetic = backtest từ TF2 dataset"
    },
    "active_users":   { "type": "integer", "minimum": 0, "description": "Concurrent active users" },
    "orders_count":   { "type": "integer", "minimum": 0, "description": "Transaction count 24h" },
    "campaign_flag":  { "type": "boolean", "description": "true = đang có marketing campaign" },
    "load_test_flag": { "type": "boolean", "description": "true = đang chạy performance test" },
    "migration_flag": { "type": "boolean", "description": "true = đang migration data" }
  },
  "required": ["linked_account_id", "traffic_volume", "traffic_source", "campaign_flag", "load_test_flag", "migration_flag"]
}
```

### 11.2 Traffic Volume Collection Spec — CDO Implementation Guide

> **v3.2.0 production gate:** Contract bắt buộc `traffic_volume` — section này định nghĩa **cách CDO thu thập** để team implement được.

**Granularity:** 1 `business_context` object **per linked account per batch day**. DetectRequest gửi mảng `business_contexts[]` hoặc object đơn nếu single-tenant batch.

| Field | Rule |
|---|---|
| `linked_account_id` | Khớp `line_item_usage_account_id` trong CUR |
| `traffic_volume` | Sum `RequestCount` 24h UTC (00:00–23:59) |
| `traffic_source` | Metric nguồn chính — ghi rõ để audit |

**Nguồn dữ liệu (ưu tiên):**

| Priority | Source | CloudWatch Metric | Namespace |
|---|---|---|---|
| 1 | Application Load Balancer | `RequestCount` | `AWS/ApplicationELB` |
| 2 | CloudFront | `Requests` | `AWS/CloudFront` |
| 3 | API Gateway | `Count` | `AWS/ApiGateway` |
| 4 | **Capstone fallback** | Synthetic từ script | `traffic_source: Synthetic` |

**CDO Athena/CE query pattern (ALB):**

```sql
-- Pseudo: CDO aggregate per account per day
SELECT linked_account_id,
       SUM(request_count) AS traffic_volume
FROM alb_access_logs_normalized
WHERE date = :batch_date
GROUP BY linked_account_id
```

**Edge cases:**

| Case | `traffic_volume` | Detection mode |
|---|---|---|
| Batch/offline workload (no HTTP) | `0` | Dùng absolute `daily_cost` + `usage_density_24h` |
| Multi-ALB same account | Sum tất cả ALB | `traffic_source: Mixed` |
| TF2 backtest (no traffic CSV) | Generate synthetic | `traffic_source: Synthetic` — xem `tools/generate_synthetic_traffic.py` |

**Synthetic traffic cho backtest:** Script correlate traffic với CE daily cost (ρ ≈ 0.85 cho benign events). File output: `capstone-phase2/data/tf2-finops/synthetic_traffic_daily.csv`.

### 11.1 Feature Engineering — `cost_per_request` (AI Engine tính, không do CDO gửi)

Trước khi đưa vào Isolation Forest, AI Engine **PHẢI** derive feature chuẩn hóa theo traffic:

```python
# Per linked_account_id × day (join business_context by linked_account_id)
daily_cost = sum(line_item_unblended_cost)  # filter by account + date
cost_per_request = daily_cost / max(traffic_volume, 1)
```

| Scenario | `daily_cost` | `traffic_volume` | `cost_per_request` | Kết luận |
|---|---|---|---|---|
| Flash sale (benign) | $100 → $300 | 10K → 30K | ~$0.01 (ổn định) | **Không anomaly** |
| True leak | $100 → $300 | 10K → 10K | $0.01 → $0.03 | **Anomaly** |
| Idle resource | $50 (đều) | 0 | N/A — dùng `usage_density_24h` | `idle_resource` |

**Detection rule (v3.2.0):**

- Primary scoring feature: **`cost_per_request`** (và ratio vs 7d rolling avg của nó)
- Secondary: absolute `daily_cost` chỉ dùng khi `traffic_volume = 0` (batch/offline workload)
- Nếu `campaign_flag = true` hoặc `cost_per_request` ổn định ±15% → classify `BENIGN_DEMAND_SURGE`, không auto-contain

> **Dataset note**: TF2 CSV không có cột `traffic_volume`. Chạy `python AIO2/tools/generate_synthetic_traffic.py` → `synthetic_traffic_daily.csv` correlated với benign events (flash sale B2).

> **WHY business context**: Dataset TF2 có 3 sự kiện benign trông y anomaly nhưng **hợp lệ**. Detector chỉ nhìn absolute cost sẽ vỡ ngưỡng FP ≤10% (reference: README line 119).

---

## 12. Time Integrity Contract

Bảo vệ quan hệ nhân quả (causality) trong hệ thống phân tán. **v3.1.0 tách hai lớp kiểm tra thời gian** — khớp ai-api-contract.md §3.1.

| Lớp | Trường kiểm tra | Ngưỡng | Hành vi khi vượt |
|---|---|---|---|
| **Request Timestamp** | `X-Request-Timestamp` (header API) | ≤ **300 giây** | Reject `400 ERR_REPLAY_DETECTED` |
| **Data Timestamp** | `line_item_usage_start_date` (CUR payload) | ≤ **36 giờ** | Chấp nhận bình thường (CUR delay 8–24h là expected) |

> [!WARNING]
> **Breaking change từ v3.0.0:** Ngưỡng `max_allowed_skew_ms: 10000` (10 giây) trên `source_timestamp` vs `ingestion_timestamp` **đã bị loại bỏ**. Không reject batch CUR vì data age — chỉ reject request replay.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "TimeIntegrity",
  "description": "v3.1.0: Request replay protection (300s) tách khỏi CUR data latency (36h acceptable)",
  "type": "object",
  "properties": {
    "request_timestamp": {
      "type": "string",
      "format": "date-time",
      "description": "X-Request-Timestamp từ CDO — reject nếu skew > 300s so với server clock"
    },
    "line_item_usage_start_date": {
      "type": "string",
      "format": "date-time",
      "description": "Thời điểm AWS ghi nhận usage — chấp nhận delay đến 36h"
    },
    "collector_timestamp": {
      "type": "string",
      "format": "date-time",
      "description": "Thời điểm CDO Collector thu thập (audit only, không reject)"
    },
    "ingestion_timestamp": {
      "type": "string",
      "format": "date-time",
      "description": "Thời điểm AI Engine nhận dữ liệu (AI tự gán)"
    },
    "data_age_hours": {
      "type": "number",
      "minimum": 0,
      "description": "abs(ingestion - line_item_usage_start_date) in hours — informational"
    }
  },
  "required": ["request_timestamp"]
}
```

---

## 13. Telemetry Quality Contract

AI Engine tự đánh giá dữ liệu trước khi ra quyết định. **Nếu `completeness_score < 0.8` → AI forced into DRY-RUN.**

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "TelemetryQuality",
  "description": "AI Engine tự tính các score này SAU KHI nhận data từ CDO. Không yêu cầu CDO gửi",
  "type": "object",
  "properties": {
    "cur_status":              { "type": "string", "enum": ["HEALTHY", "DELAYED", "MISSING"] },
    "cloudwatch_status":       { "type": "string", "enum": ["HEALTHY", "DEGRADED", "MISSING"] },
    "cost_explorer_status":    { "type": "string", "enum": ["HEALTHY", "STALE"] },
    "completeness_score":      { "type": "number", "minimum": 0, "maximum": 1, "description": "Tỷ lệ trường bắt buộc có giá trị hợp lệ" },
    "freshness_score":         { "type": "number", "minimum": 0, "maximum": 1, "description": "1.0 - (data_age_hours / 24)" },
    "integrity_score":         { "type": "number", "minimum": 0, "maximum": 1, "description": "SHA256 checksum match rate" },
    "delay_score":             { "type": "number", "minimum": 0, "maximum": 1, "description": "Điểm phạt nếu CUR bị trễ > 12h" },
    "is_forced_dry_run":       { "type": "boolean", "description": "Internal flag — map sang API response data_confidence: LOW khi true" },
    "data_confidence":         { "type": "string", "enum": ["HIGH", "LOW"], "description": "HIGH = CUR đầy đủ; LOW = CE fallback hoặc forced dry-run (ai-api-contract.md §5.1)" }
  },
  "required": ["completeness_score", "is_forced_dry_run", "data_confidence"]
}
```

> **WHY forced DRY-RUN**: Nếu AI Engine detect trên dữ liệu thiếu → sinh False Positive → trigger auto-containment sai → tắt resource production → outage. Forced dry-run là safety net (reference: 01_requirements.md §4 Constraints — NEVER terminate prod). AI Engine trả `data_confidence: LOW` trong `DetectResponse` để CDO biết kết quả ở chế độ degraded — **không dùng trường `is_dry_run` ở API layer** (v1.2.0).

---

## 14. Quota Telemetry Contract

Hệ thống can thiệp `quota-cap` cần biết headroom trước khi áp trần.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "QuotaSignals",
  "description": "CDO gọi AWS Service Quotas API → aggregate → gửi kèm batch",
  "type": "object",
  "properties": {
    "service_code":    { "type": "string", "description": "e.g. AmazonEC2, AmazonSageMaker" },
    "current_quota":   { "type": "number", "minimum": 0, "description": "AWS Service Quotas current limit" },
    "current_usage":   { "type": "number", "minimum": 0, "description": "Actual usage hiện tại" },
    "utilization_pct": { "type": "number", "minimum": 0, "maximum": 100, "description": "(usage / quota) × 100" },
    "headroom_pct":    { "type": "number", "minimum": 0, "maximum": 100, "description": "100 - utilization_pct" }
  },
  "required": ["service_code", "current_quota", "current_usage", "utilization_pct", "headroom_pct"]
}
```

---

## 15. Human Feedback Contract — Active Learning Loop

Continual Learning: SRE/Engineer xác nhận qua Slack → AI cập nhật pattern memory.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "HumanFeedback",
  "description": "CDO/SRE gửi qua POST /v1/feedback — AI Engine dùng để calibrate detection",
  "type": "object",
  "properties": {
    "anomaly_id": {
      "type": "string",
      "pattern": "^ANM-[0-9]{4}-[0-9]{4}[A-Z]$",
      "description": "Format: ANM-YYYY-MMDD[A-Z] (khớp API Contract §5.2)"
    },
    "reviewer_id":  { "type": "string", "description": "Email hoặc Slack User ID" },
    "verdict":      { "type": "string", "enum": ["TRUE_POSITIVE", "FALSE_POSITIVE", "BENIGN_EVENT"] },
    "reason":       { "type": "string", "description": "Giải trình lý do đánh giá" },
    "reviewed_at":  { "type": "string", "format": "date-time" }
  },
  "required": ["anomaly_id", "reviewer_id", "verdict", "reason", "reviewed_at"]
}
```

> **WHY feedback loop**: Dataset TF2 chỉ có 3 nhãn mẫu (mentor giữ đáp án). Feedback loop cho phép hệ thống calibrate sau deployment từ phản hồi thực tế (reference: Arize AI observability patterns).

---

## 16. Audit Chain — Tamper-evident Integrity

Chuỗi hash bảo vệ tính toàn vẹn kiểm toán. Audit trail lưu ≥90 ngày (reference: 01_requirements.md §4).

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "AuditChain",
  "description": "AI Engine tự generate — append-only chain. CDO không cần gửi",
  "type": "object",
  "properties": {
    "audit_id":       { "type": "string", "format": "uuid" },
    "event_hash":     { "type": "string", "pattern": "^[a-f0-9]{64}$", "description": "sha256(current_payload + previous_hash)" },
    "previous_hash":  { "type": "string", "pattern": "^[a-f0-9]{64}$", "description": "Hash bản ghi trước đó (append-only chain)" },
    "signature":      { "type": "string", "description": "Chữ ký KMS" },
    "retention_days": { "type": "integer", "default": 90 }
  },
  "required": ["audit_id", "event_hash", "previous_hash", "signature"]
}
```

---

## 17. Prometheus PromQL Queries — AI Engine Runtime Metrics

AI Engine expose metrics qua `/metrics` endpoint (OpenTelemetry Prometheus exporter, port 9090). CDO/SRE dùng các PromQL queries sau để giám sát runtime:

### 17.1 Request Throughput & Latency

```promql
# Tổng số request/phút theo endpoint
rate(http_server_requests_total{service="ai-engine"}[5m])

# P99 latency cho detection endpoint (phải < 300ms — sync detect v1.4.0)
histogram_quantile(0.99, rate(http_server_request_duration_seconds_bucket{service="ai-engine", endpoint="/v1/detect"}[5m]))
```

### 17.2 Error Rate & Availability

```promql
# Error rate (phải < 0.5%)
rate(http_server_requests_total{service="ai-engine", status=~"5.."}[5m])
  /
rate(http_server_requests_total{service="ai-engine"}[5m])

# Availability SLI (phải >= 99.5%)
1 - (
  sum(rate(http_server_requests_total{service="ai-engine", status=~"5.."}[30m]))
  /
  sum(rate(http_server_requests_total{service="ai-engine"}[30m]))
)
```

### 17.3 AI Model Performance

```promql
# Bedrock inference latency P99 (phải < 10s, nếu > 10s → fallback)
histogram_quantile(0.99, rate(ai_model_inference_duration_seconds_bucket{service="ai-engine", model="nova-pro"}[5m]))

# Model fallback rate (bao nhiêu % request phải fallback)
rate(ai_model_fallback_total{service="ai-engine"}[1h])
  /
rate(ai_model_inference_total{service="ai-engine"}[1h])

# Token usage per day (budget guardrail: < 500K tokens/day)
sum(increase(ai_model_tokens_total{service="ai-engine"}[24h]))
```

### 17.4 Telemetry Quality

```promql
# Completeness score trung bình (phải > 0.8, nếu < 0.8 → forced DRY-RUN)
avg(ai_telemetry_completeness_score{service="ai-engine"})

# Forced dry-run rate (phải = 0% trong điều kiện bình thường)
rate(ai_detection_forced_dry_run_total{service="ai-engine"}[24h])

# Data freshness — latency từ source → ingestion
histogram_quantile(0.99, rate(ai_telemetry_ingestion_delay_seconds_bucket{service="ai-engine"}[1h]))
```

### 17.5 Detection & Containment

```promql
# Anomalies detected per day
sum(increase(ai_anomalies_detected_total{service="ai-engine"}[24h]))

# Containment success rate (phải > 95%)
sum(rate(ai_containment_success_total{service="ai-engine"}[7d]))
  /
sum(rate(ai_containment_triggered_total{service="ai-engine"}[7d]))

# False positive rate (target ≤ 10% — track qua human feedback)
sum(increase(ai_feedback_total{service="ai-engine", verdict="FALSE_POSITIVE"}[30d]))
  /
sum(increase(ai_feedback_total{service="ai-engine"}[30d]))
```

### 17.6 ECS Container Health

```promql
# CPU utilization per task (scale-up trigger: > 70%)
avg(aws_ecs_cpu_utilization{cluster="tf-2-aiops-cluster", service="ai-engine"}) by (task_id)

# Memory utilization per task
avg(aws_ecs_memory_utilization{cluster="tf-2-aiops-cluster", service="ai-engine"}) by (task_id)

# Running task count (min=2, max=10)
aws_ecs_running_task_count{cluster="tf-2-aiops-cluster", service="ai-engine"}
```

---

## 18. Cross-cutting Requirements

Mọi signal payload phải comply các quy tắc sau:

| Requirement | Rule | Enforcement |
|---|---|---|
| **Tenant scoping** | Mọi payload bắt buộc có `tenant_id` | AI Engine reject payload thiếu `tenant_id` → `400 ERR_INVALID_SCHEMA` |
| **Time precision** | Timestamp RFC3339 UTC, millisecond precision | Schema validation |
| **Schema validation** | AI ingestion layer validate JSON schema | Reject malformed → log to DLQ |
| **PII** | KHÔNG được chứa PII (email/phone/name) | CDO anonymize tại ingestion layer |
| **Metric units** | cost=USD, cpu=percent, memory=MiB, network=bytes, latency=ms | Tường minh trong schema |
| **Data classification** | `pii_present: false`, `sensitivity_level: internal` | CDO enforce at ingestion |

---

## 19. Telemetry Outage Recovery Matrix

| Kịch bản | Detection | Hành vi AI Engine |
|---|---|---|
| CUR trễ (chưa cập nhật S3) | CDO gửi `telemetry_delay_event = true` + `missing_resources` + `current_ce_cost_gap_usd` + CE 30d fallback | `data_confidence: LOW`. Hold detection cho services trong `missing_resources` nếu gap ≥1%. Alert-only. Retry mỗi 1h. |
| CUR sẵn sàng (normal path) | `telemetry_delay_event = false` | CDO **không** pull CE API. Chỉ gửi `aws_cur_line_items`. `data_confidence: HIGH`. |
| Mất CloudWatch Metrics | `cloudwatch_status: MISSING` | AI chạy CUR-only detection. `confidence *= 0.5`. Containment = Dry-run/Alert-only. |
| Pipeline CDO sập hoàn toàn | Sau 26h không nhận dữ liệu | AI phát cảnh báo P1 đỏ tới Slack cả hai nhóm. |
| Dữ liệu ước tính (`is_estimated`) | `is_estimated = true` | AI giảm `confidence_score`. Không kích hoạt auto-containment. |
| Cost Explorer rate limit | `cost_explorer_status: STALE` | CDO serve từ S3 cache. AI ghi nhận `stale_data_used: true`. |

---

## Open Questions (Resolved)

- [x] **Q1**: Signal nào cần exactly-once delivery?
  - *Resolved*: At-least-once OK cho tất cả. Idempotency Key xử lý dedup. Exactly-once phức tạp không cần thiết cho batch 24h.

- [x] **Q2**: Encryption ngoài TLS chuẩn?
  - *Resolved*: TLS 1.3 in-transit đủ. At-rest dùng AWS KMS (S3 + S3). Không cần end-to-end encryption bổ sung.

---

## Related Documents

- ai-api-contract.md v1.4.0 — 6 API endpoints, Idempotency rules, DetectResponse.data_confidence.
- deployment-contract.md v1.3.0 — ECS Fargate compute, CDO IAM Boundaries, Rollback cache.
- docs/01_requirements.md — Success criteria, hard constraints, retention requirements.
- docs/02_solution_design.md — Architecture overview, component breakdown, data flow.
- docs/03_ai_engine_spec.md — Model governance, Bedrock Guardrails, Prompt engineering.
- docs/04_eval_report.md — Backtest results, failure analysis, curveball impact.
- docs/05_adrs.md — Architecture Decision Records (ADR-001 to ADR-005).

---

## 20. Telemetry State Store — S3-based Feature Store

Để hỗ trợ các thuật toán phát hiện bất thường cần lookback window (như `idle_resource` >3 ngày và `gradual_drift` >4 tuần), AI Engine sử dụng **S3-based Feature Store** trong bucket `s3://company-cdo-{account_id}-telemetry/features/`:

1. **Ghi nhận dữ liệu**: Với mỗi lượt chạy `POST /v1/detect`, AI Engine ghi vector đặc trưng hàng ngày tại:
   `s3://company-cdo-{account_id}-telemetry/features/{cdo_namespace}/{resource_id}/{YYYY-MM-DD}.json`
   (`cdo_namespace` = `cdo-01` hoặc `cdo-02` khi chia sẻ account)
   Schema tệp JSON:
   ```json
   {
     "resource_id": "string",
     "date": "YYYY-MM-DD",
     "unblended_cost": 0.0,
     "usage_amount": 0.0,
     "usage_density_24h": 0.0,
     "traffic_volume": 0.0,
     "cost_per_request": 0.0,
     "cpu_percent": 0.0
   }
   ```
2. **Đọc dữ liệu lookback**: List + get các file trong `features/{cdo_namespace}/{resource_id}/` — 3 ngày (`idle_resource`) hoặc 30 ngày (`gradual_drift`, `cost_per_request` baseline).
