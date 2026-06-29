# AI API Contract - Task Force 2 (FinOps Watch)

<!-- Owner: Nhóm AI 2
     Signed by: AI Lead + CDO Leads × 2 (CDO-01, CDO-02) + Reviewer panel
     Date signed: 2026-06-25 (W11 T5)
     🔒 FREEZE - no change without formal change request -->

## 1. Mục đích

Tài liệu này định nghĩa **AI API Contract** giữa **Nhóm AI** (đóng vai trò cung cấp trí tuệ nhân tạo phát hiện bất thường & phân tích nguyên nhân) và **Nhóm CDO** (đóng vai trò vận hành nền tảng dữ liệu, thu thập log & thực thi). 

Hệ thống hoạt động theo cơ chế **Batch Job chu kỳ 24 giờ**. CDO Platform chịu trách nhiệm đóng gói dữ liệu và gửi tới AI Engine qua API endpoint `/v1/detect` chạy bất đồng bộ (Async) để tối ưu hóa hiệu năng và tránh nghẽn mạng.

Để giải quyết giới hạn kích thước Payload của AWS API Gateway (tối đa 10MB) và ALB khi truyền log CUR lớn, AI Engine hỗ trợ **Cơ chế nạp lai (Hybrid Ingestion Input)**: Cho phép CDO truyền mảng dữ liệu thô nếu dung lượng nhỏ, HOẶC truyền một con trỏ S3 URL chứa file log vi mô nếu dung lượng lớn. 

Hệ thống cũng hỗ trợ **Cờ chạy cưỡng chế (`is_ad_hoc`)** để bỏ qua khóa trùng lặp 24h khi cần quét khẩn cấp. Kết quả phân tích được lưu vào DynamoDB và CDO có thể truy xuất qua endpoint kết quả.

---

## 2. Versioning & Evolution

- **Current Version**: `v1.0` (nằm trong đường dẫn `/v1/`)
- **Breaking Changes**: Bất kỳ thay đổi cấu trúc dữ liệu bắt buộc (Breaking changes) sẽ yêu cầu nâng phiên bản lên `/v2/`. Cả hai phiên bản phải được hỗ trợ song song tối thiểu **30 ngày** để đảm bảo khả năng tương thích ngược.
- **Non-breaking Changes**: Việc thêm các trường tùy chọn hoặc bổ sung endpoint mới được coi là minor bump, không thay đổi đường dẫn `/v1/`.
- **Change Request Process**: Đề xuất thay đổi phải được đưa ra thảo luận trong cuộc họp Task Force nội bộ, có sự đồng thuận của cả AI Lead và CDO Leads trước khi cập nhật tài liệu này.

---

## 3. Security, Authentication & Rate Limiting

- **Inter-service Authentication**: Sử dụng cơ chế **AWS IAM SigV4** để xác thực các yêu cầu gọi chéo giữa các service nội bộ của CDO và AI Engine (không sử dụng API key tĩnh).
- **Cross-account Access**: Sử dụng cơ chế **STS assume-role** kèm theo Session Tag `tenant_id` để phân tách và bảo vệ dữ liệu người thuê.
- **Network Isolation**: AI Engine được đóng gói container chạy hoàn toàn trong **Private Subnet** phía sau **Internal Application Load Balancer (ALB)**, chỉ cho phép kết nối nội bộ từ các CDO VPC của Task Force (cấm mở cổng public ra Internet).
- **Rate Limiting**: 
  - Đóng cấu hình hạn mức gọi API tối đa trên mỗi Tenant thông qua API Gateway Usage Plan.
  - Phản hồi mã lỗi `429 Too Many Requests` kèm theo header `Retry-After: <seconds>` nếu vượt quá hạn mức.

---

## 4. Cross-Cutting Headers

Mọi API request gửi từ CDO Platform tới AI Engine (ngoại trừ endpoint `/health` ẩn danh phục vụ ALB) bắt buộc phải đính kèm các Header tiêu chuẩn sau:

| Header | Type | Required | Description |
|---|---|---|---|
| `Content-Type` | String | ✓ | Định dạng dữ liệu truyền tải, cố định: `application/json`. |
| `Accept` | String | ✓ | Định dạng mong muốn nhận về, cố định: `application/json`. |
| `X-Tenant-Id` | UUID v4 | ✓ | Định danh duy nhất cho từng Tenant (Cô lập dữ liệu logic và kiểm soát phân quyền tác vụ). |
| `Authorization` | Signature | ✓ | Chữ ký xác thực AWS IAM SigV4. |
| `X-Idempotency-Key` | String | ✓ | Composite Key định dạng `tenant_id:YYYY-MM-DD`. Ngăn chặn việc thực thi trùng lặp trong cùng một chu kỳ 24 giờ (bị bỏ qua nếu `is_ad_hoc` đặt là `true`). |
| `X-Correlation-Id` | UUID | optional | Khóa theo dõi vết luồng nghiệp vụ E2E qua các microservice (tự sinh nếu thiếu). |

> [!IMPORTANT]
> **Quy tắc Idempotency & Trùng khóa nâng cao**:
> - `X-Idempotency-Key` được lưu trữ trên DynamoDB với thời gian TTL tự động hủy sau 24 giờ.
> - **Nếu tiến trình đang chạy (processing):** Trả về `409 Conflict` kèm thông tin tiến trình cũ đang xử lý.
> - **Nếu tiến trình đã hoàn thành (completed / failed):**
>   - *Trường hợp Payload khớp hoàn toàn:* Trả về mã `200 OK` kèm kết quả đã lưu trong DynamoDB tương ứng với `audit_id` của lần chạy đầu tiên.
>   - *Trường hợp Payload khác biệt (Hash Mismatch):* Hệ thống trả về lỗi **`400 Bad Request`** kèm mã lỗi nội bộ **`ERR_IDEMPOTENCY_MISMATCH`** (Cảnh báo việc gửi dữ liệu khác nhau trên cùng một khóa).

---

## 5. Endpoints Specification

### 5.1 Endpoint 1: `POST /v1/detect`

**Mục đích**: CDO Platform gọi API này để gửi dữ liệu chi phí vĩ mô & log CUR vi mô (hoặc con trỏ S3 tới file CUR) trong chu kỳ 24 giờ. Tiến trình xử lý chạy bất đồng bộ (Async) ở background.

#### Request Body
Hỗ trợ cơ chế **Hybrid Ingestion** và **Cờ cưỡng chế** trong cùng một cấu trúc payload:

1.  `data_source_type` (Enum: `RAW_JSON` | `S3_POINTER`).
2.  `is_ad_hoc` (Boolean: Cờ chạy cưỡng chế khẩn cấp, bỏ qua Idempotency Key nếu đặt là `true`, mặc định: `false`).
3.  `aws_cost_explorer_daily` (Mảng chứa 6 cột dữ liệu vĩ mô, luôn luôn bắt buộc).
4.  `aws_cur_line_items` (Bắt buộc nếu `data_source_type` là `RAW_JSON`. Nhận mảng dữ liệu CUR vi mô trực tiếp nếu payload dưới 10MB).
5.  `s3_bucket_uri` (Bắt buộc nếu `data_source_type` là `S3_POINTER`. Nhận đường dẫn S3 chứa file log CUR nén nếu dung lượng lớn).

