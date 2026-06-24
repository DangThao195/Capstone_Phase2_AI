# Tài liệu Nghiên cứu Thuật toán Anomaly Detection & GenAI RCA

* **Mã Task Jira:** `[AI-RESEARCH]`
* **Thành viên thực hiện:** Trường, Thảo, Hảo
* **Trạng thái:** Hoàn thành Thiết kế Khung Hệ thống Lai Single-Shot & Kịch bản Can thiệp Toàn diện (W11 T5)

---

## 1. Cơ Chế Nạp Dữ Liệu Gom Cụm Một Lần (Single-Shot Ingestion Structure)

Để tối ưu hóa hiệu năng mạng nội bộ và triệt tiêu độ trễ giao tiếp (API Round-trips), dự án loại bỏ cơ chế gọi dữ liệu 2 giai đoạn độc lập. Thay vào đó, cứ mỗi chu kỳ 24 giờ, CDO Platform sẽ đóng gói và gửi sang AI Engine **một Payload JSON duy nhất** chứa đồng thời:
* Dữ liệu vĩ mô tổng hợp theo ngày (`aws_cost_explorer_daily`).
* Danh sách chi tiết các dòng log tài nguyên vi mô (`aws_cur_line_items`) thuộc chu kỳ hạch toán đó.

---

## 2. Đặc Tả Input Đầu Vào Từ CDO (Raw Interface Specification)

Do hệ thống đang trong giai đoạn thử nghiệm sàng lọc và tối ưu hóa các phương pháp phát hiện (Feature Selection & Model Testing), AI Engine yêu cầu CDO Platform **gửi toàn bộ 100% các cột dữ liệu thô** từ các tệp tin hạch toán mẫu. Toàn bộ logic tiền xử lý, bóc tách đặc trưng hoặc loại bỏ thuộc tính nhiễu sẽ do đội AI tự đảm nhiệm bên trong Container.

### 2.1. Khối Dữ Liệu Vĩ Mô Hàng Ngày (Khớp 100% với `cost_explorer_daily.csv`)
CDO Platform đẩy mảng dữ liệu JSON của chu kỳ 24h chứa đầy đủ các trường sau:
* `date` (String/Format YYYY-MM-DD): Ngày hạch toán dòng tiền.
* `linked_account_id` (Int64): ID tài khoản AWS thành viên phát sinh chi phí.
* `linked_account_name` (String): Tên định danh môi trường của tài khoản (`prod-core`, `staging`, `dev`, `ml-research`, `data-analytics`).
* `service` (String): Tên hiển thị thương mại của dịch vụ AWS.
* `service_code` (String): Mã code vĩ mô của dịch vụ phục vụ gom nhóm toán học (Ví dụ: `AmazonEC2`, `AmazonRDS`).
* `region` (String): Vùng vật lý triển khai hạ tầng (`us-east-1`, `ap-southeast-1`).
* `unblended_cost` (Float64): Chi phí thô chưa áp giảm giá của ngày hạch toán.
* `is_estimated` (Boolean): Cờ trạng thái ước tính số liệu của AWS.

### 2.2. Khối Dữ Liệu Vi Mô Chi Tiết Dòng Log (Khớp 100% với `cur_line_items.csv`)
Khi phát hiện nghi ngờ hoặc chuyển tiếp dữ liệu ngữ cảnh cho GenAI, Payload đính kèm từ CDO phải chứa toàn vẹn các thuộc tính log chi tiết sau:
* `identity_line_item_id` (String): ID duy nhất của dòng log hạch toán CUR.
* `bill_billing_period_start_date` & `bill_billing_period_end_date` (String): Chu kỳ lập hóa đơn doanh nghiệp.
* `line_item_usage_start_date` & `line_item_usage_end_date` (String): Thời gian chạy máy thực tế của tài nguyên.
* `line_item_line_item_type` (String): Phân loại chi phí hệ thống (`Usage`, `Tax`, `Fee`...).
* `line_item_usage_type` (String): Mã chi tiết cấu hình tài nguyên vật lý chạy máy (Ví dụ: `BoxUsage:p3.2xlarge`).
* `line_item_operation` (String): Thao tác vận hành hạ tầng đám mây hệ thống.
* `line_item_usage_amount` (Float64): Khối lượng tiêu thụ vật lý đo đạc được.
* `line_item_normalization_factor` (Float64) & `line_item_normalized_usage_amount` (Float64): Số liệu quy đổi chuẩn hóa.
* `line_item_currency_code` (String): Mã tiền tệ (Mặc định: `USD`).
* `line_item_unblended_cost` (Float64): Chi phí vi mô phát sinh cục bộ của riêng tài nguyên đó.
* `line_item_resource_id` (String): ID vật lý duy nhất của thiết bị (Mã ARN của RDS, Instance ID của EC2...) phục vụ làm bia can thiệp vật lý thật.
* `resource_tags_user_environment` (String): Tag môi trường phục vụ phân luồng kịch bản an toàn.
* `resource_tags_user_owner` (String): Tag tên kỹ sư chịu trách nhiệm sở hữu tài nguyên.
* `resource_tags_user_team` (String): Tag đội nhóm dự án trực thuộc phục vụ gán thâm hụt tiền.
* `resource_tags_user_cost_center` (String): Tag mã trung tâm chi phí hạch toán doanh nghiệp.

