# Telemetry Contract — Task Force 2 (FinOps Watch)

<!-- Owner: Nhóm AI 2
     Signed by: AI Lead + CDO Leads × 2 (CDO-01, CDO-02) + Reviewer panel
     Date signed: 2026-06-25 (W11 T5)
     🔒 FREEZE — no change without formal Change Request
     Word target: 2000-3000 từ (Contract tier)
     Cross-ref: ai-api-contract.md · deployment-contract.md · docs/02_solution_design.md -->

---

## 1. Mục đích và Phạm vi

Hợp đồng này định nghĩa **các tín hiệu (signals) dữ liệu chi phí và cấu hình hạch toán** mà nhóm CDO phải thu thập từ AWS Infrastructure → chuẩn hóa → truyền tải cho AI Engine.

**Nguyên tắc cốt lõi**: CDO Platform là **source-of-truth** duy nhất. CDO **PULL** dữ liệu từ AWS CUR (S3) và Cost Explorer API theo chu kỳ cố định — AI Engine không trực tiếp gọi AWS APIs để tối ưu hóa chi phí và bảo mật hệ thống.

**Phạm vi phát hiện (Chế độ CUR-Only)**: Để tối ưu chi phí telemetry và bám sát tệp dữ liệu thực nghiệm, dự án loại bỏ lớp dữ liệu hiệu năng CloudWatch. Hệ thống tập trung bóc tách không gian dữ liệu đa biến của CUR để phục vụ phát hiện 5 loại bất thường chính:

| # | Anomaly Type | Tín hiệu chính (Mô hình CUR-Only) |
|---|---|---|
| 1 | `runaway_usage` | Dòng chi phí tăng vọt đột biến, duy trì liên tục hằng ngày không giảm kể cả ngày nghỉ cuối tuần (`is_weekend = true`). |
| 2 | `idle_resource` | Chi phí phát sinh đều đặn, liên tục kéo dài nhiều tuần nhưng tài nguyên dính cờ trống người sở hữu (`resource_tags_user_owner` rỗng) hoặc thuộc các stack thử nghiệm cũ đã quá hạn (`TTL Expiry`). |
| 3 | `untagged_spend` | Thẻ định danh đội nhóm quản lý chi phí `resource_tags_user_team` mang giá trị rỗng/null, trong khi cost phát sinh vượt baseline. |
| 4 | `sudden_spike` | Chi phí nhảy bậc thang đột ngột trong chu kỳ hạch toán, tỷ số biến động `unblended_cost` tăng vượt ngưỡng an toàn. |
| 5 | `gradual_drift` | Xu hướng tăng trưởng chi phí chậm, âm thầm qua nhiều tuần, chỉ visible khi tiến hành tính toán độ lệch tích lũy chu kỳ dài hạn. |

---

## 2. Schema Governance Layer

Áp dụng OpenTelemetry Schema System để quản lý tính tương thích khi CDO và AI nâng cấp không đồng bộ.

```yaml
schema:
  version: "3.0.0"
  schema_url: "telemetry://finops-watch/v3"
  compatibility:
    backward: true                   # CDO upgrade pipeline trước AI — OK
    deprecation_window_days: 30      # Hỗ trợ version cũ song song 30 ngày
    action_on_expiry: reject_request # Từ chối payload version cũ sau grace period
  change_request_process:
    channel: "Task Force WhatsApp + Meeting"
    approval: "AI Lead + CDO Leads đồng thuận"
    bump_rule: "Breaking change → version major. Add field → minor."
```

> **WHY backward compatibility**: CDO-01 và CDO-02 có thể deploy pipeline khác nhau. Nếu không backward-compatible, AI Engine buộc phải hỗ trợ 2 schema cùng lúc — phức tạp không cần thiết (reference: ADR-003).

---

## 3. Request Integrity Layer

Bảo vệ chống giả mạo payload (anti-tampering) và chống tấn công phát lại (anti-replay). Khớp với `X-Payload-SHA256` và `X-Request-Timestamp` đã define trong ai-api-contract.md §4.

```yaml
request_integrity:
  payload_sha256: string          # SHA256 hash của JSON body — verify integrity
  request_timestamp: rfc3339      # Thời điểm CDO tạo request (UTC ISO 8601)
  signature_verified: bool        # true nếu AWS IAM SigV4 hợp lệ
  replay_window_seconds: 300      # Cửa sổ chấp nhận: 5 phút
```

