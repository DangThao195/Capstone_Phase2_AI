# Synthetic Telemetry Generation Report for FinOps Watch

Báo cáo chi tiết quá trình và kết quả sinh dữ liệu vận hành nhân tạo (**synthetic telemetry**) đồng bộ với dữ liệu hóa đơn chi phí AWS (CUR 2.0) phục vụ dự án FinOps Watch.

---

## 📂 Thông tin File Kết quả

Dữ liệu telemetry sinh ra đã được lưu thành công tại:
👉 **[metrics.csv](file:///C:/Users/ASUS/OneDrive/Obsidian%20Vault/XBrain-Phase2/Capstone_Project/data/metrics_data/metrics.csv)**

### 📊 Thống kê Bộ dữ liệu
*   **Tổng số dòng telemetry:** `137,055` dòng
*   **Tổng số tài nguyên (resource_id):** `276` tài nguyên (100% khớp dữ liệu CUR)
*   **Số tài nguyên bất thường (Anomaly):** `11` tài nguyên (5 EC2 Runaway GPU, 1 RDS Idle, 1 EBS Idle Volume, 1 EC2 Untagged, 1 NAT Gateway Spike, 1 CloudWatch Logs Spike, 1 DynamoDB Drift)
*   **Số tài nguyên sự kiện bình thường (Benign):** `3` tài nguyên (1 EC2 Flash Sale, 1 Data Transfer Migration, 1 EC2 Load Test)
*   **Phạm vi thời gian:** `2026-03-01` đến `2026-05-31` (92 ngày)

---

## 🛠️ Phương pháp sinh dữ liệu: Causal Bridge (Cầu nhân quả)

> [!IMPORTANT]
> Dữ liệu telemetry không sinh ngẫu nhiên. Chi phí (**Cost**) trong CUR được coi là **Nguồn Sự thật (Source of Truth)**. Telemetry được tính toán ngược từ Cost kết hợp với **Behavior Template** của từng kịch bản.

Hệ thống đã triển khai sơ đồ ánh xạ logic:
```
AWS CUR Cost (Daily unblended_cost)
       ↓
Tính toán Baseline Cost & Ratio (c / mean_cost)
       ↓
Áp dụng Behavior Template (Idle, Runaway, Drift, Spike, Untagged, Normal)
       ↓
Ánh xạ qua Logical Metric Model (compute_utilization, network_in, workload_rate,...)
       ↓
Sinh các CloudWatch Metrics cụ thể & Units tương ứng theo từng AWS Service
```

### 🧠 Logic mô phỏng các Behavior Templates chính

1.  **Idle Resource (A2, A3):**
    *   *Chi phí:* Vẫn ở mức cao/hằng ngày.
    *   *Vận hành:* CPUUtilization cực thấp (1-3%), DatabaseConnections/IOPS/Network giảm về sát 0.
    *   *Trường hợp EBS (unattached volumes):* VolumeReadOps/VolumeWriteOps/VolumeQueueLength = 0, VolumeIdleTime = 100%, AttachmentState = 0 (unattached).
2.  **Runaway Usage (A1):**
    *   *Chi phí:* Rất cao và liên tục.
    *   *Vận hành:* CPU/GPU utilization chạm trần (88-96%), RAM (82-92%), network/disk I/O rất lớn, uptime 24/7 (86400s/ngày) không giảm tải cuối tuần.
3.  **Sudden Spike (A5, A6):**
    *   *Chi phí:* Tăng đột biến bậc thang trong thời gian ngắn rồi về baseline.
    *   *Vận hành (A5 NAT Gateway):* `BytesTransferred` được tính toán chính xác dựa trên đơn giá xử lý NAT Gateway ($0.045/GB) để giải thích khớp 100% với chi phí ~$520/ngày.
    *   *Vận hành (A6 CloudWatch Logs):* `IncomingBytes` và `IncomingLogEvents` được tính chính xác dựa trên đơn giá ingestion ($0.50/GB) để giải thích khớp với chi phí ~$260/ngày.
4.  **Gradual Drift (A7):**
    *   *Chi phí:* Bò lên từ từ từ ~$60/ngày lên ~$320/ngày.
    *   *Vận hành (DynamoDB):* `ProvisionedWriteCapacityUnits` tăng dần tuyến tính theo cost (tính theo giá $0.0156/WCU-day), trong khi `ConsumedWriteCapacityUnits` và `ConsumedReadCapacityUnits` vẫn giữ mức bình thường thấp, biểu thị lỗi Auto-scaling bị trượt dốc.
5.  **Untagged Spend (A4):**
    *   *Chi phí & Vận hành:* Hoạt động và chi phí hoàn toàn bình thường, tỷ lệ thuận.
    *   *Quản trị:* Chỉ số `TagCompliance` được set về `0.0%` (các tài nguyên khác là `100.0%`).
6.  **Benign Events (B1, B2, B3):**
    *   *Flash Sale (B1):* CPUUtilization (80-92%), Memory (80-90%), Network và Disk I/O tăng cực mạnh tương ứng với chi phí cao. Không có dấu hiệu lỗi.
    *   *Migration Egress (B2):* `BytesTransferred` tăng mạnh dựa trên giá egress ($0.09/GB).
    *   *Load Testing (B3):* CPUUtilization (95-100%), Memory (85-90%) tăng cao theo chu kỳ kiểm thử.

---

## 📐 Cấu trúc Schema File `metrics.csv`

Mỗi dòng dữ liệu trong file kết quả tuân thủ đúng định dạng yêu cầu:

| Cột | Kiểu dữ liệu | Ý nghĩa | Ví dụ |
| :--- | :--- | :--- | :--- |
| `timestamp` | String (RFC3339) | Thời điểm ghi nhận metric (daily grain) | `2026-04-01T00:00Z` |
| `resource_id` | String | ID tài nguyên gốc khớp với CUR | `i-0fbgpu00000000` |
| `service` | String | Tên AWS Service (product code) | `AmazonEC2` |
| `account_id` | String | Linked Account ID | `200000000015` |
| `metric_name` | String | Tên CloudWatch Metric vật lý | `CPUUtilization` |
| `metric_value` | Float | Giá trị đo được | `91.56` |
| `unit` | String | Đơn vị đo lường | `Percent` |
| `is_anomaly` | Boolean | Nhãn bất thường (Ground Truth) | `True` |
| `anomaly_type` | String | Phân loại kịch bản bất thường/sự kiện | `runaway_usage` |

---

## ✅ Báo cáo kết quả Quality Check

Trước khi hoàn tất, hệ thống đã chạy qua bộ kiểm định chất lượng:
*   [x] **Bảo toàn Tài nguyên:** 100% tài nguyên ghi nhận bất thường trong `anomaly_labels_full.csv` đều có dữ liệu telemetry tương ứng.
*   [x] **Khớp ID gốc:** 100% `resource_id` trong file telemetry mới sinh đều tồn tại trong file `cur_line_items.csv`.
*   [x] **Chuẩn hóa Metric Service:** Các chỉ số được gán đúng theo đặc thù dịch vụ (ví dụ: DynamoDB có Capacity Units, RDS có DatabaseConnections, EC2 có CPUUtilization).
*   [x] **Phân biệt Benign vs Anomaly:** Benign event được đánh nhãn `is_anomaly = False` và `anomaly_type = benign_event` nhưng vẫn có telemetry mô phỏng workload cao khớp với chi phí tăng vọt.
*   [x] **Không có giá trị vô lý:** Các chỉ số phần trăm luôn nằm trong khoảng `[0.0, 100.0]`, các chỉ số dung lượng/số lượng luôn lớn hơn hoặc bằng 0.

---

## 📖 Giải thích Chi tiết các Trường Dữ liệu (Data Fields)

Dưới đây là chi tiết các cột dữ liệu có trong file `metrics.csv`:

### 1. `timestamp`
*   **Ý nghĩa:** Thời gian ghi nhận chỉ số (metric).
*   **Định dạng:** Chuỗi thời gian chuẩn ISO 8601 / RFC3339 (mức độ ngày - daily grain).
*   **Ví dụ:** `2026-04-01T00:00Z`

### 2. `resource_id`
*   **Ý nghĩa:** ID duy nhất định danh tài nguyên AWS phát sinh chi phí và dữ liệu vận hành. Trường này dùng để map trực tiếp với cột `line_item_resource_id` trong file hóa đơn AWS CUR.
*   **Ví dụ:** `i-0fbgpu00000000` (EC2 Instance), `arn:aws:rds:us-east-1:acct:db:db-staging-orphan-01` (RDS Database).

### 3. `service`
*   **Ý nghĩa:** Mã dịch vụ AWS cung cấp tài nguyên đó (khớp với `line_item_product_code` trong CUR).
*   **Ví dụ:** `AmazonEC2`, `AmazonRDS`, `AmazonDynamoDB`...

### 4. `account_id`
*   **Ý nghĩa:** ID của Linked Account (tài khoản liên kết trong AWS Organizations) chứa tài nguyên này.
*   **Ví dụ:** `200000000015` (tương ứng với account `ml-research`).

### 5. `metric_name`
*   **Ý nghĩa:** Tên của chỉ số đo lường hiệu năng vận hành cụ thể (CloudWatch Metric Name).
*   **Ví dụ:** `CPUUtilization`, `DatabaseConnections`, `BytesTransferred`, `TagCompliance`.

### 6. `metric_value`
*   **Ý nghĩa:** Giá trị đo lường được của metric tại thời điểm ghi nhận.
*   **Kiểu dữ liệu:** Số thực (Float).
*   **Ví dụ:** `91.56`, `345293.0`.

### 7. `unit`
*   **Ý nghĩa:** Đơn vị đo lường của chỉ số.
*   **Giá trị khả dụng:** `Percent`, `Bytes`, `Count`, `Seconds`, `Milliseconds`, `Bytes/Second`...

### 8. `is_anomaly`
*   **Ý nghĩa:** Nhãn Boolean xác định dòng dữ liệu này có nằm trong khoảng thời gian xảy ra **bất thường thực sự (anomaly)** hay không. Đây là **nhãn Ground Truth** để huấn luyện và đánh giá mô hình.
*   **Giá trị:** `True` (Tài nguyên đang bị bất thường), `False` (Tài nguyên hoạt động bình thường hoặc thuộc Benign Event).

### 9. `anomaly_type`
*   **Ý nghĩa:** Mô tả phân loại chi tiết của bất thường hoặc sự kiện.
*   **Các giá trị chính:**
    *   `normal`: Trạng thái hoạt động bình thường.
    *   `runaway_usage`: Quên tắt tài nguyên, chạy công suất cao 24/7.
    *   `idle_resource`: Lãng phí tài nguyên (chạy nhưng tải trọng ≈ 0).
    *   `sudden_spike`: Tăng vọt đột biến ngắn ngày (như ghi log lặp vô hạn).
    *   `gradual_drift`: Auto-scaling bị trượt dốc, chỉ scale-out không scale-in.
    *   `untagged_spend`: Thiếu nhãn bắt buộc (TagCompliance = 0).
    *   `benign_event`: Sự kiện tăng chi phí hợp lệ (Flash sale, load test định kỳ, migration dữ liệu).
