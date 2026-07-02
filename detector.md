# FinOps Watch Detector Design

## 1. Mục tiêu dự án

Xây dựng một pipeline phát hiện anomaly chi phí trên dữ liệu AWS/CUR lịch sử 3 tháng, với luồng:

Data → Ingest → Detect → Score + lọc FP → Route → Containment

Mục tiêu chính:
- Phát hiện các trường hợp chi phí bất thường sớm.
- Giảm false positive để giữ FP dưới ngưỡng cho phép.
- Không dùng label làm đầu vào để train model.
- Dùng các nhãn public như mẫu tham chiếu cho calibration và kiểm thử, không phải để train.

---

## 2. Nguyên tắc làm việc

### 2.1 Dữ liệu chính dùng cho detector
- File chính:
  - [data/cur_line_items.csv](data/cur_line_items.csv)
  - [data/cost_explorer_daily.csv](data/cost_explorer_daily.csv)
- File nhãn public dùng cho tham khảo:
  - [data/anomaly_labels_public.csv](data/anomaly_labels_public.csv)

### 2.2 Quy tắc về label
- Không dùng label từ file nhãn để train detector.
- Chỉ dùng label public như:
  - mẫu calibration cho ngưỡng,
  - mẫu kiểm thử logic,
  - mẫu minh họa cho báo cáo.
- Các anomaly còn lại phải được tự phát hiện từ dữ liệu.

---

## 3. Pipeline tổng thể

### Bước 1: Data Ingest
Mục đích: chuẩn hóa dữ liệu từ nhiều nguồn thành một schema thống nhất để làm đầu vào detector.

Input:
- [data/cur_line_items.csv](data/cur_line_items.csv)
- [data/cost_explorer_daily.csv](data/cost_explorer_daily.csv)

Các bước:
1. Đọc dữ liệu CSV.
2. Chuyển đổi kiểu dữ liệu:
   - ngày tháng sang datetime,
   - cost sang numeric,
   - tag/owner/account/service sang string.
3. Chuẩn hóa tên cột và đơn vị.
4. Loại bỏ dòng không hợp lệ hoặc thiếu thông tin quan trọng.

Output:
- DataFrame chuẩn hóa gồm các cột:
  - date
  - account
  - service
  - resource_id
  - cost
  - usage
  - tags
  - region
  - is_estimated

---

### Bước 2: Detect
Mục đích: phát hiện candidate anomaly bằng cách tự dò từ dữ liệu, không dùng label.

Input:
- DataFrame đã ingest.

Phương pháp đề xuất:
1. Tạo feature theo thời gian và theo dimension:
   - cost hôm nay so với baseline trước đó,
   - % tăng so với trung bình 7/14/30 ngày,
   - z-score / deviation,
   - slope / trend trong nhiều ngày,
   - ratio usage/cost,
   - tăng đột biến trên account/service/resource.
2. Dùng các rule hoặc thuật toán phát hiện bất thường như:
   - spike detection,
   - trend drift detection,
   - idle resource detection,
   - untagged spend detection.
3. Chấm điểm từng candidate bằng mức độ bất thường.

Output:
- Danh sách candidate anomalies, mỗi record có:
  - timestamp
  - account
  - service
  - resource_id
  - anomaly_type (candidate)
  - score
  - evidence

---

### Bước 3: Score + lọc FP
Mục đích: giảm false positive bằng cách chấm điểm và loại những trường hợp trông giống anomaly nhưng hợp lệ.

Input:
- Candidate anomalies từ bước Detect.

Các tín hiệu dùng để lọc FP:
- Có pattern rõ ràng và có giải thích hợp lệ không?
- Có phải là planned event không?
- Có phải là expected month-end / seasonal growth không?
- Có phải là migration / load test / flash sale không?
- Có phải là spike ngắn rồi về 0 nhưng không có dấu hiệu hệ thống bị lạm dụng không?

Score đề xuất:
- Mức tăng so với baseline
- Độ bền của anomaly (ngắn hạn hay kéo dài)
- Tính nhất quán giữa cost và usage
- Mức ảnh hưởng theo account/service/resource
- Có tag / ownership / context hợp lệ không

Output:
- Danh sách alert đã lọc tốt, gồm:
  - anomaly_id
  - detected_at
  - account
  - service
  - resource_id
  - anomaly_type
  - severity
  - score
  - fp_risk
  - reason

---

### Bước 4: Route
Mục đích: định tuyến alert tới đúng owner hoặc team phù hợp.

Input:
- Alert đã được score và lọc.

Logic định tuyến:
- Nếu anomaly liên quan tới compute / EC2 / EKS → route tới Platform/SRE.
- Nếu liên quan tới RDS / DB → route tới Data/DB team.
- Nếu liên quan tới tag / ownership / cost allocation → route tới FinOps.
- Nếu mức độ nghiêm trọng cao → tạo ticket ưu tiên cao.

Output:
- Route ticket / alert payload gồm:
  - owner_team
  - severity
  - summary
  - recommended_action

---

### Bước 5: Containment
Mục đích: đề xuất hành động giảm thiểu rủi ro sau khi phát hiện anomaly.

Input:
- Alert đã được route.

Các hành động đề xuất:
- Dừng / hủy instance không dùng.
- Resize xuống mức phù hợp.
- Tắt / hạn chế workload không cần thiết.
- Gắn tag / bổ sung ownership.
- Kiểm tra cấu hình auto-scaling / misconfiguration.
- Khoanh vùng resource có dấu hiệu bất thường để review.

Output:
- Containment action list gồm:
  - action_type
  - target_resource
  - priority
  - expected_effect
  - status

---

## 4. Output cuối cùng của dự án