**Rule**: Nếu `abs(now - request_timestamp) > 300s` → Reject `400 Bad Request` + log `ERR_REPLAY_DETECTED`.

> **WHY 300s**: Tradeoff giữa độ trễ CDO pipeline (~30s bình thường) và cửa sổ tấn công replay. 5 phút đủ headroom cho network jitter mà không mở quá rộng cho replay attack.

---

## 4. Tenant Context & Idempotency

Khớp 1:1 với `X-Tenant-Id` và `X-Idempotency-Key` trong ai-api-contract.md §4.

```yaml
tenant_context:
  tenant_id: uuid_v4              # Linked Account ID ánh xạ → UUID
  account_id: string              # AWS Linked Account ID (e.g. 200000000012)
  account_name: string            # e.g. prod-core, staging, ml-research
  correlation_id: uuid_v4         # Trace ID xuyên suốt E2E request chain
  idempotency_key: string         # Format: tenant_id:YYYY-MM-DD (reference: API Contract §4)
  ttl_expiry: rfc3339             # Hết hạn sau 24h trong DynamoDB
```

**Idempotency Rules** (khớp API Contract §4 quy tắc nâng cao):
- Trùng key + cùng hash → `200 OK` kèm kết quả cũ
- Trùng key + khác hash → `400 ERR_IDEMPOTENCY_MISMATCH`
- Trùng key + đang chạy → `409 Conflict`

> **WHY composite key**: `tenant_id:YYYY-MM-DD` đảm bảo mỗi tenant chỉ có 1 batch/ngày. `is_ad_hoc = true` bypass key này cho quét khẩn cấp (tối đa 5 lần/ngày — xem Deployment Contract §7).

---

## 5. Hybrid Ingestion Contract

Giải quyết giới hạn **10MB** của API Gateway/ALB. Khớp với `data_source_type` trong ai-api-contract.md §5.1.

```yaml
hybrid_ingestion:
  data_source_type:
    enum: [RAW_JSON, S3_POINTER]
  raw_json_max_size_mb: 10
  s3_config:
    allowed_buckets: [company-cdo-telemetry]
    allowed_extensions: [.json.gz]
    max_object_size_mb: 500
    checksum_required: true        # SHA256 verification trước khi extract
    encryption: aws-kms
```

> **WHY Hybrid**: Cost Explorer data nhỏ (~50KB, 6 cột × 100 records) → RAW_JSON đủ. CUR data lớn (~5-50MB, 24K+ line items) → S3_POINTER tránh timeout. CDO chọn mode phù hợp per-batch.

---

## 6. Signal 1: aws_cost_explorer_daily — Macro Layer (Trends)

Dữ liệu tổng hợp vĩ mô. Map trực tiếp với các cột thô gốc trong tệp tin `cost_explorer_daily.csv` thuộc bộ dữ liệu TF2.

| Thuộc tính    | Giá trị cấu hình                                              |
| ------------- | ------------------------------------------------------------- |
| Type          | Tabular aggregate (daily grain)                               |
| Frequency     | PULL 1 lần/ngày lúc 02:00 AM (EventBridge cron)               |
| Emit point    | CDO gọi aws ce get-cost-and-usage → normalize → đóng gói JSON |
| Retention     | 7 ngày hot (DynamoDB cache), 30 ngày cold (S3)                |
| Used for      | Trend detection, account-level anomaly, baseline calculation  |
| Emit SLA      | p99 < 60s từ CE API response → AI consumable                  |
| Volume SLA    | ~100-500 records/batch (6 accounts × ~20 services × 1 day)    |
| Cost estimate | $0.01/request × 2 requests/day = $0.60/month                  |

### Schema bắt buộc

*(Khớp 100% với 8 cột thô gốc của hệ thống hạch toán, loại bỏ toàn bộ các trường phái sinh tự tính toán)*

```yaml
cost_explorer_signals:
  date: string                    # Định dạng ngày hạch toán dòng tiền (YYYY-MM-DD)
  linked_account_id: int64        # ID tài khoản AWS thành viên phát sinh chi phí
  linked_account_name: string     # Tên định danh môi trường của tài khoản (VD: prod-core, staging)
  service: string                 # Tên hiển thị thương mại của dịch vụ AWS (VD: "Amazon Relational Database Service")
  service_code: string            # Mã code vĩ mô của dịch vụ (VD: AmazonRDS, AmazonEC2)
  region: string                  # Vùng địa lý triển khai hạ tầng (VD: us-east-1, ap-southeast-1)
  unblended_cost: float           # Chi phí ngày hiện tại chưa áp giảm giá (USD)
  is_estimated: bool              # Trạng thái ước tính số liệu tạm thời của AWS (True/False)
```

