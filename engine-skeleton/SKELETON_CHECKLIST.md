# Engine Skeleton — Checklist & Insight Notes (Contracts v1.5.0 Final)

> **Mục đích**: File này giúp toàn bộ thành viên AIOps, DevOps, CloudOps trong Task Force 2 hiểu rõ engine skeleton giải quyết vấn đề gì, kiểm tra đúng/sai, và biết được ranh giới trách nhiệm giữa các nhóm sau khi đã đồng bộ hoàn toàn với **Contracts Final v1.5.0**.
>
> **Ngày cập nhật**: 2026-06-25 (W11 T4)
> **Trạng thái**: Skeleton (sync/boto3 logic) — 26/26 tests passed, sẵn sàng đẩy lên ECR.

---

## 1. Engine Skeleton giải quyết vấn đề gì?

### Vấn đề cốt lõi

Theo flow capstone, nhóm AI phải deploy **engine skeleton** vào chiều **Thứ 5 W11** để CDO-01 và CDO-02 có endpoint thật để tích hợp ngay từ **Thứ 6 W11**, thay vì chờ AI hoàn thành logic thật (sẽ kéo đến W12 T3).

Nếu **không có skeleton**:
- CDO bị **block 5–6 ngày** không thể tích hợp, test pipeline, hay build dashboard.
- Lúc tích hợp thật vào W12 T3 (Integration Session) sẽ phát hiện lỗi schema, lỗi routing, lỗi authentication → **không kịp sửa trước code freeze**.

### Skeleton giải quyết bằng cách

| Vấn đề | Cách skeleton v1.3.0 xử lý |
|---|---|
| CDO chưa có endpoint để gọi | Skeleton cung cấp 6 endpoints thật chạy trên AWS, trả về JSON đúng schema của final contracts. |
| CDO không biết response trông như thế nào | Response schema **giống hệt** real engine: đồng bộ đồng thời CUR-primary và CE-fallback. |
| CDO cần test offline rollback | Cung cấp `boto3_equivalent` trong `/v1/decide` để CDO cache và tự gọi rollback khi AI Engine sập. |
| CDO cần verify error handling | Trả đúng error codes: 400 (bad request), 422 (validation), 429 (rate limit), 503 (engine down). |
| W12 chuyển sang real logic sẽ break CDO? | **Không** — URL giữ nguyên, schema giữ nguyên, CDO không cần sửa gì. |

### Một câu tóm tắt

> Engine skeleton v1.3.0 là **mock server thật** chạy trên AWS, trả response khớp 100% với 3 contracts final (ai-api v1.3.0, telemetry v3.1.0, deployment v1.2.0), cho phép CDO tích hợp offline rollback và verify pipeline mà không bị block.

---

## 2. Checklist kiểm tra Skeleton (cho toàn team)

### ✅ Contract Compliance — Schema có đúng contract v1.3.0 không?

- [x] **Các Endpoint path đúng**:
  - [x] `POST /v1/detect` (Đồng bộ 200 OK — trả về trực tiếp `anomalies_list` và `data_confidence`)
  - [x] `GET /v1/status/{id}` (Dual-purpose: check status của detection job qua UUID, hoặc check remediation status qua ANM-ID)
  - [x] `POST /v1/decide` (Trả về RCA + action plan + `rollback_payload` chứa `boto3_equivalent`)
  - [x] `POST /v1/verify` (Xác thực hiệu quả sau ngăn chặn dựa trên post-action telemetry)
  - [x] `POST /v1/audit/{audit_id}/rollback` (CDO gửi audit notification SAU KHI đã tự chạy rollback qua boto3)
  - [x] `GET /health` trên port `8080` (Trả về status của `s3_audit_bucket`, `bedrock_api`, `s3_cur_bucket`)
