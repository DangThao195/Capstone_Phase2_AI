# SUMMARY — FinOps Watch Anomaly Detection Pipeline
## Capstone Phase 2 · End-to-End Report

> Auto-generated từ `anomaly_detection_pipeline.ipynb` sau khi chạy xong.

---

## 1. Dữ liệu đầu vào

| Nguồn | Granularity | Kích thước | Vai trò |
|---|---|---|---|
| `cost_explorer_daily.csv` | Daily | 92 ngày × nhiều account/service | Billing signal chính — dùng làm feature |
| `cur_line_items.csv` | Per-resource | High-cardinality | Context/drill-down, không vào model |
| `metrics_data/metrics.csv` | **Hourly** | 134,688 rows × 14 cols, 61 resources | CPU/memory/network/disk — aggregated daily, joined với cost |
| `anomaly_labels_public.csv` | Event-level | 3 events (A2, A6, B2) | Ground truth để tạo `y` và đánh giá |

---

## 2. EDA — Những phát hiện quan trọng

- **Cost data**: 92 ngày liên tục, không gap, 6 accounts, 17 services. `unblended_cost` phân phối right-skewed → cần feature engineering.
- **Metrics data (hourly)**: 6 resource types. `gpu_utilization` và `database_connections` sparse by design (chỉ applicable cho GPU/DB). `cpu_percent` có daily seasonality rõ (business hours cao hơn).
- **Label imbalance**: anomaly ~6%, benign ~4%, normal ~90% → phải dùng `scale_pos_weight`.
- **A6 cold-start**: AmazonCloudWatch chỉ xuất hiện đúng 7 ngày → lag/rolling features = NaN → imputation thay vì drop.

---

## 3. Feature Engineering

Từ cost daily data, engineer **32 features** theo 3 nhóm:

### 3.1 Temporal / Rolling (trên `unblended_cost` per account-service group)

| Feature | Ý nghĩa |
|---|---|
| `lag_1`, `lag_3`, `lag_7` | Cost ngày hôm qua / 3 ngày trước / 1 tuần trước |
| `rolling_3d_mean`, `rolling_7d_mean`, `rolling_14d_mean` | Baseline chi tiêu ngắn/trung/dài hạn |
| `rolling_7d_std` | Volatility của chi tiêu trong 7 ngày |
| `ema_7`, `ewma_14` | Exponential smoothing — ít nhạy với outlier hơn rolling mean |

### 3.2 Deviation Signals (top SHAP features)

| Feature | Công thức | Ý nghĩa |
|---|---|---|
| `rate_of_change` | `(cost − lag_1) / lag_1` | Bắt sudden spike — A6 có ROC ≈ +1.9 |
| `delta` | `cost − rolling_7d_mean` | Độ lệch tuyệt đối khỏi baseline |
| `rolling7_x_dow` | `rolling_7d_mean × day_of_week` | Interaction: baseline × ngày trong tuần |

### 3.3 Cold-start Imputation

Thay vì `dropna()`, NaN trong lag/rolling được fill bằng **global per-service median**.
Kết quả: cold-start rows (như CloudWatch A6) vẫn vào model với features có nghĩa.


---

## 3b. Những gì model thực sự học — Feature Groups

Model XGBoost nhận vào **32 features** chia làm 3 nhóm, mỗi nhóm mang một loại signal khác nhau:

### Nhóm 1 — Cost Trend Features (từ `unblended_cost`)
*Câu hỏi: chi tiêu đang đi theo xu hướng nào?*

| Feature | Loại | Ý nghĩa cho model |
|---|---|---|
| `lag_1`, `lag_3`, `lag_7` | Lag | "Hôm qua/3 ngày trước/tuần trước cost bao nhiêu?" — context ngắn hạn |
| `rolling_3d_mean`, `rolling_7d_mean`, `rolling_14d_mean` | Rolling | Baseline 3/7/14 ngày — model so cost hiện tại với baseline này |
| `rolling_7d_std` | Volatility | Service này thường biến động nhiều không? High std = less surprising if spike |
| `ema_7`, `ewma_14` | Smoothed trend | Trend dài hạn ít bị nhiễu bởi 1 ngày spike |
| `rate_of_change` | Deviation | `(cost - lag_1) / lag_1` — **top SHAP feature** — bắt sudden spike |
| `delta` | Deviation | `cost - rolling_7d_mean` — lệch bao nhiêu so với baseline |
| `rolling7_x_dow` | Interaction | Baseline × ngày trong tuần — weekend thường chi tiêu thấp hơn |

### Nhóm 2 — Operational Metric Features (từ `metrics_data/metrics.csv`)
*Câu hỏi: resource đang hoạt động như thế nào khi cost tăng?*