##### Data Schema
```json
{
  "data_source_type": "string (Required - Kiểu nạp dữ liệu: RAW_JSON | S3_POINTER)",
  "is_ad_hoc": "boolean (Optional - Bỏ qua khóa idempotency nếu true, mặc định: false)",
  "aws_cost_explorer_daily": [
    {
      "date": "string (Required - Định dạng YYYY-MM-DD)",
      "linked_account_id": "long (Required - ID tài khoản thành viên AWS)",
      "linked_account_name": "string (Required - Tên môi trường tài khoản, VD: prod-core)",
      "service": "string (Required - Tên thương mại dịch vụ AWS)",
      "service_code": "string (Required - Mã định danh dịch vụ, VD: AmazonRDS)",
      "region": "string (Required - Vùng triển khai, VD: us-east-1)",
      "unblended_cost": "float (Required - Số tiền phát sinh thô)",
      "is_estimated": "boolean (Required - Cờ trạng thái ước tính số liệu của AWS)"
    }
  ],
  "s3_bucket_uri": "string (Required nếu S3_POINTER - Đường dẫn S3 trỏ tới tệp log CUR thô nén .json.gz)",
  "aws_cur_line_items": [
    {
      "bill_billing_period_start_date": "string (Required)",
      "bill_payer_account_id": "long (Required)",
      "line_item_usage_account_id": "long (Required)",
      "line_item_usage_account_name": "string (Required)",
      "line_item_line_item_type": "string (Required)",
      "line_item_usage_start_date": "string (Required)",
      "line_item_usage_end_date": "string (Required)",
      "line_item_product_code": "string (Required)",
      "line_item_usage_type": "string (Required)",
      "line_item_operation": "string (Required)",
      "line_item_resource_id": "string (Nullable - ARN/ID thiết bị vật lý thật)",
      "line_item_usage_amount": "float (Required)",
      "pricing_unit": "string (Required)",
      "line_item_unblended_rate": "float (Required)",
      "line_item_unblended_cost": "float (Required)",
      "line_item_currency_code": "string (Required)",
      "product_product_name": "string (Required)",
      "product_region_code": "string (Required)",
      "product_instance_type": "string (Nullable)",
      "resource_tags_user_team": "string (Nullable)",
      "resource_tags_user_environment": "string (Required - Tag môi trường: prod-core, prod-payments, staging, dev, sandbox, ml-research, data-analytics)"
      "resource_tags_user_cost_center": "string (Nullable)",
      "resource_tags_user_owner": "string (Nullable)"
    }
  ]
}
```

##### Request Example (Sử dụng S3 Pointer + Chạy khẩn cấp Ad-hoc)
```json
{
  "data_source_type": "S3_POINTER",
  "is_ad_hoc": true,
  "aws_cost_explorer_daily": [
    {
      "date": "2026-03-20",
      "linked_account_id": 200000000012,
      "linked_account_name": "staging",
      "service": "Amazon Relational Database Service",
      "service_code": "AmazonRDS",
      "region": "us-east-1",
      "unblended_cost": 87.46,
      "is_estimated": false
    }
  ],
  "s3_bucket_uri": "s3://company-cdo-telemetry/cur/2026-06-25-raw-compressed.json.gz"
}
```

#### Response Body (`202 Accepted`)
```json
{
  "audit_id": "UUID (Mã định danh duy nhất cho phiên kiểm toán chi phí)",
  "status": "processing",
  "retry_after_seconds": 30,
  "message": "string (Thông điệp phản hồi từ hệ thống)",
  "created_at": "RFC3339 UTC Timestamp"
}
```

##### Response Example
```json
{
  "audit_id": "8f3b610c-18a4-4e2b-9801-bde901844b20",
  "status": "processing",
  "retry_after_seconds": 30,
  "message": "Ingestion successful. Please poll for results after the designated time.",
  "created_at": "2026-06-25T10:00:00Z"
}
```
---

### 5.2 Endpoint 2: `GET /v1/detect/result/{audit_id}`

**Mục đích**: CDO Platform gọi API này để truy xuất kết quả phân tích bất thường chi phí từ AI Engine dựa trên `audit_id`. Dữ liệu được đọc trực tiếp từ DynamoDB (P99 Latency < 10ms).

#### Request Headers
Yêu cầu đính kèm `X-Tenant-Id` và `Authorization` (Enforce multi-tenant: Chỉ trả về thông tin nếu `audit_id` thuộc về đúng `X-Tenant-Id` tương ứng, ngược lại trả về `403 Forbidden`).

#### Request Query Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `limit` | Integer | optional | Số bản ghi tối đa trả về trên một trang (mặc định: 50, tối đa: 100). |
| `next_token` | String | optional | Token phân trang nhận được từ phản hồi trước để truy xuất trang tiếp theo. |

#### Response Body (`200 OK`)
Phản hồi khi trạng thái là `completed` sẽ trả về **mảng danh sách các điểm bất thường (`anomalies_list`)** kèm theo **cơ chế phân trang (`pagination`)** để xử lý trường hợp phát hiện nhiều tài nguyên bất thường cùng lúc. Mỗi bản ghi bất thường chứa đầy đủ metadata, số liệu tài chính và chỉ dẫn kỹ thuật kèm **Audit Trail Context** để đảm bảo lưu vết tuân thủ $\ge 90$ ngày.