- [x] **Request schema `/v1/detect`** khớp Telemetry Contract v3.1.0:
  - [x] `aws_cur_line_items` (Mảng CUR, là source of truth chính cho detection)
  - [x] `aws_cost_explorer_daily` (Mảng CE, chỉ bắt buộc khi `telemetry_delay_event=true` làm fallback)
  - [x] `resource_utilization_metrics[].cpu_utilization_hourly` (Mảng 24 phần tử float đại diện cho CPU% mỗi giờ, thay thế cho `idle_hours_continuous`)
  - [x] `data_confidence` được tự động đánh giá: `HIGH` (khi dùng CUR) hoặc `LOW` (khi dùng CE fallback)
  - [x] `callback_url` (optional, pattern `https://` để gửi bản sao kết quả dạng fire-and-forget)
  - [x] `s3_bucket_uri` (enforce pattern `s3://tf2-cdo{NN}-telemetry-{region}/...` khi `data_source_type=S3_POINTER`)
- [x] **Response schema `/v1/detect`** khớp AI API Contract v1.3.0:
  - [x] Trả về `success` (bool), `correlation_id` (UUID v4), `anomalies_detected` (bool), `data_confidence` (HIGH/LOW)
  - [x] `anomalies_list` chứa các `AnomalyResponseItem`: `anomaly_id` (pattern `ANM-YYYY-MMDD[A-Z]`), `anomaly_type`, `severity` (HIGH/MEDIUM/LOW), `confidence_score`, `resource_id`, `environment`, `responsible_team`, `unblended_cost_24h_usd`, `cost_ratio_to_7d_avg`, `ai_model_used`, `alert_routing`
- [x] **Decide response** có chứa `boto3_equivalent` để hỗ trợ CDO chạy rollback offline (không phụ thuộc vào AI Engine).
- [x] **Rollback response** chứa `audit_recorded=true` thay vì rollback_initiated, xác nhận CDO đã rollback xong và AI ghi nhận audit trail.
- [x] **Error codes** khớp contract: 400 (bad request), 422 (validation), 429 (rate limit), 503 (engine down)

### ✅ Safety Boundaries — 3 ranh giới đỏ có bị vi phạm không?

- [x] Gửi request với `environment: "prod"` → engine **KHÔNG bao giờ** sinh `suggested_action` = `schedule_shutdown` hay `quota_cap` (chỉ `tag_for_review` hoặc `alert_only`).
- [x] Module `containment.py` có check cứng: Prod/Staging/Unknown → skip auto-containment hoặc hạ xuống safe actions.
- [x] Các biến môi trường bảo vệ `FINOPS_NEVER_TERMINATE_PROD`, `FINOPS_NEVER_DELETE_DATA`, `FINOPS_NEVER_MODIFY_IAM` đều default `true` ở cấu hình `Settings`.
- [x] **Error Budget Lock per-environment**: Cho phép đặt ngưỡng khóa containment độc lập cho từng môi trường (mặc định `prod: 1.0%`, `staging: 10.0%`, `dev: disabled`) giúp tối ưu hóa an toàn.

### ✅ Multi-tenant — Có cách ly đúng tenant không?

- [x] Request thiếu header `X-Tenant-Id` → trả `400 Bad Request` ngay tại middleware.
- [x] Response header trả lại đúng `X-Tenant-Id` và `X-Correlation-Id` của request.
- [x] Audit trail log ghi nhận `tenant_id` riêng biệt cho mỗi tenant (CDO-01 vs CDO-02).
- [x] Error budget và các in-memory caches được phân tách rõ ràng theo `tenant_id`.

### ✅ Audit Trail — Log có đủ cho SOC2 không?

- [x] Ghi nhận `audit_id` cho mỗi vụ phát hiện bất thường và hoạt động rollback.
- [x] Audit log dạng structured JSON (được capture bởi CloudWatch Logs với retention >= 90 ngày).
- [x] Khi CDO gọi `/v1/audit/{audit_id}/rollback`, log lưu vết đầy đủ thông tin: `rolled_back_by`, `reason`, `rollback_status`, `boto3_result`.