> [!WARNING]
> **Naming Mismatch (Bẫy lệch pha tên dịch vụ AWS):** CUR dùng mã ngắn `service_code` (e.g. AmazonEC2), Cost Explorer dùng tên dài `service` (e.g. Amazon Elastic Compute Cloud - Compute). CDO bắt buộc cung cấp nguyên bản cả hai trường từ file thô sang để tránh lỗi phân rã khi join dữ liệu ở tầng AI Engine.

> [!IMPORTANT]
> **Dữ liệu ước tính:** Khi dòng tiền dính cờ `is_estimated = true` ở các ngày chưa final, AI Engine PHẢI tự động hạ điểm tin cậy và KHÔNG được phép cấp lệnh can thiệp vật lý thật. CDO gửi kèm tín hiệu `telemetry_delay_event = true` khi CUR chưa finalized để AI tạm hoãn tiến trình và kiểm tra lại mỗi 1 giờ.

### WHY cache DynamoDB

Cost Explorer API có rate limit là 5 requests/second. CDO sẽ cache toàn bộ kết quả thô thu thập được vào DynamoDB để tránh vượt limit. Khi AI Engine cần tính toán baseline trung bình trượt tuần/tháng phái sinh (`rolling_7d_avg`, `rolling_30d_avg`), code ứng dụng sẽ đọc trực tiếp từ cache này chứ không gọi trực tiếp lên AWS CE API.

---

# 7. Signal 2: `daily_cur_spend_usd` — Micro Layer (Facts)

Dữ liệu vi mô cấp tài nguyên chi tiết. Khớp chính xác 100% với cấu trúc bản ghi thô gốc của tệp tin `cur_line_items.csv` thuộc bộ dữ liệu TF2. Đây là nguồn sự thật tối cao (Source of Truth) phục vụ cho mô hình AI cô lập và suy luận can thiệp hạ tầng.

## Thuộc tính

| Thuộc tính | Giá trị cấu hình                                                                                                                          |
| ---------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| Type       | Tabular AWS Cost and Usage Report (CUR 2.0) resource-level                                                                                |
| Frequency  | PULL 1 lần/ngày ngay sau khi nhận được tín hiệu vĩ mô (Cost Explorer)                                                                     |
| Emit point | CDO Platform đọc S3 CUR manifest → Athena Query trích xuất dữ liệu chu kỳ 24h → Đóng gói JSON / nén .gz → Truyền qua giao thức S3_POINTER |
| Retention  | 7 ngày hot (DynamoDB audit cache), 90 ngày cold (Compliance Archive)                                                                      |
| Used for   | Resource-level anomaly detection, drill-down RCA reasoning, containment targeting                                                         |
| Emit SLA   | p99 < 300 giây (Bao gồm thời gian Athena Query + Nén + Upload Egress)                                                                     |
| Volume SLA | ~200 - 500 records/batch/ngày (Dựa theo quy mô phân phối dữ liệu thô lịch sử)                                                             |

## Schema bắt buộc

(Bao bao phủ toàn bộ 23 cột thô gốc từ file mẫu, nghiêm cấm CDO tự ý đổi tên trường hoặc tính toán trước đặc trưng phái sinh):