| Feature | Nguồn gốc | Ý nghĩa cho model |
|---|---|---|
| `met_cpu_mean`, `met_cpu_max` | CPU utilization | EC2/RDS đang làm việc thật hay idle? |
| `met_mem_mean`, `met_mem_max` | Memory usage | Memory leak pattern — tăng dần liên tục |
| `met_net_in_mean`, `met_net_in_max` | Network inbound | DDoS hay data ingestion? |
| `met_net_out_mean` | Network outbound | Data exfiltration hay planned export? |
| `met_disk_mean` | Disk IO | Disk bottleneck hay backup job? |
| `met_db_conn_mean`, `met_db_conn_max` | DB connections | RDS saturation hay idle? |
| `met_gpu_mean`, `met_gpu_max` | GPU utilization | Training job running hay GPU runaway? |

> Join strategy: `service_code` → `resource_type` mapping (AmazonEC2→compute, AmazonRDS→database...)
> rồi join với daily aggregated metrics trên `(date, resource_type)`.
> Coverage: ~70% rows có metric data. 30% còn lại (CloudWatch, Lambda...) được fillna = median.

### Nhóm 3 — Cost-Metric Coherence Features (derived)
*Câu hỏi: cost spike có được "giải thích" bởi metric tương ứng không?*

| Feature | Công thức | Pattern detect được |
|---|---|---|
| `coherence_cost_cpu` | `cost_change / cpu_change` | **Idle resource**: cost tăng nhưng CPU vẫn thấp → coherence >> 1 → A2 pattern |
| `coherence_cost_net` | `cost_change / net_change` | **Benign transfer**: cost và network tăng cùng tỉ lệ → coherence ≈ 1 → B2 pattern |
| `coherence_cost_db` | `cost_change / conn_change` | **Billing anomaly**: RDS cost tăng không có connection increase → suspicious |

> **Đây là formalization của intuition:** *"anomaly = cost spike mà metric không giải thích được"*
>
> - `coherence >> 1` → cost tăng nhiều hơn metric → **anomaly candidate**
> - `coherence ≈ 1`  → cost và metric tăng proportional → **likely benign**
> - `coherence < 1`  → metric tăng nhưng cost không → edge case, khác loại vấn đề

### Nhóm 4 — Categorical & Time Features
`account_encoded`, `service_encoded`, `day_of_week`, `week_of_year`, `is_weekend`

*Cho model biết context: account nào, service nào, ngày nào trong tuần.*

---

## 3c. Tại sao XGBoost phù hợp với feature set này?

- **Nonlinear interactions**: `coherence_cost_cpu` cao + `delta` cao + `service=AmazonRDS` → A2 idle pattern. XGBoost tự học được 3-way interaction này, linear model không thể.
- **Handles mixed scale**: lag_1 ($0–$500), met_net_in_bytes (billions), coherence (0–50) — XGBoost dùng tree splits, không cần normalize.
- **Handles NaN via fillna(0)**: cold-start rows có lag=NaN được impute và vào model bình thường.
- **scale_pos_weight**: tự động upweight anomaly samples (6% của dataset) để model không bias về "predict normal cho mọi thứ".

---
---

## 4. Train/Test Split — Walk-Forward Validation

> **Không dùng random split** vì time-series có temporal dependency.
> Random split sẽ cho model "nhìn vào tương lai" → inflated metrics.

```
TRAIN_WINDOW = 60 days
VAL_WINDOW   = 7  days  
STEP_SIZE    = 7  days

Timeline:
[====Train 60d====][=Val 7d=]
        [====Train 60d====][=Val 7d=]
                [====Train 60d====][=Val 7d=]
                        ...
```

- Data **không bao giờ bị shuffle**
- Val window luôn nằm **sau** train window (no look-ahead bias)
- Optuna optimize **mean F1 across all splits** với **recall floor ≥ 50%**
- Final model: train trên **toàn bộ fe_df** với best Optuna params

---

## 5. Phương pháp phát hiện anomaly (Hybrid)

```
Incoming daily cost record
         │
         ├─ Có lịch sử ≥ 7 ngày? ──YES──► XGBoost (supervised)
         │                                  ├─ 32 features
         │                                  ├─ Optuna 30 trials (TPE sampler)
         │                                  ├─ Threshold = argmax F1 trên val split
         │                                  └─ SHAP waterfall cho explainability
         │
         └─ NO (cold-start) ─────────────► Rule: cost > 3× global service median
                                            └─ Flag as spike candidate
         │
         └─ Isolation Forest (parallel, unsupervised)
              ├─ Không cần label
              ├─ contamination = 3–5%
              └─ Safety net cho account/service mới
```

### Vấn đề quan trọng: Rule-based không phân biệt được anomaly spike vs benign spike