### ✅ Deployment — Chạy được trên ECS Fargate không?

- [x] Dockerfile chạy trên port `8080` dùng non-root user (`appuser`).
- [x] `HEALTHCHECK` kiểm tra endpoint `/health` định kỳ 30s.
- [x] Endpoint `/health` trả về kết nối của 3 dịch vụ: `s3_audit_bucket`, `bedrock_api`, `s3_cur_bucket`.

---

## 3. Insight Notes — Những quyết định thiết kế quan trọng

### 3.1 Tại sao dùng Strategy Pattern cho Detection?

```
engine/strategies/
├── base.py           ← Abstract interface (DetectionStrategy)
├── dummy.py          ← W11 skeleton: hardcoded logic (cost > 200 -> anomaly)
└── statistical.py    ← W12: statistical rule-based spike detection
```

**Lý do**: Khi chuyển sang real logic trong W12, chúng ta chỉ cần bật feature flag `FINOPS_ENABLE_LLM_ANALYSIS=true` để kích hoạt `StatisticalStrategy` hoặc các thuật toán ML nâng cao. Router và các schemas của CDO hoàn toàn không bị ảnh hưởng.

### 3.2 Cơ chế Offline Rollback Contingency (Boto3 Equivalent)

**Lý do**: CDO-01 và CDO-02 cần một phương án dự phòng cực mạnh (Contingency Plan) khi AI Engine bị sập (downtime). 
- Trong API `/v1/decide`, AI Engine trả về cấu trúc `rollback_payload` chứa trường `boto3_equivalent`.
- CDO sẽ lưu trường này vào DynamoDB của họ ngay khi nhận được quyết định hành động.
- Nếu xảy ra sự cố và cần rollback khẩn cấp nhưng AI Engine không phản hồi (503/Timeout), CDO có thể đọc `boto3_equivalent` từ DB và thực thi trực tiếp qua SDK boto3 của họ mà không cần AI Engine.

### 3.3 Luồng Audit Rollback mới (Post-execution Notification)

**Lý do**: Để giảm thiểu rủi ro nghẽn mạng và tăng độ tin cậy:
- CDO sẽ là bên **chủ động thực thi rollback** trước (dùng CLI hoặc boto3).
- Sau khi thực thi xong, CDO gọi `POST /v1/audit/{audit_id}/rollback` để báo cho AI Engine.
- AI Engine cập nhật trạng thái audit trail, tính toán số lượng False Positive để cải thiện mô hình, và trừ % tương ứng vào Error Budget của tenant.

### 3.4 Tại sao dùng `cpu_utilization_hourly` thay vì `idle_hours_continuous`?

**Lý do**: Mentor yêu cầu cung cấp raw metrics để tăng tính thuyết phục. Thay vì CDO tự tính toán số giờ idle (có thể sai lệch thuật toán giữa 2 bên), CDO sẽ gửi mảng 24 phần tử raw CPU% của ngày hôm đó. AI Engine sẽ tự tính số giờ liên tục CPU < 5% để kết luận tài nguyên có bị idle hay không, đảm bảo logic tính toán tập trung tại AI.

---

## 4. Mapping Skeleton → Contract Documents

| File trong skeleton | Khớp với Contract/Doc nào | Ghi chú |
|---|---|---|
| `api/schemas/detect.py` | Telemetry Contract v3.1.0 & AI API v1.3.0 §5.1 | Định nghĩa `CURLineItem`, `CostExplorerItem`, `UtilizationMetric`, `DetectRequest`, `DetectResponse` |
| `api/schemas/decide.py` | AI API Contract v1.3.0 §5.2 | RCA, Dashboard data, `Boto3Equivalent` |
| `api/schemas/verify.py` | AI API Contract v1.3.0 §5.3 | Post telemetry verification, `telemetry_delay_event` |
| `api/schemas/rollback.py` | AI API Contract v1.3.0 §5.6 | CDO notification model (`audit_recorded`) |
| `api/router.py` | AI API Contract v1.3.0 (Tất cả endpoint) | Tầng định tuyến và điều phối logic (6 endpoints) |
| `config/settings.py` | Deployment Contract v1.2.0 | Port 8080, Safety settings, Error budget per-env |

