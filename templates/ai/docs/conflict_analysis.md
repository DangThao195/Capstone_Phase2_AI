# Báo Cáo Phân Tích Xung Đột (Conflict Analysis) - solution_design.md

Tài liệu này chi tiết các điểm xung đột và không nhất quán giữa file dự thảo cũ **`solution_design.md`** và các tài liệu chuẩn mới:
1. [01_requirements_pm.md](01_requirements_pm.md) (Yêu cầu nghiệp vụ)
2. [ALGORITHM PIPELINE FLOWCHART.md](ALGORITHM%20PIPELINE%20FLOWCHART.md) (Sơ đồ luồng thuật toán)
3. [INTERNAL_RESEARCH_FINOPS.md](INTERNAL_RESEARCH_FINOPS.md) (Nghiên cứu kỹ thuật & đặc tả)

---

## 1. Bảng Tổng Hợp Các Điểm Xung Đột Chính

| Hạng mục so sánh          | Trạng thái trong `solution_design.md`                                                  | Tài liệu chuẩn (Requirements/Research)                                                                                            | Mức độ nghiêm trọng        |
| :------------------------ | :------------------------------------------------------------------------------------- | :-------------------------------------------------------------------------------------------------------------------------------- | :------------------------- |
| **Cơ chế Ingestion**      | CDO tải CUR/Cost Explorer, ghi CSV phẳng lên S3, truyền `data_s3_path` sang AI Engine. | **Single-Shot Bulk Ingestion**: CDO gửi một Payload JSON duy nhất chứa 100% cột thô (macro daily & micro CUR items).              | **Cao (Kiến trúc)**        |
| **Xử lý trễ dữ liệu**     | Không có cơ chế xử lý dữ liệu AWS bị trễ (data lag).                                   | CDO gửi `telemetry_delay_event` -> AI Engine chuyển sang `SUSPENDED` và tự động kiểm tra lại mỗi 1 giờ.                           | **Trung bình (Vận hành)**  |
| **Kiến trúc AI Model**    | Giai đoạn 2 dùng Context Filter / Rule Engine thủ công để lọc bẫy FP.                  | Sử dụng mô hình lai: ML lọc thô (Isolation Forest), sau đó dùng **Amazon Nova (Pro/Lite)** chạy 2 Stage (RCA & Mitigation).       | **Cao (Thuật toán)**       |
| **Ma Trận Can Thiệp**     | Chỉ gắn tag review chung (`tag-for-review`) cho các tài nguyên.                        | **Ma trận 5 môi trường**: `prod` (tag review), `staging` (RDS stop), `dev` (EC2 stop), `ml` (SageMaker stop), `data` (Quota-cap). | **Cao (Rủi ro hệ thống)**  |
| **Mã Lệnh Can Thiệp**     | Không mô tả các câu lệnh CLI hoặc API can thiệp cụ thể.                                | Định nghĩa rõ các câu lệnh CLI giả lập như `aws rds stop-db-instance`, `aws ec2 stop-instances`, `aws sagemaker stop-...`         | **Trung bình**             |
| **Cấu Trúc Đầu Ra**       | Định tuyến cảnh báo đơn giản sang Slack/Jira.                                          | JSON phân tách nghiêm ngặt: `finance_dashboard_data` (Finance) vs `engineering_dashboard_data` (Engineering).                     | **Cao (API Contract)**     |
| **Giới Hạn Ngân Sách**    | Không đề cập giới hạn chi phí hoạt động của AI Engine.                                 | Tích hợp **Circuit Breaker** khống chế chi phí gọi Bedrock dưới **$50 USD / tháng**.                                              | **Trung bình (Tài chính)** |
| **Ranh Giới Đỏ (Safety)** | Chỉ cấm tắt máy trên Prod Accounts bằng chế độ Dry-run chung.                          | Quy định cứng: **NEVER terminate prod, NEVER delete data, NEVER modify IAM**, Audit Trail lưu giữ tối thiểu 90 ngày.              | **Cao (Tuân thủ)**         |

---

## 2. Chi Tiết Các Điểm Xung Đột Nghiệp Vụ & Kỹ Thuật

### 2.1. Phương thức truyền và định dạng dữ liệu đầu vào (Ingestion)
* **Trong `solution_design.md` (Bước 2 & 3):**
  > *"CDO Platform kéo dữ liệu từ CUR (S3) và Cost Explorer API. Chuẩn hóa dữ liệu thô thành tệp CSV phẳng (daily grain) và lưu trữ trên S3 tạm thời... truyền tham số `data_s3_path`..."*
* **Thực tế tài liệu chuẩn (Outcome 1 & Research §1, 2):**
  Hệ thống sử dụng **Single-Shot Bulk Ingestion JSON Payload** truyền trực tiếp toàn bộ 100% cột dữ liệu thô (tương ứng với hai file csv) từ CDO sang AI Engine qua mạng nội bộ. Việc này loại bỏ việc ghi file trung gian trên S3 và giảm số lượt gọi API round-trips.
* **Hậu quả nếu không sửa:** Lệch pha thiết kế API giữa CDO Platform và AI Engine. CDO sẽ không xây dựng Endpoint truyền JSON bulk, gây lỗi khi ráp nối API ở Tuần 12.