```json
{
  "audit_id": "UUID",
  "status": "string (completed | processing | failed)",
  "error_message": "string (Chỉ xuất hiện khi status là failed, ví dụ: ERR_LLM_TIMEOUT | ERR_DATABASE_CONN)",
  "message": "string (Chỉ xuất hiện khi status là processing để cập nhật trạng thái)",
  "total_anomalies_found": "int (Tổng số bất thường phát hiện được trong chu kỳ)",
  "anomalies_list": [
    {
      "anomaly_metadata": {
        "anomaly_id": "string (Mã định danh bất thường, định dạng ANM-YYYY-MMDD[A-Z])",
        "timestamp": "RFC3339 UTC Timestamp",
        "resource_id": "string (ID vật lý hoặc ARN của thiết bị bất thường)",
        "environment": "string (Môi trường phát sinh: prod, staging, dev, sandbox, ml-research, data-analytics)",
        "confidence_score": "float (Độ tin cậy thuật toán từ 0.0 đến 1.0)",
        "ai_model_used": "string (Mô hình AI thực hiện suy luận)"
      },
      "finance_dashboard_data": {
        "target_recipient": "Finance Team & CFO Dashboard",
        "metrics": {
          "unblended_cost_24h_usd": "float (Chi phí 24 giờ qua)",
          "cost_ratio_to_7d_avg": "float (Tỷ số tăng so với baseline trung bình 7 ngày)",
          "projected_monthly_waste_usd": "float (Lượng tiền lãng phí dự kiến trong 30 ngày tiếp theo nếu không can thiệp)"
        },
        "allocation": {
          "responsible_team": "string (Squad chịu trách nhiệm)",
          "cost_center_code": "string (Mã trung tâm chi phí)"
        },
        "executive_summary": "string (Bản tóm tắt bằng ngôn ngữ tài chính tự nhiên)"
      },
      "engineering_dashboard_data": {
        "target_recipient": "Engineering Console & Slack Alert",
        "technical_context": {
          "aws_service": "string (Mã dịch vụ)",
          "usage_type": "string (Usage type)",
          "pricing_unit": "string",
          "usage_amount_24h": "float",
          "usage_density_24h": "float"
        },
        "root_cause_analysis": {
          "primary_driver_feature": "string (Thuộc tính chính dẫn tới phát hiện bất thường)",
          "technical_reason": "string (Phân tích kỹ thuật chi tiết lỗi)",
          "missing_mandatory_tags": "array of strings (Danh sách các thẻ tag bắt buộc bị thiếu)"
        },
        "mitigation_action": {
          "strategy": "string (Chiến lược can thiệp tương ứng với môi trường)",
          "immediate_action": "string (Lệnh can thiệp tức thì: tag-for-review | time-gated-countdown | auto-shutdown | quota-cap)",
          "applied_payload": {
            "action_type": "string (Kiểu tác động: inject_aws_tag | stop_instance | stop_sagemaker_notebook | restrict_quota)",
            "tag_key": "string (optional - Khóa tag bổ sung)",
            "tag_value": "string (optional - Giá trị tag bổ sung)",
            "aws_cli_command": "string (Lệnh AWS CLI tương ứng để can thiệp thực tế)"
          },
          "enforcement_countdown": {
            "time_lock_seconds": "int (Thời gian đếm ngược đối với Staging, mặc định 14400)",
            "fallback_action": "string (Hành động cưỡng chế sau đếm ngược, VD: schedule-shutdown | stop-db-instance)"
          }
        },
        "audit_trail_context": {
          "action_triggered_by": "string (Phương thức kích hoạt: system_auto_containment | human_operator)",
          "pre_action_state": "string (Trạng thái tài nguyên trước can thiệp: running | active)",
          "post_action_state": "string (Trạng thái tài nguyên sau can thiệp: stopped | tagged | restricted)",
          "execution_iam_role": "string (Mã vai trò IAM thực thi tác động vật lý thật)",
          "rollback_script_encapsulated": "string (Câu lệnh AWS CLI đảo ngược/khôi phục lại trạng thái cũ)"
        }
      }
    }
  ],
  "pagination": {
    "next_token": "string (Nullable - Token cho trang tiếp theo, null nếu hết dữ liệu)",
    "limit": "int (Số bản ghi tối đa trên một trang, mặc định: 50)"
  }
}
```