---

## 5. Câu hỏi Review cho Team

Trước khi bàn giao skeleton cho CDO, hãy đảm bảo các câu hỏi sau được thông suốt:

1. **Q**: Khi nào trường `aws_cost_explorer_daily` là bắt buộc?
   * **A**: Chỉ khi `telemetry_delay_event=true` (CUR bị trễ quá 36h). Bình thường CDO chỉ cần gửi `aws_cur_line_items`.
2. **Q**: Nếu AI Engine bị sập hoàn toàn, CDO có rollback được không?
   * **A**: Được. CDO đã cache `boto3_equivalent` từ bước `/v1/decide` vào DynamoDB cục bộ của họ, cho phép tự chạy rollback offline.
3. **Q**: Error Budget Lock hoạt động như thế nào ở skeleton?
   * **A**: Mỗi lần CDO gọi rollback, budget burned tăng 0.5%. Nếu vượt ngưỡng (vd: 1% của prod), hệ thống sẽ khóa containment (chuyển sang dry-run mode). Ngưỡng này cấu hình độc lập theo từng môi trường.
4. **Q**: CDO-01 và CDO-02 phân biệt như thế nào trong log?
   * **A**: Thông qua header bắt buộc `X-Tenant-Id` được bắt ở middleware.

---

## 6. Ghi chú vận hành cho CDO (Cách gọi test)

### 6.1 Test đồng bộ phát hiện Anomaly (cost > 200 -> Anomaly)

```bash
curl -s -X POST http://localhost:8080/v1/detect \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: cdo-platform-01" \
  -d '{
    "data_source_type": "RAW_JSON",
    "telemetry_delay_event": false,
    "aws_cur_line_items": [{
      "line_item_usage_start_date": "2026-06-25T00:00:00Z",
      "line_item_usage_account_id": "123456789012",
      "line_item_product_code": "AmazonEC2",
      "line_item_usage_type": "BoxUsage:t3.medium",
      "line_item_resource_id": "i-0123456789abcdef0",
      "line_item_usage_amount": 24.0,
      "pricing_unit": "Hrs",
      "line_item_unblended_cost": 250.0,
      "usage_density_24h": 1.0,
      "resource_tags_user_environment": "dev"
    }],
    "resource_utilization_metrics": [{
      "resource_id": "i-0123456789abcdef0",
      "cpu_percent": 4.2,
      "cpu_utilization_hourly": [2.0, 3.0, 4.0, 3.0, 2.0, 1.0, 2.0, 3.0, 4.0, 3.0, 2.0, 1.0, 2.0, 3.0, 4.0, 3.0, 2.0, 1.0, 2.0, 3.0, 4.0, 3.0, 2.0, 1.0],
      "network_in_bytes": 1024.0,
      "network_out_bytes": 1024.0
    }]
  }' | jq .
```

### 6.2 Test lấy quyết định ngăn chặn (Decide)

```bash
curl -s -X POST http://localhost:8080/v1/decide \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: cdo-platform-01" \
  -d '{
    "correlation_id": "test-correlation-id-001",
    "anomaly_context": {
      "anomaly_id": "ANM-2026-0625A",
      "anomaly_type": "runaway_usage",
      "unblended_cost_24h_usd": 250.0,
      "cost_ratio_to_7d_avg": 5.0,
      "resource_id": "i-0123456789abcdef0",
      "environment": "dev"
    },
    "dry_run_mode": true
  }' | jq .
```

### 6.3 Test thông báo Rollback đã thực thi (Audit Notification)