---

## 3. Kiến Trúc Lai Phức Hợp Với Amazon Nova GenAI (RCA Engine)

Dự án ứng dụng dòng mô hình thế hệ mới **Amazon Nova (Amazon Nova Pro / Nova Lite)** qua hạ tầng Amazon Bedrock để thực hiện chuỗi tư duy suy luận chuyên sâu (Chain-of-Thought) thay thế cho Rule-based truyền thống:

* **LLM Stage 1 (Root Cause Analysis Engine):** Phân tích logic chéo giữa biến phái sinh kỹ thuật (`usage_density_24h`) và các log CUR vi mô để chỉ mặt đặt tên nguyên nhân gốc rễ (Root Cause) bằng ngôn từ tài chính hữu hảo (Finance-friendly), che giấu các thuật ngữ toán học trừu tượng. Nếu trường `owner` bị rỗng (`NaN`), mô hình tự động kết luận lỗi *Mis-tagged Spend* (Vi phạm luật Tag doanh nghiệp).
* **LLM Stage 2 (Mitigation Action Engine):** Đọc thuộc tính `resource_tags_user_environment` để đối chiếu ma trận an toàn và sinh ra mã lệnh AWS CLI can thiệp chính xác phân luồng theo 5 vùng môi trường cụ thể ở Mục 4.

---

## 4. Đặc Tả Kịch Bản Chi Tiết Xử Lý Trên 5 Vùng Môi Trường (Mitigation Engineering)