**Rule `cost > 3× median` chỉ nhìn vào magnitude — không biết spike có được lên kế hoạch không.**

Ví dụ cụ thể từ dataset:
- **A6** (anomaly): CloudWatch $263/ngày, median ~$90/ngày → 2.9× → rule flag ✅ đúng
- **B2** (benign): DataTransfer $650/ngày, median thấp hơn nhiều → rule flag ⚠️ **False Positive**

B2 là một lần di chuyển data lake có kế hoạch, có ticket — nhưng rule không biết điều đó.

**Đây là fundamental limitation của pure magnitude-based rule.**

Trong production, có 3 cách giải quyết:

| Cách | Mô tả | Phù hợp khi nào |
|---|---|---|
| **Ticket integration** | Nếu có change ticket / maintenance window khớp → suppress alert | Production với ITSM system (ServiceNow, Jira) |
| **Pattern-based suppression** | Benign spike thường đều đặn (scheduled_backup 2AM mỗi ngày). Rule thêm: "recurring at same time → benign". Nhưng one-time event như B2 thì không áp dụng được | Recurring benign events |
| **Human-in-the-loop** | Flag cả hai (anomaly + benign), human review confirm. Nếu confirm benign → add vào suppression list. **Đây là cách TF2 chọn** | Mọi trường hợp — safety first |

**Bottom line**: Rule-based layer trong notebook này có thể FP trên B2. Đây không phải bug — đây là known limitation cần document rõ. Threshold `SPIKE_K = 3.0` là tunable — tăng lên sẽ giảm FP nhưng tăng FN. TF2 chọn human-in-the-loop như safety net cuối cùng.

---

## 6. Kết quả cuối cùng

### XGBoost (Optuna-tuned + optimal threshold)

| Metric | Giá trị | TF2 Gate | Đánh giá |
|---|---|---|---|
| Precision | **0.827** | ✅ ≥ 80% | Đạt yêu cầu |
| Recall | **1.000** | — | Rất tốt (> 90%) |
| F1-score | **0.905** | — | Rất tốt |
| FPR | **0.027** | ✅ ≤ 10% | Rất thấp |
| ROC-AUC | **1.000** | — | Excellent |

**TF2 Gate: ✅ PASS**

### Isolation Forest (unsupervised baseline)

| Metric | Giá trị |
|---|---|
| Precision | 0.014 |
| Recall | 0.013 |
| F1-score | 0.013 |
| FPR | 0.111 |
| ROC-AUC | 0.278 |

**TF2 Gate: ❌ FAIL**

### Detection Coverage per event

| Event | Type | XGBoost | Isolation Forest | Ghi chú |
|---|---|---|---|---|
| **A2** | `idle_resource` | ✅ Fully detected (292/292) | ⚠️ Weakly detected (4/292 records) | 292 records (73 ngày × 4 services acct dev) |
| **A6** | `sudden_spike` | ✅ Fully detected (28/28) | ❌ Missed (0/28 records) | Cold-start → rule-based catches 7/7 days |
| **B2** | `benign_event` | ✅ TN — correctly suppressed (0/18 flagged) | ⚠️ FP — 4/18 records falsely flagged | Rule-based có thể FP → cần human review |

---

## 7. Tóm tắt quy trình end-to-end

```
Raw Data (cost + metrics + labels)
    │
    ▼
[EDA] Kiểm tra chất lượng, missing values,
      label distribution, cold-start cases
    │
    ▼
[SHAP Feature Recommendation] Train initial XGBoost,
      rank features, quyết định giữ/loại/tạo mới
    │
    ▼
[Feature Engineering] 17+ lag/rolling/deviation features,
      impute NaN thay vì drop (cold-start fix)
    │
    ▼
[Walk-Forward Validation] 60d train / 7d val / 7d step,
      no shuffle, no look-ahead
    │
    ▼
[Optuna Tuning] 30 trials, TPE sampler, recall floor ≥ 50%,
      optimize mean F1 across all splits
    │
    ▼
[Final XGBoost] Train trên full data, threshold = argmax F1,
      SHAP waterfall cho top anomalies
    │
    ▼
[Cold-Start Rule] Parallel: cost > 3× service median,
      flags new services with no history
    │
    ▼
[Isolation Forest] Parallel unsupervised baseline,
      contamination 3–5%, no labels needed
    │
    ▼
[Model Comparison] Precision/Recall/F1/FPR/ROC-AUC,
      TF2 Gate check, detection coverage per event
    │
    ▼
[Output] Anomaly scores + SHAP explanations → REST API
         insight.md + SUMMARY.md auto-generated
```

---

## 8. Giải thích thuật ngữ