##### Request Example
`GET /v1/detect/result/8f3b610c-18a4-4e2b-9801-bde901844b20?limit=1&next_token=page2-token-example`

##### Response Example (Trạng thái completed - Môi trường Staging)
```json
{
  "audit_id": "8f3b610c-18a4-4e2b-9801-bde901844b20",
  "status": "completed",
  "total_anomalies_found": 1,
  "anomalies_list": [
    {
      "anomaly_metadata": {
        "anomaly_id": "ANM-2026-0623A",
        "timestamp": "2026-06-23T17:05:46Z",
        "resource_id": "arn:aws:rds:us-east-1:200000000012:db:db-staging-orphan-01",
        "environment": "staging",
        "confidence_score": 0.94,
        "ai_model_used": "amazon.nova-pro-v1:0"
      },
      "finance_dashboard_data": {
        "target_recipient": "Finance Team & CFO Dashboard",
        "metrics": {
          "unblended_cost_24h_usd": 27.84,
          "cost_ratio_to_7d_avg": 12.4,
          "projected_monthly_waste_usd": 835.20
        },
        "allocation": {
          "responsible_team": "data-eng",
          "cost_center_code": "CC-2002"
        },
        "executive_summary": "Hệ thống phát hiện một cơ sở dữ liệu AmazonRDS trên môi trường Staging đang lãng phí ngân sách doanh nghiệp, tiêu tốn $27.84/ngày. Tài nguyên này hiện không mang lại giá trị vận hành thực tế và đang làm chi phí của đội data-eng vượt 12.4 lần so với baseline tuần trước."
      },
      "engineering_dashboard_data": {
        "target_recipient": "Engineering Console & Slack Alert",
        "technical_context": {
          "aws_service": "AmazonRDS",
          "usage_type": "db.r5.2xlarge:ProvisionedStorage",
          "pricing_unit": "Hrs",
          "usage_amount_24h": 24.0,
          "usage_density_24h": 1.0
        },
        "root_cause_analysis": {
          "primary_driver_feature": "usage_density_24h",
          "technical_reason": "Cơ sở dữ liệu RDS instance loại db.r5.2xlarge bị bỏ hoang sau đợt kiểm thử di trú dữ liệu của đội data-eng. Máy chủ duy trì trạng thái vận hành hết công suất liên tục 24/24 (usage_density = 1.0) nhưng ghi nhận số lượng kết nối (Active Connections) tiệm cận bằng 0 trong suốt 10 tuần qua.",
          "missing_mandatory_tags": [
            "resource_tags_user_owner"
          ]
        },
        "mitigation_action": {
          "strategy": "Time-gated Containment (Staging Rules)",
          "immediate_action": "tag-for-review",
          "applied_payload": {
            "action_type": "inject_aws_tag",
            "tag_key": "FinOps_Alert",
            "tag_value": "Staging_Review_Required",
            "aws_cli_command": "aws rds add-tags-to-resource --resource-name arn:aws:rds:us-east-1:200000000012:db:db-staging-orphan-01 --tags Key=FinOps_Alert,Value=Staging_Review_Required"
          },
          "enforcement_countdown": {
            "time_lock_seconds": 14400,
            "fallback_action": "schedule-shutdown"
          }
        },
        "audit_trail_context": {
          "action_triggered_by": "system_auto_containment",
          "pre_action_state": "running",
          "post_action_state": "tagged",
          "execution_iam_role": "arn:aws:iam::200000000012:role/FinOpsEngineExecutionRole",
          "rollback_script_encapsulated": "aws rds remove-tags-from-resource --resource-name arn:aws:rds:us-east-1:200000000012:db:db-staging-orphan-01 --tag-keys FinOps_Alert"
        }
      }
    }
  ],
  "pagination": {
    "next_token": null,
    "limit": 50
  }
}
```