### 4.1 Output chính
Một file hoặc bảng kết quả sau khi chạy pipeline:
- detected_alerts.csv

Các cột gợi ý:
- anomaly_id
- detected_at
- account
- service
- resource_id
- anomaly_type
- severity
- score
- fp_risk
- route_to
- recommended_action
- status

### 4.2 Output phụ
- summary_report.json
  - số alert phát hiện
  - số alert sau khi lọc FP
  - top affected services/accounts/resources
  - breakdown theo anomaly type
- containment_plan.md
  - danh sách hành động đề xuất
- evaluation_report.md
  - precision / recall / F1 / confusion matrix (nếu có nhãn đánh giá)

---

## 5. Cách thực hiện để hoàn thành dự án

### Phase 1: Khai phá dữ liệu
1. Load [data/cur_line_items.csv](data/cur_line_items.csv) và [data/cost_explorer_daily.csv](data/cost_explorer_daily.csv).
2. Xác định các dimension chính: account, service, resource, date.
3. Tạo view tổng hợp theo ngày và theo account/service/resource.

### Phase 2: Xây detector
1. Thực hiện feature engineering từ cost và usage.
2. Xây rule / score cho các kiểu anomaly chính:
   - spike,
   - drift,
   - idle,
   - untagged spend.
3. Chọn ngưỡng bằng cách dùng 3 nhãn mẫu public cho calibration.

### Phase 3: Lọc FP
1. Xây logic phân biệt anomaly thật và benign event.
2. Chỉnh ngưỡng để tránh báo quá nhiều FP.
3. Tạo danh sách alert sau lọc.

### Phase 4: Đánh giá và báo cáo
1. So sánh với nhãn hoặc mẫu đánh giá.
2. Tính precision / recall / F1 / FP rate.
3. Viết báo cáo và lưu output.

---

## 6. Các kịch bản scenario bổ trợ

Các scenario này không dùng để train model, mà dùng để bổ trợ hiểu biết, calibrate detector và validate logic.

### 6.1 Scenario anomaly

#### A. Sudden spike
- Mô tả: cost tăng đột ngột trong 1–3 ngày rồi về lại mức cũ.
- Ví dụ: NAT traffic spike, CloudWatch log spike.
- Dấu hiệu: cost tăng mạnh, usage tăng đột ngột, rồi quay về.
- Expected behavior: detector nên báo anomaly trong thời gian spike.

#### B. Gradual drift
- Mô tả: cost tăng đều trong nhiều ngày/tuần, không có spike rõ ràng.
- Ví dụ: DynamoDB write capacity tăng dần.
- Dấu hiệu: slope dương, trend kéo dài, average monthly cost tăng.
- Expected behavior: detector nên báo trend anomaly, không cần phải đợi spike.

#### C. Idle resource
- Mô tả: resource vẫn tồn tại nhưng usage gần 0 hoặc rất thấp, cost vẫn tiếp tục phát sinh.
- Ví dụ: RDS / EC2 idle.
- Dấu hiệu: usage thấp, cost vẫn có, duration dài.
- Expected behavior: detector nên phát hiện là idle resource anomaly.

#### D. Runaway usage
- Mô tả: workload chạy liên tục, không giảm cuối tuần, cost tăng cao.
- Ví dụ: compute cluster chạy 24/7 không cần thiết.
- Dấu hiệu: usage cao liên tục, không có pattern giảm cuối tuần, cost tăng bền vững.
- Expected behavior: detector nên báo là runaway usage.

#### E. Untagged spend
- Mô tả: cost lớn nhưng thiếu tag ownership/team.
- Dấu hiệu: team/tag trống, chi phí lớn, không rõ owner.
- Expected behavior: detector nên phát hiện là cost allocation anomaly.

### 6.2 Scenario benign / FP

#### F. Planned flash sale
- Mô tả: cost tăng tạm thời do event kinh doanh có chủ đích.
- Dấu hiệu: tăng có lịch trình rõ, thường ngắn hạn, có thể giải thích được.
- Expected behavior: detector không nên báo quá mức hoặc nên thấp severity.

#### G. Scheduled load test
- Mô tả: workload tăng cao do test định kỳ.
- Dấu hiệu: tăng có thời điểm rõ, có quy trình test, không phải lỗi.
- Expected behavior: detector nên tránh báo như anomaly nghiêm trọng.

#### H. One-off migration
- Mô tả: spike ngắn do migration hoặc rollout.
- Dấu hiệu: tăng ngắn, rõ thời gian bắt đầu/kết thúc, có lý do vận hành.
- Expected behavior: detector nên đánh giá là benign hoặc soft alert.

#### I. Expected month-end burst
- Mô tả: chi phí tăng ở cuối tháng do lịch biểu bình thường.
- Dấu hiệu: pattern lặp lại hàng tháng, có thể dự đoán.
- Expected behavior: detector không nên báo nhầm.

---

## 7. Gợi ý cách lưu scenario bổ trợ

Nên lưu dưới dạng một file riêng, ví dụ:
- scenarios.csv

Cột gợi ý:
- scenario_id
- scenario_type
- service
- resource_id
- start_date
- end_date
- usage_pattern
- cost_pattern
- expected_label
- expected_severity

Mục đích của file này:
- hỗ trợ calibration,
- validate rules,
- kiểm tra detector trước khi chạy trên dữ liệu chính.

---

## 8. Kết luận

Pipeline đề xuất là một hệ thống phát hiện anomaly theo kiểu tự dò, không phụ thuộc label đầu vào. Dữ liệu chính dùng để chạy backtest, còn các scenario bổ trợ dùng để hiểu pattern, tune ngưỡng và kiểm thử detector trước khi đưa ra alert cuối cùng.
