# AI Engine Skeleton (v1.3.0) — Hướng Dẫn Tích Hợp Cho CDO

Chào mừng nhóm CDO-03 và CDO-06 đến với bản tích hợp **AI Engine Skeleton v1.3.0**. Thư mục này chứa mã nguồn khung (skeleton) đã đồng bộ 100% với các hợp đồng final: **ai-api-contract v1.3.0**, **telemetry-contract v3.1.0**, và **deployment-contract v1.2.0**.

Mọi endpoint và cấu trúc JSON ở đây đều phản ánh chính xác API thật sẽ được bàn giao trong Tuần 12.

---

## 1. Hướng Dẫn Khởi Chạy API Nhanh

### Cách 1: Chạy trực tiếp bằng Python (Local Dev)
1. **Khởi tạo môi trường ảo Python 3.12+**:
   ```bash
   python -m venv .venv
   .venv\Scripts\activate   # Trên Windows
   source .venv/bin/activate # Trên Linux/macOS
   ```
2. **Cài đặt các thư viện liên quan**:
   ```bash
   pip install -r requirements.txt
   ```
3. **Cấu hình môi trường**:
   Sao chép `.env.example` thành `.env` để sử dụng cấu hình mặc định:
   ```bash
   cp .env.example .env
   ```
4. **Khởi chạy ứng dụng**:
   ```bash
   uvicorn main:app --host 0.0.0.0 --port 8085 --reload
   ```
   *Sau khi chạy, tài liệu API (Swagger UI) sẽ khả dụng tại: `http://localhost:8085/docs`*

---

### Cách 2: Chạy bằng Docker (Khuyên dùng cho CDO)
Để mô phỏng chính xác môi trường chạy trên AWS ECS Fargate, bạn có thể build và chạy Docker container cục bộ:

1. **Build Docker Image**:
   ```bash
   docker build -t finops-engine:v1.3.0 .
   ```
2. **Chạy Docker Container**:
   ```bash
   docker run -d \
     -p 8080:8080 \
     --name ai-engine \
     -e FINOPS_PORT=8080 \
     -e FINOPS_ENVIRONMENT=development \
     -e FINOPS_DRY_RUN_MODE=true \
     finops-engine:v1.3.0
   ```
   *Truy cập Swagger UI tại: `http://localhost:8080/docs`*

---

## 2. Danh Sách API Endpoints (v1.3.0)

Hệ thống cung cấp 6 endpoints phục vụ đầy đủ chu trình phát hiện và ngăn chặn tự động:

| Phương thức | Endpoint | Mô tả | Mã trạng thái |
|---|---|---|:---:|
| **GET** | `/health` | Kiểm tra trạng thái kết nối phụ thuộc (`s3_audit_bucket`, `bedrock_api`, `s3_cur_bucket`). | 200 |
| **POST** | `/v1/detect` | Nhận dữ liệu CUR và CPU metrics, trả về kết quả phát hiện đồng bộ. | 200 |
| **GET** | `/v1/status/{id}` | Dual-purpose: Kiểm tra trạng thái detection job (UUID) hoặc tiến trình ngăn chặn (ANM-ID). | 200 |
| **POST** | `/v1/decide` | Phân tích nguyên nhân gốc (RCA), đề xuất hành động và trả về payload CLI/Boto3. | 200 |
| **POST** | `/v1/verify` | Nhận dữ liệu telemetry sau ngăn chặn để đánh giá hiệu quả. Trả về: `DONE`, `RETRY`, `ROLLBACK`, `ESCALATE`. | 200 |
| **POST** | `/v1/audit/{audit_id}/rollback` | CDO gọi để thông báo cho AI sau khi đã tự chạy rollback thành công thông qua Boto3. | 200 |

---

## 3. Quy Tắc Tích Hợp Bắt Buộc (Cho CDO)

### 3.1 Cấu hình HTTP Headers
Tất cả các API (trừ `/health` và tài liệu `/docs`) yêu cầu phải truyền hai headers sau:
*   `X-Tenant-Id`: Mã tenant của CDO (Ví dụ: `cdo-platform-03` hoặc `cdo-platform-06`). Nếu thiếu sẽ bị lỗi `400 Bad Request`.
*   `X-Correlation-Id`: ID trace chuỗi request (UUID v4). Nếu CDO không truyền, AI Engine sẽ tự động sinh và trả về trong Response Header. CDO cần log lại ID này để đối chiếu sự cố.

### 3.2 Cơ chế Offline Rollback (Boto3 Contingency Plan)
Để đảm bảo an toàn tuyệt đối khi AI Engine gặp sự cố (503/Timeout/Mất kết nối):
1. CDO gọi API `/v1/decide` và nhận về cấu trúc `rollback_payload`.
2. CDO **bắt buộc phải lưu trường `rollback_payload.boto3_equivalent`** vào cơ sở dữ liệu (DynamoDB) của mình.
3. Nếu cần thực hiện rollback khẩn cấp nhưng AI Engine không phản hồi, CDO sẽ đọc tham số từ trường này và gọi trực tiếp thư viện SDK Boto3 của họ để hủy ngăn chặn tài nguyên AWS.

### 3.3 Hậu kiểm tra (Verification)
Sau khi CDO thực thi lệnh CLI/Boto3 thành công, CDO phải gọi `/v1/verify` kèm dữ liệu post-telemetry. Nếu API trả về `next_action = ESCALATE`, CDO cần hiển thị thông tin trong `escalation_bundle` lên PagerDuty/Console và thông báo cho kỹ sư hệ thống xử lý thủ công.

---

## 4. Biến Môi Trường Cấu Hình (Environment Variables)

Hệ thống cho phép cấu hình linh hoạt thông qua các biến môi trường (nhập qua Docker `-e` hoặc ECS Task Definition) mà không cần build lại code:

| Tên biến | Mặc định | Gợi ý cho Production | Ý nghĩa |
|---|---|---|---|
| `FINOPS_ENVIRONMENT` | `development` | `production` | Chuyển môi trường hoạt động (hạ thấp quyền tự động ngăn chặn trên Prod) |
| `FINOPS_PORT` | `8080` | `8080` | Cổng HTTP lắng nghe |
| `FINOPS_DRY_RUN_MODE` | `true` | `false` | Đặt `false` để kích hoạt các hành động ngăn chặn thật |
| `FINOPS_ENABLE_AUTO_CONTAINMENT` | `false` | `true` | Cho phép AI tự động kích hoạt lệnh ngăn chặn |
| `FINOPS_ERROR_BUDGET_LOCK_THRESHOLD_PCT` | `1.0` | `1.0` | % Ngân sách lỗi được phép tiêu hao trước khi khóa Auto-containment trên PROD |

---

## 5. Kiểm Thử Với Postman
Bộ smoke test tự động đã được tích hợp sẵn tại file [postman_smoke_test.json](postman_smoke_test.json).
1. Import file này vào Postman của bạn.
2. Kiểm tra biến `baseUrl` đang cấu hình cổng tương ứng (Mặc định: `http://localhost:8085`).
3. Chạy toàn bộ Collection để kiểm tra tự động luồng dữ liệu liên thông từ Detect → Decide → Verify → Rollback.