##### Response Example (Trạng thái processing - Đang xử lý)
```json
{
  "audit_id": "8f3b610c-18a4-4e2b-9801-bde901844b20",
  "status": "processing",
  "message": "AI Engine is still processing cost anomaly detection."
}
```

---

### 5.3 Endpoint 3: `POST /v1/action/extend`

**Mục đích**: CDO Platform gọi API này khi kỹ sư nhấn nút "Gia hạn" (Extend/Snooze) cho tài nguyên đang bị cảnh báo đếm ngược ở môi trường Staging (countdown 4 giờ), giúp hoãn hành động tắt máy.

#### Request Headers
Yêu cầu đính kèm `X-Tenant-Id` và `Authorization` để xác thực quyền sở hữu phiên.

#### Request Body
```json
{
  "audit_id": "UUID (Required - ID phiên kiểm toán phát hiện bất thường)",
  "extend_seconds": "int (Required - Số giây muốn kéo dài thêm, ví dụ: 14400 cho 4 giờ)",
  "reason": "string (Required - Lý do gia hạn từ kỹ sư)"
}
```

##### Request Example
```json
{
  "audit_id": "8f3b610c-18a4-4e2b-9801-bde901844b20",
  "extend_seconds": 14400,
  "reason": "Running long-term load testing on Staging environment until tonight"
}
```

#### Response Body (`200 OK`)
```json
{
  "audit_id": "8f3b610c-18a4-4e2b-9801-bde901844b20",
  "status": "extended",
  "new_expiration_time": "RFC3339 UTC Timestamp (Thời điểm hết hạn mới)",
  "message": "Countdown extended successfully."
}
```

##### Response Example
```json
{
  "audit_id": "8f3b610c-18a4-4e2b-9801-bde901844b20",
  "status": "extended",
  "new_expiration_time": "2026-06-25T18:00:00Z",
  "message": "Staging containment countdown extended by 4 hours."
}
```

---

### 5.4 Endpoint 4: `POST /v1/action/rollback`

**Mục đích**: CDO Platform gọi API này khi kỹ sư chọn "Khôi phục" (Rollback/Restore) một tài nguyên từ dashboard. API kiểm tra xác thực người dùng, trả về lệnh phục hồi vật lý tương ứng lưu trong DynamoDB và đánh dấu hoàn tác trong Audit Log.

#### Request Headers
Yêu cầu đính kèm `X-Tenant-Id` và `Authorization` để xác thực quyền khôi phục.

#### Request Body
```json
{
  "audit_id": "UUID (Required - ID phiên kiểm toán muốn phục hồi)",
  "requested_by_user": "string (Required - Email của kỹ sư thực hiện khôi phục để lưu audit log)",
  "justification_on_rollback": "string (Required - Giải trình lý do phục hồi tài nguyên để kiểm toán)"
}
```

##### Request Example
```json
{
  "audit_id": "8f3b610c-18a4-4e2b-9801-bde901844b20",
  "requested_by_user": "bao.nguyen@company.com",
  "justification_on_rollback": "Need to resume training job for Project SecureDocs AI, approved by Team Lead"
}
```

#### Response Body (`200 OK`)
Trả về payload phục hồi và lệnh AWS CLI tương ứng để CDO Platform thực thi.

```json
{
  "audit_id": "UUID",
  "status": "rollback_initiated",
  "rollback_payload": {
    "action_type": "string (Kiểu phục hồi: start_instance | restore_quota | remove_aws_tag)",
    "aws_cli_rollback_command": "string (Lệnh AWS CLI tương ứng để phục hồi tài nguyên thật)",
    "original_resource_id": "string (ID tài nguyên bị tác động)"
  },
  "message": "string"
}
```

##### Response Example (Khôi phục một RDS instance đã bị tắt trên Staging)
```json
{
  "audit_id": "8f3b610c-18a4-4e2b-9801-bde901844b20",
  "status": "rollback_initiated",
  "rollback_payload": {
    "action_type": "start_instance",
    "aws_cli_rollback_command": "aws rds start-db-instance --db-instance-identifier db-staging-orphan-01",
    "original_resource_id": "arn:aws:rds:us-east-1:200000000012:db:db-staging-orphan-01"
  },
  "message": "Rollback CLI command generated. CDO execution worker authorized to restore resource."
}
```

