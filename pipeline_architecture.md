# 🏗️ Kiến trúc Pipeline FinOps Anomaly Detection

Tài liệu này mô tả chi tiết kiến trúc của bộ máy phát hiện bất thường chi phí kết hợp **Cost Data + Performance Telemetry Metrics** không giám sát (Unsupervised Hybrid Pipeline).

---

## 🗺️ SƠ ĐỒ LUỒNG XỬ LÝ (ASCII ARCHITECTURE FLOW)

```
        +---------------------------+       +---------------------------+
        | cur_line_items.csv (Cost) |       |  metrics.csv (Telemetry)  |
        +-------------+-------------+       +-------------+-------------+
                      |                                   |
                      v                                   v
        +-------------+-------------+       +-------------+-------------+
        |  Daily Cost Aggregation   |       |   Group & Pivot Metrics   |
        |  (Resource-level cost)    |       |   (Dạng dọc sang dạng ngang)|
        +-------------+-------------+       +-------------+-------------+
                      |                                   |
                      v                                   |
        +-------------+-------------+                     |
        | Date Padding (Lấp đầy ngày)|                     |
        |  -> Tránh lỗi Cold-Start  |                     |
        +-------------+-------------+                     |
                      |                                   |
                      +-----------------+-----------------+
                                        |
                                        v
                        +---------------+---------------+
                        |   Merge & Default Fill NaNs   |
                        | (CPU=0, Conns=0, compliance=100)
                        +---------------+---------------+
                                        |
                                        v
                        +---------------+---------------+
                        |  Đặc trưng thống kê nâng cao:  |
                        |  * Robust Z-Score (Median/MAD)|
                        |  * OLS Cost Slope (14d)       |
                        |  * Weekend Ratio & Peer Ratio |
                        +---------------+---------------+
                                        |
                 +----------------------+----------------------+
                 |                                             |
                 v                                             v
  +--------------+--------------+               +--------------+--------------+
  |  RULE ENGINE (Luật cứng)    |               | ISOLATION FOREST (Học máy)  |
  |  - Rule A: Untagged Spend   |               | - Input: 10 chiều features  |
  |  - Rule B: EBS Idle/Orphan  |               | - Contamination: 1.2%       |
  |  - Rule C: RDS Idle         |               | - Output: if_anomaly        |
  |  - Rule D: Gradual Cost Drift|              |                             |
  +--------------+--------------+               +--------------+--------------+
                 |                                             |
                 +----------------------+----------------------+
                                        |
                                        v
                        +---------------+---------------+
                        | Combine Raw: Rule OR ML pred  |
                        +---------------+---------------+
                                        |
                                        v
                        +---------------+---------------+
                        |   PERSISTENCE FILTER (N>=3)   |
                        |  - Lọc nhiễu ngắn ngày        |
                        |  - Bypass nếu Z-score > 15.0  |
                        +---------------+---------------+
                                        |
                                        v
                        +---------------+---------------+
                        |  WHITELIST MUTING (Bỏ qua)    |
                        |  - flashsale / loadtest...   |
                        +---------------+---------------+
                                        |
                                        v
                        +---------------+---------------+
                        |      CẢNH BÁO CUỐI CÙNG       |
                        |   (Precision V5 = 100.0%)     |
                        +-------------------------------+
```

---

## 🔍 CHI TIẾT CÁC THÀNH PHẦN