```yaml
cur_signals:
  bill_billing_period_start_date: string  # Ngày bắt đầu chu kỳ lập hóa đơn (Format UTC)
  bill_payer_account_id: int64            # ID tài khoản AWS gốc thanh toán hóa đơn tổng
  line_item_usage_account_id: int64       # ID tài khoản AWS thành viên trực tiếp chạy máy
  line_item_usage_account_name: string    # Tên tài khoản thành viên (VD: staging, dev, prod-core)
  line_item_line_item_type: string        # Phân loại hạch toán chi phí (VD: Usage, Tax, Fee)
  line_item_usage_start_date: string      # Thời gian tài nguyên bắt đầu chạy máy trong ngày
  line_item_usage_end_date: string        # Thời gian tài nguyên kết thúc chạy máy trong ngày
  line_item_product_code: string          # Mã định danh ngắn của dịch vụ AWS (VD: AmazonRDS)
  line_item_usage_type: string            # Mã chi tiết cấu hình tài nguyên (VD: BoxUsage:p3.2xlarge)
  line_item_operation: string             # Thao tác vận hành hạ tầng (VD: RunInstances, CreateDBInstance)
  line_item_resource_id: string           # *** Khóa chính can thiệp: ARN hoặc ID vật lý của thiết bị ***
  line_item_usage_amount: float           # Khối lượng tiêu thụ tài nguyên đo đạc vật lý hằng ngày
  pricing_unit: string                    # Đơn vị hạch toán tính giá tài nguyên (VD: Hrs, GB)
  line_item_unblended_rate: float         # Đơn giá chạy máy thực tế tại thời điểm hạch toán
  line_item_unblended_cost: float         # *** Nguồn số liệu dòng tiền tối cao phục vụ AI Detection ***
  line_item_currency_code: string         # Đơn vị tiền tệ hạch toán đám mây (Mặc định: USD)
  product_product_name: string            # Tên hiển thị đầy đủ của dịch vụ (VD: Amazon SageMaker)
  product_region_code: string             # Mã khu vực vật lý đặt máy chủ (VD: us-east-1)
  product_instance_type: string           # Loại cấu hình chip/máy chủ ảo nếu có (VD: p3.2xlarge)
  resource_tags_user_team: string         # Thẻ gán tên đội nhóm chịu trách nhiệm (Rỗng = Untagged Spend)
  resource_tags_user_environment: string  # Thẻ gán môi trường: prod, staging, dev, sandbox, ml-research, data-analytics
  resource_tags_user_cost_center: string  # Thẻ gán mã trung tâm chi phí hạch toán doanh nghiệp
  resource_tags_user_owner: string        # Thẻ gán danh tính kỹ sư sở hữu và khởi tạo tài nguyên
```

## Athena Query tối ưu hóa chi phí vận hành

(CDO bắt buộc cấu hình quét theo phân vùng ngày hạch toán, nghiêm cấm quét toàn bộ cơ sở dữ liệu gây lãng phí ngân sách):

```sql
SELECT bill_billing_period_start_date,
       line_item_usage_account_id,
       line_item_usage_account_name,
       line_item_product_code,
       line_item_resource_id,
       line_item_unblended_cost,
       resource_tags_user_team,
       resource_tags_user_environment
FROM "cur2_database"."cur2_table"
WHERE bill_billing_period_start_date = DATE_FORMAT(CURRENT_DATE, '%Y-%m-01 00:00:00')
  AND line_item_usage_start_date >= DATE_ADD('day', -2, CURRENT_DATE)
```

## WHY `line_item_unblended_cost` instead of `line_item_usage_amount`

Biến số đo đạc vật lý `usage_amount` hằng ngày thường xuyên dao động răng cưa biên độ nhỏ do độ trễ mạng hạch toán (nhiễu tự nhiên trong log CUR). Biến số dòng tiền `line_item_unblended_cost` mang phân phối ổn định, tuyến tính và phản ánh chính xác nhất bản chất thâm hụt ngân sách, giúp mô hình bóc tách đặc trưng sạch hơn.

---

## 8. Resource Identity Contract

Chuẩn hóa định danh tài nguyên theo OpenTelemetry semantic conventions. Dùng cho multi-tenant routing + RCA drill-down.

```yaml
resource_identity:
  resource_id: string             # line_item_resource_id (ARN hoặc instance ID)
  resource_type: string           # e.g. aws:ec2:instance, aws:rds:db, aws:sagemaker:notebook
  aws_service: string             # line_item_product_code
  account_id: string              # line_item_usage_account_id
  account_name: string            # line_item_usage_account_name
  region: string                  # product_region_code
  environment:
    enum: [prod, staging, dev, sandbox, ml-research, data-analytics]
  owner: string | null            # resource_tags_user_owner
  team: string | null             # resource_tags_user_team
  cost_center: string | null      # resource_tags_user_cost_center
```

---

## 9. Resource Lineage Contract

Truy vết nguồn gốc tài nguyên. Hỗ trợ RCA nhân quả: `Cost spike → deployment abc123 → commit 84fd2a → team-ml`.