---

### 5.5 Endpoint 5: `GET /health` (Alb Port 8080 Health Check)

**Mục đích**: AWS Application Load Balancer (ALB) và ECS Fargate gọi định kỳ (chu kỳ 30s) để kiểm tra tính sẵn sàng của Container. 

*Không yêu cầu xác thực chéo (Không bắt buộc SigV4/X-Tenant-Id).*

#### Response Body (`200 OK`)
```json
{
  "status": "healthy",
  "timestamp": "2026-06-25T10:00:00Z",
  "services": {
    "dynamodb": "connected",
    "bedrock_api": "accessible"
  }
}
```

---

## 6. Service Level Objectives (SLO)

| Metric | Target | How to measure |
|---|---|---|
| **Ingestion Latency (P99)** | < 50 ms | Phản hồi `202 Accepted` ngay sau khi ghi nhận request hợp lệ từ CDO. |
| **Result Query Latency (P99)** | < 10 ms | Thời gian đọc bản ghi kết quả phân tích từ DynamoDB Store. |
| **LLM Inference SLA** | < 30 giây | Thời gian gọi Amazon Bedrock (Nova LLM) & viết kết quả vào DB. |
| **System Availability** | 99.5% | Tính sẵn sàng của API Gateway & Load Balancer. |
| **Error Rate** | < 0.5% | Tỷ lệ các phản hồi lỗi hệ thống (5xx) trên tổng số request. |

---

## 7. Error Handling

Hệ thống trả về các mã lỗi HTTP chuẩn, yêu cầu CDO xử lý theo kịch bản:

| HTTP Code | Internal Code | Error Scenario | CDO Mitigation Action |
|---|---|---|---|
| **`400 Bad Request`** | `ERR_INVALID_SCHEMA` | Dữ liệu đầu vào sai Schema hoặc thiếu `X-Tenant-Id`. | Kiểm tra log, sửa mã nguồn client, **KHÔNG** gửi lại. |
| **`400 Bad Request`** | `ERR_IDEMPOTENCY_MISMATCH` | Sử dụng lại `X-Idempotency-Key` nhưng gửi kèm Request Body khác (khác lượng cost, khác file S3). | CDO sửa logic tạo key, **KHÔNG** gửi lại. |
| **`401 Unauthorized`**| `ERR_AUTH_FAILED` | Xác thực IAM SigV4 thất bại hoặc token hết hạn. | Làm mới thông tin xác thực và thử lại một lần. |
| **`403 Forbidden`** | `ERR_CROSS_TENANT_DENIED` | Gọi API với `audit_id` hợp lệ nhưng không thuộc về `X-Tenant-Id` của Header. | Cảnh báo bảo mật hệ thống, chặn luồng xử lý. |
| **`404 Not Found`** | `ERR_AUDIT_NOT_FOUND` | Gọi API với `audit_id` không tồn tại trên hệ thống. | Kiểm tra mã `audit_id` đầu vào, không gọi lại. |
| **`409 Conflict`** | `ERR_DUP_IDEMPOTENCY` | Trùng khóa `X-Idempotency-Key` khi tiến trình đang chạy. | Polling chờ kết quả, không gửi request mới đè lên. Trả về mã lỗi HTTP 409 kèm theo header Retry-After: 30 để CDO Platform tự động cấu hình Worker ngủ (sleep) 30 giây trước khi thực hiện Polling lại kết quả|
| **`422 Unprocessable Entity`** | `ERR_ROLLBACK_NOT_SUPPORTED` | Gọi Rollback cho tài nguyên không hỗ trợ hoàn tác (ví dụ: môi trường `prod` chỉ gắn tag, hoặc tài nguyên đã bị xóa vật lý). | Báo cáo SRE kiểm tra thủ công, không thử lại. |
| **`422 Unprocessable Entity`** | `ERR_ALREADY_ROLLED_BACK` | Gọi Rollback cho tài nguyên đã được phục hồi trước đó. | Báo cáo SRE trạng thái hiện tại, tắt tiến trình. |
| **`429 Too Many Requests`** | `ERR_RATE_LIMITED` | Vượt quá hạn mức gọi API quy định của Tenant. | Áp dụng thuật toán chờ đợi tăng dần (Exponential backoff). |
| **`500 Internal Error`** | `ERR_LLM_TIMEOUT` | **Bedrock xử lý vượt quá 45 giây (Hard Timeout)**. | AI Engine tự hủy luồng suy luận, ghi nhận kết quả `failed` trong DB. CDO polling phát hiện mã này sẽ **lập tức chuyển sang đường dẫn Fallback (Rule-Based alert)** để cảnh báo SRE bằng quy tắc tĩnh. |
| **`503 Unavailable`**| `ERR_SERVICE_DOWN` | Nền tảng AI bị sập hoặc Bedrock bị Throttling >60%. | Kích hoạt hệ thống cảnh báo tĩnh Fallback ngay lập tức. |