### 2.2. Luồng xử lý dị thường và mô hình AI
* **Trong `solution_design.md` (Bước 5):**
  > *"Giai đoạn 1 (ML Filter): Chạy mô hình thống kê nhẹ (Rolling Z-Score)... Giai đoạn 2 (Context Filter): Các điểm nghi ngờ được đối chiếu với danh sách sự kiện nghiệp vụ hợp lệ..."*
* **Thực tế tài liệu chuẩn (Flowchart & Research §3):**
  * Giai đoạn lọc thô sử dụng **Isolation Forest (IF)** hoặc Heuristic để loại bỏ 95% dữ liệu thường.
  * Việc phân tích và định tuyến hành động hoàn toàn do **Amazon Nova (Pro/Lite)** đảm nhiệm thông qua 2 Giai đoạn suy luận (Stage 1 RCA Engine - giải trình tự nhiên và phát hiện thẻ tag lỗi; Stage 2 Mitigation Engine - chọn giải pháp theo ma trận).
* **Hậu quả nếu không sửa:** Không tận dụng được mô hình ngôn ngữ lớn Amazon Nova để tự động lập luận nguyên nhân gốc rễ, hệ thống quay về dùng bộ luật tĩnh (Rule-based) dễ sinh báo động giả và thiếu tính linh hoạt.

### 2.3. Rủi ro vận hành hạ tầng và Ma trận an toàn 5 môi trường
* **Trong `solution_design.md` (Bước 6 & Rủi ro Prod):**
  > *"Thực hiện hành động gắn thẻ cảnh báo (tag-for-review) trên tài nguyên... Ở Prod, hệ thống chỉ chạy ở chế độ Dry-run..."*
* **Thực tế tài liệu chuẩn (Outcome 3 & Research §4):**
  Môi trường được chia làm **5 vùng** với các hành động can thiệp tự động sâu sắc:
  1. `prod`: `tag-for-review` (không phải chỉ là dry-run chung, mà là gắn tag thật bằng lệnh `aws ec2 create-tags`).
  2. `staging`: Tự động tắt máy RDS (`aws rds stop-db-instance`) sau 4 tiếng đếm ngược nếu không có gia hạn.
  3. `dev`: Dừng máy EC2 ngay lập tức (`aws ec2 stop-instances`) nếu điểm tin cậy AI $\ge 0.80$.
  4. `ml-research`: Dừng SageMaker notebook GPU (`aws sagemaker stop-notebook-instance`) nếu AI $\ge 0.80$ và idle.
  5. `data-analytics`: Áp Quota-Cap để bóp băng thông qua Service Quotas API (ở đây mới bắt buộc cấu hình **Dry-run mode**).
* **Hậu quả nếu không sửa:** Gây lãng phí tài nguyên ở các môi trường thấp (do thiếu kịch bản Auto-Shutdown thực tế) hoặc rủi ro làm hỏng cơ sở dữ liệu môi trường Data Analytics nếu tắt nóng thay vì áp trần quota.

### 2.4. Ranh giới đỏ và bảo mật (Compliance Rules)
* **Trong `solution_design.md`:**
  Thiếu hoàn toàn các quy tắc ràng buộc tuân thủ (Compliance Boundaries) của doanh nghiệp.
* **Thực tế tài liệu chuẩn (Requirements §4):**
  * Quy tắc **3 KHÔNG**: KHÔNG bao giờ tắt/xóa tài nguyên Prod, KHÔNG xóa dữ liệu log/file (cấm lệnh delete trên S3/DynamoDB), KHÔNG tự động chỉnh sửa quyền hạn IAM.
  * Phải lưu trữ vết kiểm toán (Audit Trail) cùng snapshot trạng thái và payload rollback tối thiểu **90 ngày**.
* **Hậu quả nếu không sửa:** Thiết kế vi phạm các ranh giới bảo mật nghiêm ngặt của tổ chức, có nguy cơ gây mất mát dữ liệu hoặc làm thay đổi baseline bảo mật của tài khoản AWS.

### 2.5. Mục tiêu tài chính (Circuit Breaker)
* **Trong `solution_design.md`:**
  Không đề cập đến việc tự kiểm soát chi phí của chính hệ thống AI.
* **Thực tế tài liệu chuẩn (Requirements §4):**
  Yêu cầu tích hợp sẵn **Circuit Breaker** khống chế chi phí gọi Bedrock API dưới ngưỡng **$50 USD / tháng**.
* **Hậu quả nếu không sửa:** Khi dữ liệu CUR quá lớn hoặc hệ thống bị spam, chi phí gọi token Bedrock có thể tăng vọt vượt quá hạn mức của tài khoản Capstone.

---

## 3. Khuyến Nghị & Hành Động

> [!NOTE]  
> Tài liệu **`solution_design.md`** là phiên bản **nháp (Draft)** của giai đoạn Tuần 11. Các xung đột trên đã được khắc phục hoàn toàn trong tài liệu cập nhật chính thức mới nhất là **`02_solution_design.md`**.

* **Khuyến nghị:** 
  * Sử dụng [02_solution_design.md](02_solution_design.md) làm tài liệu thiết kế giải pháp chính thức cho dự án để đảm bảo tính nhất quán cao nhất với PM Requirements, Flowchart thuật toán và Nghiên cứu nội bộ.
  * Lưu trữ hoặc đánh dấu file cũ `solution_design.md` là deprecated/lịch sử để tránh gây nhầm lẫn cho các thành viên phát triển trong nhóm AI.