```yaml
resource_lineage:
  deployment_id: string | null    # CloudFormation StackId hoặc ECS deployment ID
  git_sha: string | null          # Commit hash tạo tài nguyên
  pipeline_run_id: string | null  # CI/CD pipeline execution ID
  created_by: string | null       # IAM User/Role ARN
  created_at: rfc3339             # CloudTrail CreateTime
  ttl_expiry: rfc3339 | null      # Ngày hết hạn theo kế hoạch
```

> **WHY lineage**: Khi AI Engine phát hiện anomaly, nếu biết `created_by: team-ml, deployment_id: sagemaker-training-job-42`, RCA reasoning sẽ chính xác hơn so với chỉ biết "resource X tốn tiền" (reference: Google Cloud SRE lineage patterns).

---

## 10. Business Context Signals — False Positive Reduction

Loại bỏ **3 bẫy False Positive** trong dataset: flash-sale, migration, load test (reference: TF2 Dataset README §Bẫy FP).

Logic: `cost ↑ + traffic ↑ = normal growth` | `cost ↑ + traffic flat = anomaly`

```yaml
business_context:
  active_users: int               # Concurrent active users (nếu có)
  orders_count: int               # Transaction count
  traffic_volume: float           # Request volume aggregate
  campaign_flag: bool             # true = đang có marketing campaign
  load_test_flag: bool            # true = đang chạy performance test
  migration_flag: bool            # true = đang migration data
```

> **WHY business context**: Dataset TF2 có 3 sự kiện benign trông y anomaly nhưng **hợp lệ**. Detector không có business context sẽ vỡ ngưỡng FP ≤10% (reference: README line 119).

---

## 11. Time Integrity Contract

Bảo vệ quan hệ nhân quả (causality) trong hệ thống phân tán. **Reject nếu clock skew > 10s**.

```yaml
time_integrity:
  source_timestamp: rfc3339       # Thời điểm AWS agent sinh dữ liệu
  collector_timestamp: rfc3339    # Thời điểm CDO Collector thu thập
  ingestion_timestamp: rfc3339    # Thời điểm AI Engine nhận dữ liệu
  clock_skew_ms: int              # abs(ingestion - source)
  max_allowed_skew_ms: 10000     # 10 giây
```
---

## 12. Telemetry Quality Contract

AI Engine tự động thực thi đánh giá chất lượng nguồn dữ liệu thô do CDO Platform nạp sang trước khi đưa vào luồng suy luận ra quyết định can thiệp.

```yaml
telemetry_quality:
  cur_status:
    enum: [HEALTHY, DELAYED, MISSING]
  cost_explorer_status:
    enum: [HEALTHY, STALE]
  completeness_score: float       # Tỷ lệ các trường dữ liệu bắt buộc mang giá trị hợp lệ (0.0-1.0)
  freshness_score: float          # Độ tươi mới của log hạch toán: 1.0 - (data_age_hours / 24)
  integrity_score: float          # Tỷ lệ trùng khớp mã băm SHA256 kiểm tra toàn vẹn
  is_forced_dry_run: bool         # Đánh dấu tự động ép hệ thống lùi về trạng thái Dry-run an toàn
```

WHY forced DRY-RUN: Đảm bảo nguyên lý an toàn hệ thống tối cao. Nếu nguồn dữ liệu thô đầu vào dính lỗi khiếm khuyết, trễ log hoặc mất tính toàn vẹn khiến chỉ số `completeness_score < 0.8`, AI Engine sẽ ngay lập tức ép hệ thống rơi vào trạng thái DRY-RUN (`is_forced_dry_run = true`).

AI Engine sẽ trả về thông số này trong API Response (`GET /v1/detect/result/{audit_id}`) để cưỡng chế CDO Platform chỉ được phép xuất văn bản cảnh báo, khóa chặt toàn bộ các câu lệnh CLI can thiệp vật lý thật, triệt tiêu hoàn toàn nguy cơ tắt nhầm tài nguyên Production do dữ liệu bẩn.

---

## 13. Quota Telemetry Contract

Hệ thống can thiệp `quota-cap` cần biết headroom trước khi áp trần.

```yaml
quota_signals:
  service_code: string            # e.g. AmazonEC2, AmazonSageMaker
  current_quota: float            # AWS Service Quotas current limit
  current_usage: float            # Actual usage
  utilization_pct: float          # (usage / quota) × 100
  headroom_pct: float             # 100 - utilization_pct
```

---

## 14. Human Feedback Contract — Active Learning Loop