### 7.1. Bổ sung các Edge Cases & Phòng Vệ Hệ Thống

| HTTP Code | Internal Code | Error Scenario (Trường hợp xảy ra) | CDO Mitigation Action |
|---|---|---|---|
| **`400 Bad Request`** | `ERR_AD_HOC_BUDGET_EXCEEDED` | Vượt quá hạn mức 5 lần gọi quét khẩn cấp (`is_ad_hoc: true`) trong ngày của một Tenant. | Chuyển luồng sang chu kỳ Batch 24h tiêu chuẩn, cấm gọi ad-hoc. |
| **`422 Unprocessable Entity`** | `ERR_RESOURCE_NOT_FOUND` | Gọi tác vụ Rollback/Extend nhưng tài nguyên vật lý đã bị xóa khỏi hạ tầng AWS. | Hủy bỏ lệnh đếm ngược, ghi nhận log Audit Trail trạng thái: `Resource_Deleted_External`. |
| **`422 Unprocessable Entity`** | `ERR_STATE_CONFLICT` | Xung đột trạng thái can thiệp (Ví dụ: Kỹ sư thao tác đè lệnh lên nhau trên Dashboard). | Tải lại giao diện để cập nhật trạng thái mới nhất từ DynamoDB. |

### 7.2. Quy tắc hạch toán dữ liệu tạm tính (Estimated Data Boundary)
Khi cờ hạch toán dữ liệu vĩ mô hoặc vi mô nhận giá trị `"is_estimated": true`, AI Engine sẽ áp dụng cơ chế phòng vệ tự động:
1. Hạ điểm tin cậy thuật toán (`confidence_score`) về mức mặc định `< 0.50`.
2. Toàn bộ kịch bản xử lý bắt buộc phải gán nhãn `"immediate_action": "tag-for-review"` (Chế độ phòng vệ Alert-only). 
3. Câu lệnh thực thi hạ tầng thật bắt buộc phải trả về chuỗi rỗng: `"aws_cli_command": ""`, triệt tiêu hoàn toàn khả năng can thiệp tự động nhầm lẫn khi dữ liệu chưa được AWS chốt sổ hóa đơn.

---

## 8. Open Questions

- [x] **Q1:** Có cần hỗ trợ cơ chế Webhook Callback từ AI Engine sang CDO Platform khi hoàn thành tiến trình Async không?
  - *Giải quyết:* Không cần thiết cho chu kỳ Batch 24 giờ. CDO Platform sẽ chịu trách nhiệm lập lịch (EventBridge cron) kích hoạt và thực hiện thăm dò trạng thái (Polling) kết quả qua Endpoint 2 sau thời gian chờ dự kiến.
- [x] **Q2:** CDO Platform cần làm gì khi nhận được câu lệnh AWS CLI can thiệp từ AI Engine?
  - *Giải quyết:* CDO Platform cần đọc trường `mitigation_action.immediate_action`. Đối với các môi trường thấp (`dev`, `sandbox`, `ml-research`), nếu `confidence_score >= 0.80`, CDO tự động kích hoạt Worker thực thi trực tiếp câu lệnh CLI được cung cấp trong `applied_payload.aws_cli_command` trên hạ tầng AWS thật. Đối với môi trường cao (`prod`), chỉ ghi nhận tag và bắn Slack alert.
