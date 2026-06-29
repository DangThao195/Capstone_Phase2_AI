# TF2 — FinOps Watch · Dataset cho backtest 3 tháng

> Dataset này là **input cho yêu cầu cứng của TF2**: *"Precision ≥80%, FP ≤10% trên backtest 3 tháng historical data."*
> Đây là **bộ dữ liệu lịch sử** để team train + đo detection. Phần **live demo** (synthetic anomaly inject → detect → alert → containment) thì team **tự bơm** trên môi trường của mình — không dùng file này.

---

## ⚠️ Đọc trước: data này là gì (và không là gì)

- Đây là **synthetic data fidelity cao**, KHÔNG phải bill thật của một công ty.
- Lý do (và vì sao đây là lựa chọn **đúng**, không phải đi tắt):
  1. Bill AWS thật là **dữ liệu mật** — không ai public được, và đề TF2 đã chốt *"Real AWS bill access — synthetic-only"*.
  2. Backtest đo precision/FP **bắt buộc phải có nhãn ground-truth** ("khoản nào là bất thường") — nhãn chỉ tồn tại khi anomaly được **cài có chủ đích**. Bill thật không có nhãn.
- Bù lại, data được dựng để **không phân biệt được với bill thật về cấu trúc**:
  - Đúng **schema CUR 2.0 chính thức** của AWS ([data dictionary](https://docs.aws.amazon.com/cur/latest/userguide/table-dictionary-cur2-line-item.html)).
  - **Service code / usage type / operation thật** (`AmazonEC2` / `BoxUsage:p3.2xlarge` / `RunInstances`…).
  - **Giá on-demand list us-east-1 thật** (vd p3.2xlarge = $3.06/hr, db.r5.2xlarge = $1.16/hr).
  - Pattern chi tiêu thật: multi-account, tag, weekday/weekend, bump cuối tháng, tăng trưởng hữu cơ + nhiễu.

---

## Bối cảnh (khớp brief TF2)

Công ty mid-size, AWS Organizations, **6 linked account**, ~12 squad. Baseline ~**$180k/tháng**. Finance không nhìn cost liên tục → chi tiêu bất thường lọt lưới nhiều ngày trước khi bị phát hiện. Nhiệm vụ của FinOps Watch: **phát hiện anomaly sớm, báo đúng người, đo được precision/FP.**

| Chỉ số dataset | Giá trị |
|---|---|
| Khoảng thời gian | 2026-03-01 → 2026-05-31 (92 ngày) |
| Line items (CUR) | 24,533 dòng · 276 resource |
| Linked accounts | 6 (`prod-core`, `prod-payments`, `staging`, `dev`, `data-analytics`, `ml-research`) |
| Tổng chi tiêu | ~$593,773 (Mar $187k · Apr $196k · May $211k) |
| Anomaly cài sẵn | 7 nhóm, ~$40,882 (~**6.9%** bill) |
| "Bẫy FP" (benign) | 3 sự kiện trông giống anomaly nhưng **hợp lệ** |

> Lưu ý: dataset **không** có cú spike 2.3× lồ lộ như câu chuyện brief — cố ý. Một spike khổng lồ thì detect được bằng mắt, backtest vô nghĩa. Ở đây anomaly **lẫn vào bill thật**, kích thước đa dạng, có cái dễ có cái khó — đúng tinh thần đo precision/recall.

---

## Files

| File | Mô tả | Dùng cho |
|---|---|---|
| `cur_line_items.csv` | CUR 2.0 resource-level, daily. Nguồn chi tiết nhất. | Detection resource/service level, drill-down containment |
| `cost_explorer_daily.csv` | Aggregate daily theo account × service (shape Cost Explorer API). | Detection trend/aggregate, near-real-time signal |
| `anomaly_labels_public.csv` | **2 anomaly mẫu + 1 confounder mẫu** (đã dán nhãn đầy đủ). | Hiểu format nhãn + calibrate detector |

> **Nhãn đầy đủ (đáp án) do mentor giữ** và dùng để chấm cuối kỳ. Team **chỉ có 3 nhãn mẫu** ở trên để calibrate — phần còn lại phải tự tìm. (Giống train/holdout trong ML: tránh tự ra đề tự chấm.)

---

## Schema `cur_line_items.csv`

Tên cột theo đúng CUR 2.0. Các cột quan trọng nhất:

| Cột | Ý nghĩa |
|---|---|
| `line_item_usage_start_date` | Ngày phát sinh (RFC3339 UTC, daily grain) |
| `line_item_usage_account_id` / `_name` | **Linked account** = đơn vị "tenant" trong FinOps |
| `line_item_product_code` | Service: `AmazonEC2`, `AmazonRDS`, `AmazonS3`… |
| `line_item_usage_type` | `BoxUsage:m5.2xlarge`, `TimedStorage-ByteHrs`… |
| `line_item_operation` | `RunInstances`, `CreateDBInstance`… |
| `line_item_resource_id` | Resource cụ thể (để drill-down + containment) |
| **`line_item_unblended_cost`** | **$ — đây là nguồn sự thật cho detection** |
| `line_item_usage_amount` / `pricing_unit` / `line_item_unblended_rate` | Lượng dùng / đơn vị / đơn giá |
| `product_region_code`, `product_instance_type` | Region, instance type |
| `resource_tags_user_team` / `_environment` / `_cost_center` / `_owner` | Tag — **`team` trống = untagged** (một loại anomaly) |

> Detection dựa trên **`unblended_cost`**, không phải `usage_amount`. (Daily `usage_amount` có thể dao động nhẹ quanh 24h do nhiễu — bình thường trong CUR.)

## Schema `cost_explorer_daily.csv`

`date, linked_account_id, linked_account_name, service, service_code, region, unblended_cost, is_estimated`

- `service` = **tên hiển thị Cost Explorer** (vd `Amazon Elastic Compute Cloud - Compute`) — khớp đúng output `aws ce get-cost-and-usage` thật.
- `service_code` = **product_code CUR** (vd `AmazonEC2`) — khoá để join về `cur_line_items.csv`.
- `region`: chiều enrich từ CUR (CE API gốc chỉ trả region nếu group-by REGION) — để các bạn detect theo region tiện hơn.
- `is_estimated = true` ở **2 ngày cuối** (mô phỏng CE/CUR chưa final). Engine nên xử lý estimated vs final — xem Telemetry Contract.

> **Lưu ý naming thực tế (đã đối chiếu data thật):** CUR dùng *code* (`AmazonEC2`), Cost Explorer dùng *tên hiển thị* (`Amazon Elastic Compute Cloud - Compute`) — hai nguồn **không cùng tên service**. Đây là cái bẫy join thật ngoài đời; file này cho sẵn cả hai để các bạn nối được.

---

## Đã đối chiếu format với data AWS thật

Không phải tự tin suông — format đã được kiểm chứng:

- **CUR**: tên cột khớp **CUR 2.0 data dictionary chính thức** của AWS (đã đọc trực tiếp tài liệu).
- **Cost Explorer**: đã chạy `aws ce get-cost-and-usage` trên một account thật và so sánh — tên service hiển thị, cấu trúc `TimePeriod/Groups/UnblendedCost{Amount,Unit}`, cờ `Estimated`, multi-account đều khớp (kể cả chi tiết `AmazonCloudWatch` viết liền không có dấu cách — đúng y AWS thật).
- Khác biệt duy nhất là **shape**: CE API trả JSON lồng (`ResultsByTime → Groups`); file này là **bảng phẳng đã normalize** — đúng thứ mà pipeline PULL của CDO sẽ tạo ra sau khi gọi API (xem Telemetry Contract). Đây là biểu diễn hợp lệ, không phải dump JSON thô.

---

## "Tenant" trong dataset này

FinOps không có user-tenant như SaaS. Ở đây **đơn vị phân bổ** là:
- **Linked account** (`line_item_usage_account_id`) — chiều chính.
- **Tag** (`resource_tags_user_team` / `cost_center`) — chiều phụ.

Detect "ở đâu / do squad nào" = group theo account và/hoặc tag.

---

## Các LOẠI anomaly có trong data (để biết bề mặt cần đào)

Biết trước **loại** là fair (giống biết các kiểu gian lận cần soi). **Resource/ngày cụ thể thì phải tự tìm** (mentor giữ đáp án):

| Loại (`anomaly_type`) | Mô tả | Tín hiệu gợi ý |
|---|---|---|
| `runaway_usage` | Compute bị quên, chạy 24/7 kể cả cuối tuần | Resource mới, đắt, chạy liên tục, không giảm cuối tuần |
| `idle_resource` | Provisioned nhưng ~0 sử dụng, kéo dài | Cost đều đặn, usage thấp, "luôn ở đó" |
| `untagged_spend` | Thiếu tag `team` → không phân bổ được | `resource_tags_user_team` rỗng, cost lớn |
| `sudden_spike` | Tăng vọt ngắn ngày do misconfig | Cost nhảy bậc thang rồi về |
| `gradual_drift` | Bò lên từ từ nhiều tuần (auto-scale không scale-down) | Trend tăng chậm, khó thấy nếu chỉ nhìn 1 ngày |

`anomaly_labels_public.csv` cho 2 ví dụ thật (1 `idle_resource`, 1 `sudden_spike`) + 1 benign để hiểu cả hai lớp.

## ⚠️ Bẫy False Positive (rất quan trọng cho FP ≤10%)

Trong data có **vài sự kiện trông y hệt anomaly nhưng HỢP LỆ** (đã được Finance duyệt): flash-sale theo kế hoạch, migration một lần, load test định kỳ. Đây là **bẫy FP** — engine nào chỉ "thấy cost tăng là báo" sẽ **vỡ ngưỡng FP ≤10%**. Detector tốt phải phân biệt *bất thường* với *tăng có chủ đích*.

`anomaly_labels_public.csv` cho 1 ví dụ benign (`B2`) để hiểu lớp này.

---

## Cách load nhanh

```python
import pandas as pd
cur = pd.read_csv("cur_line_items.csv", parse_dates=["line_item_usage_start_date"])

# tổng chi tiêu theo ngày × service (cho anomaly detection)
daily = (cur.groupby([cur.line_item_usage_start_date.dt.date,
                      "line_item_usage_account_name", "line_item_product_code"])
            ["line_item_unblended_cost"].sum().reset_index())

# untagged spend (một loại anomaly)
untagged = cur[cur.resource_tags_user_team.isna() | (cur.resource_tags_user_team == "")]
```

Athena / SQL cũng load thẳng `cur_line_items.csv` được (đúng schema CUR).

---

## Map vào deliverable TF2

- **Backtest report**: chạy detector trên 3 tháng này → so với nhãn (mentor chấm) → precision / recall / F1 / **confusion matrix** / **per-anomaly-type breakdown**.
- **Precision ≥80%, FP ≤10%**: đo trên chính dataset này.
- **Time frame goal (12/24/48h)** + **granularity (service vs account vs tag)**: hai trade-off phải defend bằng ADR. Cả hai đều ảnh hưởng trực tiếp FP đo trên data này — chọn nhạy quá → FP nổ ở mấy "bẫy FP" trên.

---

## Tự kiểm tra (để biết detector có ổn không)

- 3 nhãn mẫu trong `anomaly_labels_public.csv` — detector của bạn có bắt được A2, A6 và **không** báo B2 không?
- Cái training cluster GPU (loại `runaway_usage`) — ở granularity bạn chọn nó có **nổi lên** không, hay chìm trong bill $6k/ngày?
- 3 "bẫy FP" — detector có đủ tỉnh để **không** báo chúng không?