Continual Learning: SRE/Engineer xác nhận qua Slack → AI cập nhật pattern memory.

```yaml
human_feedback:
  anomaly_id: string              # Format: ANM-YYYY-MMDD[A-Z] (khớp API Contract §5.2)
  reviewer_id: string             # Email hoặc Slack User ID
  verdict:
    enum: [TRUE_POSITIVE, FALSE_POSITIVE, BENIGN_EVENT]
  reason: string                  # Giải trình lý do đánh giá
  reviewed_at: rfc3339
```

> **WHY feedback loop**: Dataset TF2 chỉ có 3 nhãn mẫu (mentor giữ đáp án). Feedback loop cho phép hệ thống calibrate sau deployment từ phản hồi thực tế (reference: Arize AI observability patterns).

---

## 15. Audit Chain — Tamper-evident Integrity

Chuỗi hash bảo vệ tính toàn vẹn kiểm toán. Audit trail lưu ≥90 ngày (reference: 01_requirements.md §4).

```yaml
audit_chain:
  audit_id: uuid_v4
  event_hash: string              # sha256(current_payload + previous_hash)
  previous_hash: string           # Hash bản ghi trước đó (append-only chain)
  signature: string               # Chữ ký KMS
  retention_days: 90              # Minimum theo compliance
```

---

## 16. Cross-cutting Requirements

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

## 17. Telemetry Outage Recovery Matrix

| Kịch bản | Cách thức phát hiện | Hành vi xử lý của AI Engine |
|---|---|---|
| CUR trễ (Chưa cập nhật trên S3) | CDO Platform gửi tín hiệu request kèm cờ `telemetry_delay_event = true`. | AI Engine tạm hoãn tiến trình batch job của ngày hạch toán đó. Lập lịch tự động kiểm tra lại mỗi 1 giờ. Thử lại tối đa 4 lần, nếu quá thời hạn vẫn mất kết nối sẽ phát cảnh báo P1 khẩn cấp. |
| Pipeline CDO sập hoàn toàn | Hệ thống không nhận được bất kỳ payload nạp dữ liệu nào từ Tenant quá 26 tiếng kể từ chu kỳ chạy cũ. | AI Engine tự động phát cảnh báo P1 đỏ khẩn cấp trực tiếp tới kênh Slack của cả hai nhóm kỹ thuật gọi nhân sự dậy xử lý infrastructure. |
| Dữ liệu ước tính từ AWS (`is_estimated`) | Bản ghi dữ liệu thô nhận giá trị cấu hình `is_estimated = true`. | AI Engine tự động hạ điểm tự tin thuật toán (`confidence_score < 0.50`), ép toàn bộ hành động can thiệp sang chế độ an toàn Dry-run/Alert-only, tuyệt đối cấm phát câu lệnh CLI tác động vật lý thật. |
| Cost Explorer vượt trần giới hạn gọi máy | Trạng thái hạch toán vĩ mô dính cờ `cost_explorer_status: STALE`. | CDO Platform tự động serve dữ liệu từ bảng DynamoDB cache nội bộ. AI Engine ghi nhật ký kiểm toán hệ thống dính nhãn `stale_data_used: true`. |

---

## Open Questions (Resolved)

- [x] **Q1**: Signal nào cần exactly-once delivery?
  - *Resolved*: At-least-once OK cho tất cả. Idempotency Key xử lý dedup. Exactly-once phức tạp không cần thiết cho batch 24h.

- [x] **Q2**: Encryption ngoài TLS chuẩn?
  - *Resolved*: TLS 1.3 in-transit đủ. At-rest dùng AWS KMS (DynamoDB + S3). Không cần end-to-end encryption bổ sung.

---

## Related Documents

- `ai-api-contract.md` — 5 API endpoints specification, Idempotency rules, Response schema.
- `deployment-contract.md` — ECS Fargate compute, Networking, Secrets, Circuit Breaker.
- `docs/01_requirements.md` — Success criteria, hard constraints, retention requirements.
- `docs/02_solution_design.md` — Architecture overview, component breakdown, data flow.
- `docs/03_ai_engine_spec.md` — Model governance, Bedrock Guardrails, Prompt engineering.
- `docs/04_eval_report.md` — Backtest results, failure analysis, curveball impact.
- `docs/05_adrs.md` — Architecture Decision Records (ADR-001 to ADR-005).
