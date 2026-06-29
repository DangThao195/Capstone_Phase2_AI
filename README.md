# TF2 AIOps AI Engine (v1.3.0 Skeleton)

Tài liệu này hướng dẫn cách chạy, cấu hình và bàn giao hệ thống **AI Engine Skeleton v1.3.0** cho nhóm CDO-01 và CDO-02 để kiểm thử tích hợp (Integration Testing).

---

## 1. Kiến Trúc Vận Hành (Operating Architecture)
AI Engine được thiết kế dưới dạng **Multi-tenant API** dùng chung (host ONCE). 
*   **Downstream CDO**: CDO-01 và CDO-02 cùng gọi chung vào một endpoint của AI Engine, phân biệt bằng header bắt buộc `X-Tenant-Id`.
*   **Offline Rollback (Boto3)**: CDO thực hiện cache trường `rollback_payload.boto3_equivalent` nhận được từ API `/v1/decide` vào DB cục bộ. Khi AI Engine gặp sự cố mạng (503/Timeout), CDO có thể tự động chạy rollback trực tiếp qua SDK boto3 một cách độc lập.
*   **Error Budget Lock**: Mỗi khi có rollback, AI Engine sẽ trừ 0.5% ngân sách lỗi của Tenant đó. Nếu vượt ngưỡng cho phép, Tenant sẽ bị khóa cơ chế tự động ngăn chặn (Containment locked) và chuyển về chế độ chỉ dry-run để đảm bảo an toàn.

---

## 2. Quản Lý Hạ Tầng ECR (Terraform IaC)

Thư mục `./terraform` chứa cấu hình khởi tạo AWS ECR phục vụ đóng gói container với chính sách bảo mật chuyên nghiệp:
*   Mã hóa KMS và Tự động quét mã độc (`scan_on_push`).
*   Phân quyền liên tài khoản (Cross-Account Repository Policy) cho CDO.

### Lệnh khởi tạo ECR:
1. Sao chép cấu hình biến môi trường:
   ```bash
   cd Capstone_Phase2_AI/terraform
   cp terraform.tfvars.example terraform.tfvars
   ```
2. Mở file `terraform.tfvars` và điền chính xác Account ID của CDO.
3. Khởi tạo và áp dụng:
   ```bash
   terraform init
   terraform apply -auto-approve
   ```
   *Lưu lại đường dẫn `ecr_repository_url` được xuất ra màn hình.*

---

## 3. Đóng Gói Và Đẩy Docker Image

Để bàn giao container cho bên CDO, bạn thực hiện build và push lên AWS ECR:

1. Đăng nhập Docker vào ECR (Thay thế `<your_aws_account_id>` bằng ID thật của bạn):
   ```bash
   docker login -u AWS -p $(aws ecr get-login-password --region ap-southeast-1) <your_aws_account_id>.dkr.ecr.ap-southeast-1.amazonaws.com
   ```
2. Khởi tạo Docker Image cục bộ:
   ```bash
   cd Capstone_Phase2_AI/engine-skeleton
   docker build -t finops-engine:v1.3.0 .
   ```
3. Gắn thẻ và Push lên ECR:
   ```bash
   docker tag finops-engine:v1.3.0 <your_aws_account_id>.dkr.ecr.ap-southeast-1.amazonaws.com/tf-2-ai-engine:v1.3.0
   docker push <your_aws_account_id>.dkr.ecr.ap-southeast-1.amazonaws.com/tf-2-ai-engine:v1.3.0
   ```

---

## 4. Hướng Dẫn Cấu Hình Chạy Container (Chuyển sang Production)

Hệ thống sử dụng thư viện `pydantic-settings` để quản lý biến môi trường. Khi CDO deploy container lên AWS ECS Fargate, họ **không cần thay đổi mã nguồn** mà chỉ cần inject các biến môi trường cấu hình thông qua **ECS Task Definition** (hoặc file `.env` nếu chạy cục bộ):

| Biến Môi Trường (Env Var) | Giá Trị Mặc Định | Cấu hình cho Production | Ý nghĩa |
|---|---|---|---|
| `FINOPS_ENVIRONMENT` | `development` | `production` | Chuyển trạng thái hoạt động của Engine sang Prod |
| `FINOPS_PORT` | `8080` | `8080` | Cổng HTTP mà container lắng nghe |
| `FINOPS_DRY_RUN_MODE` | `true` | `false` | Đặt thành `false` để kích hoạt hành động ngăn chặn thật (Live-action) |
| `FINOPS_ENABLE_AUTO_CONTAINMENT` | `false` | `true` | Đặt thành `true` để cho phép AI tự động kích hoạt lệnh ngăn chặn |
| `FINOPS_LOG_LEVEL` | `INFO` | `INFO` / `WARNING` | Cấp độ ghi nhận nhật ký của hệ thống |

### Lệnh chạy container cục bộ (Môi trường Dev):
```bash
docker run -d \
  -p 8080:8080 \
  --name ai-engine \
  -e FINOPS_ENVIRONMENT=development \
  -e FINOPS_DRY_RUN_MODE=true \
  <your_aws_account_id>.dkr.ecr.ap-southeast-1.amazonaws.com/tf-2-ai-engine:v1.3.0
```

### Lệnh chạy container cục bộ (Môi trường Production giả định):
```bash
docker run -d \
  -p 8080:8080 \
  --name ai-engine-prod \
  -e FINOPS_ENVIRONMENT=production \
  -e FINOPS_DRY_RUN_MODE=false \
  -e FINOPS_ENABLE_AUTO_CONTAINMENT=true \
  <your_aws_account_id>.dkr.ecr.ap-southeast-1.amazonaws.com/tf-2-ai-engine:v1.3.0
```

---

## 5. Kiểm Thử API (Postman Smoke Test)
Trong thư mục `engine-skeleton` có sẵn file [postman_smoke_test.json](file:///d:/Cloude-DevOps/Phase-2/final-production/Capstone_Phase2_AI/engine-skeleton/postman_smoke_test.json):
1. Import file này vào Postman.
2. Cấu hình biến `baseUrl` trên Postman khớp với cổng bạn deploy (ví dụ: `http://localhost:8080`).
3. Khởi chạy Runner để Postman tự động xác thực 6 endpoints đồng bộ và kiểm tra phân quyền multi-tenant.
