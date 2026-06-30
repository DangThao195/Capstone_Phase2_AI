# AI Engine ↔ CDO Integration Handshake Specification
**Task Force 2 (FinOps Watch) — Phase 6 Integration Contract**

---

## ⛔ Nguyên tắc thiết kế: Tuyệt đối KHÔNG Hardcode!
Các tài nguyên lưu trữ (DynamoDB Tables, S3 Buckets) chứa các tiền tố/hậu tố động (`{env}`, `{account_id}`). Để đảm bảo Docker container image có thể tái sử dụng (immutable artifact) giữa các môi trường (Dev, Staging, Production) và giữa các nhóm CDO khác nhau, toàn bộ thông số này **bắt buộc phải được cấu hình qua biến môi trường (Environment Variables)** hoặc **phân tách động từ Payload của API Request**.

---

## 1. CDO cung cấp cho AI Team (Infrastructure & Permissions)

Để AI Engine hoạt động trên hạ tầng AWS, nhóm CDO cần cấu hình và cung cấp các thông tin sau:

### 1.1 Cấu hình DynamoDB Tables
CDO cần tạo và quản lý 2 bảng DynamoDB với cấu trúc khóa như sau:

| Tên bảng tham chiếu | Partition Key (Kiểu) | Sort Key (Kiểu) | Mục đích | TTL Attribute |
|---|---|---|---|---|
| `finops-idempotency-{env}` | `idempotency_key` (String) | *None* | Chống trùng request & cache kết quả | `ttl_expiry` (Timestamp) |
| `finops-feature-store-{env}` | `resource_id` (String) | `date` (String, YYYY-MM-DD) | Lưu feature vector 24h đã được ETL sẵn | *None* |

*   **Lưu ý về ETL Feature Store**: CDO chịu trách nhiệm chạy luồng ETL hàng ngày để ghi mảng số liệu 24h vào trường `features` (định dạng Array hoặc JSON string) của bảng `finops-feature-store-{env}`.

### 1.2 Cấu hình S3 Telemetry Bucket
*   **Tên bucket**: `company-cdo-{account_id}-telemetry`
*   **Mục đích**: Chứa file CUR `.json.gz` thô.
*   **AI Engine hoạt động**: Khi CDO gọi API với `data_source_type = S3_POINTER`, AI Engine sẽ đọc file từ bucket này và xác thực checksum SHA-256 được truyền trong request.

### 1.3 Cấp quyền IAM (IAM Task Role Permissions)
ECS Task Role hoặc Lambda Execution Role của AI Engine cần được CDO đính kèm IAM Policy cho phép thực thi:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DynamoDBReadWriteAccess",
      "Effect": "Allow",
      "Action": [
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem"
      ],
      "Resource": [
        "arn:aws:dynamodb:ap-southeast-1:*:table/finops-idempotency-*",
        "arn:aws:dynamodb:ap-southeast-1:*:table/finops-feature-store-*"
      ]
    },
    {
      "Sid": "S3BucketReadAccess",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject"
      ],
      "Resource": "arn:aws:s3:::company-cdo-*-telemetry/*"
    },
    {
      "Sid": "BedrockInvokeModelAccess",
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel"
      ],
      "Resource": "arn:aws:bedrock:ap-southeast-1::foundation-model/amazon.nova-pro-v1:0"
    }
  ]
}
```

---

## 2. AI Team đưa cho CDO (Container & API Configuration)

Để CDO deploy và tích hợp AI Engine vào luồng tự động hóa (Step Functions), AI Team cung cấp các thông tin sau:

### 2.1 URI của Container Image (ECR)
*   **URI**: `197826770971.dkr.ecr.ap-southeast-1.amazonaws.com/tf-2-ai-engine:latest` (Hoặc tag phiên bản cụ thể như `v1.0`)

### 2.2 Danh sách biến môi trường (Environment Variables) cần cấu hình
Khi deploy Container lên ECS/App Runner, CDO cần cấu hình các biến môi trường sau để kích hoạt tích hợp storage thật:

| Tên biến (Env Var) | Giá trị mẫu | Mặc định | Mô tả |
|---|---|---|---|
| `FINOPS_ENVIRONMENT` | `production` | `development` | Môi trường triển khai (`dev`, `staging`, `production`) |
| `FINOPS_PORT` | `8080` | `8080` | Cổng container listen |
| `FINOPS_ENABLE_DYNAMIC_DB` | `true` | `false` | **Bắt buộc = true** để kích hoạt kết nối DynamoDB |
| `FINOPS_ENABLE_S3` | `true` | `false` | **Bắt buộc = true** để đọc file CUR từ S3 |
| `FINOPS_DYNAMODB_IDEMPOTENCY_TABLE` | `finops-idempotency-prod` | `finops-idempotency-dev` | Tên bảng DynamoDB quản lý Idempotency |
| `FINOPS_DYNAMODB_FEATURE_STORE_TABLE` | `finops-feature-store-prod` | `finops-feature-store-dev` | Tên bảng DynamoDB chứa Feature vectors |
| `FINOPS_S3_TELEMETRY_BUCKET` | `company-cdo-093490087544-telemetry` | `""` | Tên S3 bucket chứa dữ liệu telemetry thô |
| `FINOPS_AWS_REGION` | `ap-southeast-1` | `ap-southeast-1` | Vùng AWS của DynamoDB và S3 |
| `FINOPS_BEDROCK_MODEL_ID` | `amazon.nova-pro-v1:0` | `amazon.nova-pro-v1:0` | Model ID của AWS Bedrock sử dụng để phân tích RCA |
| `FINOPS_ENABLE_LLM_ANALYSIS` | `true` | `false` | **Bật = true** để kích hoạt cuộc gọi sang Bedrock thực tế |

### 2.3 Cấu hình Cảnh báo Sống (Health Check Probe)
CDO cấu hình ALB Target Group hoặc App Runner Health Probe gọi đến:
*   **Path**: `/health`
*   **Port**: `8080`
*   **Response**: HTTP `200 OK`
*   **Tần suất gợi ý**: 30 giây / lần.

### 2.4 Cấu trúc Rollback Cache nhận được từ `/v1/decide`
Khi phát hiện bất thường, API `/v1/decide` sẽ trả về payload chứa:
```json
{
  "applied_payload": {
    "boto3_equivalent": "ec2_client.stop_instances(InstanceIds=['i-0123456789abcdef0'])"
  },
  "rollback_payload": {
    "boto3_equivalent": "ec2_client.start_instances(InstanceIds=['i-0123456789abcdef0'])"
  }
}
```
CDO cần bắt lấy `rollback_payload.boto3_equivalent` và ghi vào bộ đệm rollback của mình để đảm bảo tự phục hồi (self-healing) khi có sự cố.