### 1. Giai đoạn 1: Feature Engineering (Trích xuất đặc trưng)
* **Date Padding:** Tạo ra MultiIndex Cartesian product giữa `date` và `resource_id`. Việc này giúp lấp đầy các ngày có chi phí $0$ cho tài nguyên mới tạo hoặc tắt đi, ngăn chặn lỗi chia cho $0$ hoặc tính toán phương sai/MAD sai lệch (lỗi **Cold-Start**).
* **Pivot Metrics:** Telemetry từ CloudWatch ban đầu ở định dạng dọc (nhiều dòng cho mỗi metric), được xoay ngang (pivoted) thành các cột tương ứng (`CPUUtilization`, `DatabaseConnections`, `VolumeIdleTime`, `TagCompliance`, `GPUUtilization`, `AttachmentState`).
* **Robust Z-score:** 
  $$\text{Robust Z-score} = \frac{\text{Cost} - \text{Median}_{14d}}{1.4826 \times \text{MAD}_{14d}}$$
  *Sử dụng Median và MAD (Median Absolute Deviation) giúp tránh việc baseline bị méo mó bởi các đỉnh spike cũ.*
* **OLS Cost Slope (14 ngày):** Tính toán độ dốc hồi quy tuyến tính của chi phí trong 14 ngày gần nhất để phát hiện xu hướng leo thang chậm.

### 2. Giai đoạn 2: Bộ máy Hybrid Detector (Phát hiện hỗn hợp)
* **Rule Engine (Chẩn đoán nguyên nhân chéo từ Metrics):**
  * **Rule A (Untagged Spend):** `TagCompliance` < 100% và chi phí hàng ngày > 40$.
  * **Rule B (EBS Orphan/Idle):** `AttachmentState` = 0 (chưa gắn kết) hoặc `VolumeIdleTime` > 99% đối với ổ đĩa EBS có chi phí > 5$.
  * **Rule C (RDS Idle):** Cơ sở dữ liệu RDS hoạt động liên tục nhưng `DatabaseConnections` = 0 và chi phí > 5$.
  * **Rule D (Gradual Drift):** Độ dốc chi phí `slope_14d` > 2.0 (tăng liên tục), CPU utilization thấp (`CPUUtilization` < 40%) trên các tài nguyên đắt đỏ (> 40$/ngày).
* **Unsupervised Machine Learning (Isolation Forest):**
  Mô hình Isolation Forest được cấu hình với độ nhiễm bẩn (`contamination`) ổn định ở mức 1.2%, học tự động phân phối của 10 chiều đặc trưng để phát hiện các mối quan hệ bất thường phức tạp phi tuyến tính mà các luật cứng không thể bao quát.

### 3. Giai đoạn 3: Bộ lọc trễ & Whitelist
* **Persistence Filter:** Đếm số ngày liên tiếp bị đánh dấu bất thường thô (`pred_raw`).
  * Chỉ phát cảnh báo nếu tình trạng kéo dài liên tục từ **3 ngày trở lên** (để dập tắt nhiễu từ các hoạt động stress test ngắn ngày).
  * Ngoại lệ: Nếu chi phí tăng vọt đột ngột quá lớn (`Robust Z-score > 15`), cảnh báo sẽ lập tức được gửi đi trong ngày đầu tiên.
* **Whitelist Muting:** Tự động tắt cảnh báo đối với các tài nguyên có chứa các từ khóa chiến dịch như `flashsale`, `loadtest`, hoặc `migration`.

---

## 📈 KẾT QUẢ ĐÁNH GIÁ (BACKTEST)

| Tập dữ liệu | Số Anomaly phát hiện | Tỷ lệ báo giả (FPR) | Độ chính xác (Precision) | Trạng thái |
|:---|:---:|:---:|:---:|:---:|
| **Train gốc (3 tháng)** | 7 / 7 kịch bản | 0.32% | **80.86%** | **✅ ĐẠT CHUẨN** |
| **Test V4 (1 tháng)** | 3 / 3 kịch bản | 1.64% | **89.19%** | **✅ ĐẠT CHUẨN** |
| **Test V5 (1 tháng)** | 7 / 7 kịch bản | 0.00% | **100.00%** | **✅ ĐẠT CHUẨN** |
| **Test Hao (1 tháng)** | 2 / 3 kịch bản | 0.18% | **93.75%** | **✅ ĐẠT CHUẨN** |