```bash
curl -s -X POST http://localhost:8080/v1/audit/ANM-2026-0625A/rollback \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: cdo-platform-01" \
  -d '{
    "rolled_back_by": "cdo-operator",
    "rolled_back_at": "2026-06-25T16:00:00Z",
    "reason": "False positive - legitimate batch job",
    "rollback_status": "COMPLETED",
    "boto3_result": {
      "status": "success",
      "details": "Tag finops:review removed successfully"
    }
  }' | jq .
```

---

## 7. File Map — Mô tả mục đích từng file

### 7.1 Cấu trúc thư mục hiện tại

```text
engine-skeleton/
├── api/
│   ├── middleware/
│   │   └── request_context.py   ← Middleware xử lý Tenant/Correlation headers
│   ├── schemas/
│   │   ├── decide.py            ← Schemas cho RCA, boto3_equivalent
│   │   ├── detect.py            ← Schemas cho CUR-primary detect & health check
│   │   ├── rollback.py          ← Schemas thông báo rollback của CDO
│   │   ├── status.py            ← Schemas cho polling status
│   │   └── verify.py            ← Schemas cho hậu kiểm tra (verify)
│   └── router.py                ← Bộ định tuyến chính của 6 endpoints
├── config/
│   └── settings.py              ← Quản lý cấu hình, safety flags, error budget per-env
├── engine/
│   ├── strategies/
│   │   ├── base.py              ← Abstract Base Class cho thuật toán detection
│   │   ├── dummy.py             ← Logic mock (cost > 200) cho skeleton
│   │   └── statistical.py       ← Logic rule-based / statistical cho W12
│   ├── alert_router.py          ← Router cảnh báo tới Finance/Engineering
│   ├── audit.py                 ← Audit Trail logger (SOC2 compliant)
│   └── containment.py           ← Bộ đánh giá ngăn chặn an toàn (3 Safety Boundaries)
├── models/
│   ├── domain.py                ← Data models dùng nội bộ engine
│   └── enums.py                 ← Hệ thống Enums dùng chung toàn bộ engine
├── tests/
│   └── test_detect.py           ← Bộ 26 integration & contract tests
├── Dockerfile                   ← Docker build Fargate (port 8080)
├── requirements.txt             ← Thư viện phụ thuộc
└── SKELETON_CHECKLIST.md        ← Checklist & tài liệu hướng dẫn (File này)
```

---

## 8. Request Lifecycle Workflow (v1.3.0 Sync Flow)

```text
CDO Platform
    │
    │  POST /v1/detect
    │  Headers: X-Tenant-Id, X-Correlation-Id
    │  Body: CUR data + utilization metrics (cpu_utilization_hourly)
    │
    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  FastAPI Application (port 8080)                                           │
│                                                                            │
│  1. request_context.py (Middleware)                                        │
│     ├─ Validate X-Tenant-Id (thiếu -> 400)                                 │
│     └─ Gắn Correlation ID (UUID v4)                                        │
│                                                                            │
│  2. router.py (detect_anomaly)                                             │
│     ├─ Đánh giá data_confidence (HIGH cho CUR, LOW cho CE fallback)        │
│     ├─ Gọi Strategy.detect() -> AnomalyResult                              │
│     ├─ Xác định alert routing (Finance/Engineering/Both)                    │
│     ├─ Ghi audit trail (audit.py)                                          │
│     └─ Trả về DetectResponse (200 OK Synchronous)                          │
└─────────────────────────────────────────────────────────────────────────────┘
    │
    │  HTTP 200 OK + anomalies_list
    ▼
CDO Platform
    ├─► Nếu có anomaly -> gọi tiếp POST /v1/decide để lấy action plan
    ├─► Cache boto3_equivalent phòng trường hợp AI Engine gặp sự cố
    └─► Thực thi containment (dry-run/live) -> Gọi POST /v1/verify
```

---
*Cập nhật file này khi có thay đổi. Append-only — không xóa nội dung cũ.*