### 4.1. Môi trường Production (`prod`) - Tuyệt đối an toàn (Human-in-the-loop)
* **Chiến lược:** Đảm bảo an toàn tuyệt đối cho tính liên tục của hệ thống kinh doanh tạo ra doanh thu. Nghiêm cấm hoàn toàn hành vi tự động tắt máy hoặc hạ cấp hạ tầng.
* **Kịch bản thực thi vật lý:** Hệ thống gọi API để thực hiện giải pháp **`tag-for-review`**. Đính kèm nhãn cảnh báo trực tiếp lên tài nguyên AWS thông qua câu lệnh CLI giả lập:
  ```bash
  aws ec2 create-tags --resources <line_item_resource_id> --tags Key=FinOps_Alert,Value=Review_Required

* **Luồng cảnh báo (Escalation):** Đóng gói dữ liệu và đẩy thông báo khẩn sang kênh Slack của đội SRE / DevOps Squad. Kỹ sư SRE chịu trách nhiệm kiểm tra trực quan và chủ động bấm nút xử lý bằng tay trên Dashboard.

### 4.2. Môi trường Tạm thời (staging) - Cơ chế Hạn định (Time-gated Alert)

**Chiến lược:** Bảo vệ tiến trình kiểm thử tải dài hạn của đội ngũ QA/QC, nhưng thiết lập hàng rào chặn đứng tình trạng tài nguyên chạy lãng phí xuyên tuần.

**Kịch bản thực thi vật lý (Giai đoạn 1 - Alert):** Tự động kích hoạt API gắn tag hạn định và phát thông báo Slack trực tiếp cho đội quản lý sở hữu tài nguyên:

```bash
aws ec2 create-tags --resources <line_item_resource_id> --tags Key=FinOps_Alert,Value=Staging_Review_Countdown
```

**Luồng cưỡng chế (Giai đoạn 2 - Enforcement):** Kích hoạt Worker thiết lập bộ đếm ngược Time-lock 4 giờ (14,400 giây). Sau khi hết giờ, nếu không ghi nhận lệnh Gia hạn (Extend) từ kỹ sư, Webhook của CDO Platform sẽ tự động gọi AWS API cưỡng chế tắt máy thật để cắt giảm dòng tiền:

```bash
aws rds stop-db-instance --db-instance-identifier <line_item_resource_id>
```

### 4.3. Môi trường Phát triển & Thử nghiệm (dev / sandbox) - Auto-Containment Vùng Thấp

**Chiến lược:** Vùng có tính chất lãng phí dòng tiền cao do thói quen quên tắt máy của lập trình viên, nhưng có bán kính rủi ro hệ thống nhỏ nhất. Ưu tiên xử lý tự động để triệt tiêu lãng phí tức thì.

**Kịch bản thực thi vật lý:** Nếu điểm tin cậy thuật toán từ Amazon Nova trả về $Confidence \ge 0.80$, hệ thống BỎ QUA bước chờ đợi phản hồi, lập tức gọi API can thiệp cưỡng chế vật lý thật ngay trong đêm:

```bash
aws ec2 stop-instances --instance-ids <line_item_resource_id>
```

### 4.4. Môi trường Nghiên cứu & Phát triển AI (ml-research) - Auto-Containment Đặc Thù

**Chiến lược:** Đội ngũ AI Researcher thường bật các cụm GPU cấu hình cực khủng (Ví dụ dòng instance p3.2xlarge) để huấn luyện mô hình rồi treo máy không tải (idle). Đây là môi trường có nguy cơ thâm hụt ngân sách nhanh nhất.

**Kịch bản thực thi vật lý:** Tương tự vùng dev, nếu $Confidence \ge 0.80$ và xác nhận thiết bị không có active job chạy ngầm, kích hoạt chế độ Auto-Containment. Gọi lệnh ngắt tài nguyên tính toán cao cấp lập tức:

```bash
aws sagemaker stop-notebook-instance --notebook-instance-name <line_item_resource_id>
```

### 4.5. Môi trường Phân tích Dữ liệu (data-analytics) - Khóa Trần Hạn Ngạch (Quota-Cap)

**Chiến lược:** Đội Data thường chạy các tác vụ kéo, nạp dữ liệu nặng (ETL) hoặc truy vấn báo cáo lớn, chi phí biến động hình răng cưa. Rủi ro lớn nhất ở đây là việc dính vòng lặp loop truy vấn lỗi làm vọt chi phí lên hàng ngàn USD/ngày.

**Kịch bản thực thi vật lý:** Để tránh làm hỏng cấu trúc file hoặc gây corrupted dữ liệu khi DB đang ghi dữ liệu lớn bằng lệnh tắt ngang, Amazon Nova sẽ chuyển đổi sang giải pháp Áp đặt giới hạn chi phí trần thông qua Service Quotas API để bóp băng thông/hạn mức xử lý:

```bash
aws service-quotas request-service-quota-increase --service-code <service_code> --quota-code <quota_code> --desired-value <safe_low_budget_value>
```

**Audit Trail cho các vùng can thiệp vật lý thật (4.3, 4.4, 4.5):** Hệ thống bắt buộc phải ghi log chi tiết cấu trúc Rollback (Ví dụ: lệnh aws ec2 start-instances tương ứng) vào DynamoDB Audit Store, lưu trữ nhật ký với thời gian $\text{Retention} \ge 90 \text{ ngày}$ phục vụ công tác kiểm toán và cho phép kỹ sư khôi phục lại máy khi cần thiết.

## 5. Định Dạng Cấu Trúc Dữ Liệu Đầu Xuất (Multi-Dashboard JSON Output Specification)

Sau khi mô hình Amazon Nova hoàn thành suy luận RCA và chọn lựa giải pháp môi trường ở Mục 4, nó xuất ra một cấu trúc JSON duy nhất nhưng được phân rã thành 2 khối dữ liệu độc lập để đáp ứng nhu cầu hiển thị giao diện phân tách:

```json
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
        "tag_value": "Staging_Review_Required"
      },
      "enforcement_countdown": {
        "time_lock_seconds": 14400,
        "fallback_action": "schedule-shutdown"
      }
    }
  }
}
```

### 5.1. Vai trò phân tách hiển thị giao diện:

**Giao diện Finance Dashboard:** Hệ thống frontend bóc tách khối `finance_dashboard_data` để vẽ biểu đồ cột thâm hụt tiền, phân bổ dòng tiền oan về cho Quản lý của đội data-eng chịu trách nhiệm theo mã trung tâm chi phí CC-2002, che giấu hoàn toàn các thông số kỹ thuật ARN phức tạp.

**Giao diện Engineering Console:** Hệ thống frontend bóc tách khối `engineering_dashboard_data` để hiển thị trực diện mã lỗi cấu hình thiết bị (db.r5.2xlarge), chỉ rõ ID thiết bị để Kỹ sư bấm nút xử lý nhanh, đồng thời đẩy mã Payload sang cho CDO thực thi đếm ngược 4 tiếng tắt máy trên hạ tầng thật.