| Thuật ngữ | Giải thích |
|---|---|
| **scale_pos_weight** | Tham số XGBoost bù đắp class imbalance. Công thức: `(số rows normal) / (số rows anomaly)`. Với dataset này ~90/6 ≈ 15 → mỗi anomaly sample được tính "nặng" gấp 15 lần normal. Không có tham số này, model sẽ bias về predict "normal" vì đó là class đa số. |
| **Walk-Forward Validation** | Cách chia train/test cho time-series. Khác với K-fold: không shuffle, val window luôn nằm sau train window. Đảm bảo model không "nhìn vào tương lai" khi đánh giá. |
| **Optuna / TPE Sampler** | Framework tự động tìm hyperparameters tốt nhất. TPE (Tree-structured Parzen Estimator) là thuật toán Bayesian optimization — nó học từ các trial trước để đề xuất params tốt hơn, thay vì random search. |
| **SHAP (SHapley Additive exPlanations)** | Phương pháp giải thích tại sao model đưa ra dự đoán. Mỗi feature được gán một giá trị đóng góp (positive = đẩy về anomaly, negative = đẩy về normal). Dựa trên lý thuyết game theory (Shapley values). |
| **Precision** | Trong số tất cả records model báo là anomaly, bao nhiêu % thực sự là anomaly. Cao = ít báo nhầm. `TP / (TP + FP)` |
| **Recall** | Trong số tất cả anomaly thật, model bắt được bao nhiêu %. Cao = ít bỏ sót. `TP / (TP + FN)` |
| **F1-score** | Harmonic mean của Precision và Recall. Khi dataset imbalanced, F1 phản ánh model performance tốt hơn Accuracy. `2 × P × R / (P + R)` |
| **FPR (False Positive Rate)** | Trong số records normal thật, bao nhiêu % bị model báo nhầm là anomaly. Thấp = ít false alarm. `FP / (FP + TN)` |
| **ROC-AUC** | Area Under the ROC Curve. Đo khả năng phân biệt anomaly vs normal ở mọi threshold. 1.0 = perfect, 0.5 = random. Không bị ảnh hưởng bởi threshold chọn. |
| **Isolation Forest** | Unsupervised anomaly detection. Ý tưởng: anomaly dễ "isolate" hơn normal points — cần ít bước split hơn trong random tree. Không cần label. `contamination` = tỉ lệ anomaly ước tính trong data. |
| **Cold-start problem** | Khi model gặp một (account, service) pair chưa từng xuất hiện trong training data → không có lag/rolling features → không thể dự đoán. Giải pháp: imputation hoặc rule-based fallback. |
| **EMA / EWMA** | Exponential (Weighted) Moving Average. Khác rolling mean ở chỗ: data gần đây được weight cao hơn data cũ. `span=7` nghĩa là data 7 ngày trước được weight ≈ 13.5% của data hôm nay. |
| **Ornstein-Uhlenbeck process** | Stochastic process dùng để generate metrics (CPU, memory). Đặc điểm: mean-reverting — giá trị có xu hướng quay về mean sau khi bị kéo ra xa. Phù hợp với CPU utilization có baseline ổn định. |
| **Geometric Brownian Motion** | Stochastic process dùng cho network/disk metrics. Đặc điểm: multiplicative growth — changes proportional to current value. Phù hợp với network traffic có thể tăng đột biến nhiều lần. |
| **Contamination (Isolation Forest)** | Hyperparameter ước tính tỉ lệ anomaly trong dataset. Nếu set quá thấp → bỏ sót nhiều anomaly. Quá cao → nhiều false positive. Với dataset này 3–5% khớp với label density thực tế. |
| **Threshold (XGBoost)** | Ngưỡng quyết định: predict_proba ≥ threshold → flag anomaly. Thay vì hardcode 95th percentile, chúng ta tìm threshold = argmax F1 trên val split cuối → cân bằng precision và recall tốt hơn. |
| **Human-in-the-loop** | Pattern trong production AI: model đưa ra suggestion, human review và confirm trước khi action được thực hiện. TF2 yêu cầu điều này cho containment actions (không auto-terminate, không auto-delete). |
| **TF2 Gate** | Hard requirement của client: Precision ≥ 80% VÀ FPR ≤ 10% trên backtest 3 tháng. Nếu không đạt → model không được deploy. |
| **Hybrid detection** | Kết hợp supervised (XGBoost, cần label) + rule-based (cold-start) + unsupervised (Isolation Forest). Mỗi tầng bổ sung cho tầng kia — không có single model nào cover được tất cả cases. |

---

*Auto-generated by `anomaly_detection_pipeline.ipynb` — Capstone Phase 2, FinOps Watch AI Engine.*
*Run notebook from top to bottom to refresh all metrics.*
