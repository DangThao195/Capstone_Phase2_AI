# Prompt: FinOps Watch — Unsupervised Hybrid Anomaly Detection Pipeline

> **Target model:** Claude Sonnet 4.5 / 4+  
> **Mục đích:** Gen code Python pipeline hoàn chỉnh, production-grade, không overfit.  
> **Sử dụng prompt này bằng cách paste toàn bộ nội dung vào một chat session mới.**

---

## SYSTEM CONTEXT (paste vào đầu chat)

```
Bạn là một Senior ML Engineer chuyên về AIOps và FinOps, có kinh nghiệm triển khai
anomaly detection pipeline cho cloud billing trên môi trường production. Bạn viết
code Python sạch, có docstring, có validation check, và luôn giải thích trade-off
của từng thiết kế. Bạn ưu tiên generalizability hơn là overfit vào bất kỳ dataset
cụ thể nào.
```

---

## USER PROMPT (paste tiếp sau System Context)

### 1. BỐI CẢNH VÀ MỤC TIÊU

Tôi đang xây dựng **FinOps Watch** — hệ thống tự động phát hiện cost anomaly trên
AWS multi-account. Nhiệm vụ của bạn là viết một **Jupyter Notebook hoàn chỉnh**
(có thể chạy từ đầu đến cuối không lỗi) thực hiện pipeline unsupervised + hybrid
detector để detect tất cả các loại anomaly trong dataset sau.

**Hard requirements (không thương lượng):**
- Precision ≥ 80%, False Positive Rate ≤ 10% trên backtest 3 tháng
- Pipeline KHÔNG được overfit vào identity của dataset này (không dùng resource_id,
  account_id, service_name làm feature trực tiếp)
- Phải generalize được sang cùng scenario nhưng khác service/account/region
- Phải phân biệt được anomaly thật vs benign event (xem mục 4)


---

### 2. DATA SOURCES

**File paths (đặt cùng folder với notebook hoặc chỉnh `DATA_DIR`):**

```
data/
  metrics.csv              ← operational metrics (CPU, memory, DB connections, GPU…)
  cost_explorer_daily.csv  ← daily cost aggregate: date × account × service
  cur_line_items.csv       ← CUR 2.0 resource-level daily detail
  anomaly_labels_full.csv  ← ground truth labels (dùng CHỈ để evaluate, KHÔNG train)
```

**Schema `cost_explorer_daily.csv`:**
```
date, linked_account_id, linked_account_name, service, service_code,
region, unblended_cost, is_estimated
```

**Schema `cur_line_items.csv` (CUR 2.0 — các cột quan trọng):**
```
line_item_usage_start_date, line_item_usage_account_id, line_item_usage_account_name,
line_item_product_code, line_item_usage_type, line_item_operation,
line_item_resource_id, line_item_unblended_cost, line_item_usage_amount,
line_item_unblended_rate, pricing_unit, product_region_code,
product_instance_type, resource_tags_user_team, resource_tags_user_environment,
resource_tags_user_cost_center, resource_tags_user_owner
```

**Schema `metrics.csv`:**
```
timestamp, resource_id, service, account_id, metric_name, metric_value, unit,
is_anomaly, anomaly_type
```
Các metric_name quan trọng: `CPUUtilization`, `MemoryUtilization`, `NetworkIn/Out`,
`DatabaseConnections`, `VolumeIdleTime`, `AttachmentState`, `ConsumedWriteCapacityUnits`,
`ProvisionedWriteCapacityUnits`, `IncomingLogEvents`, `Invocations`, `Errors`,
`TagCompliance`, `BytesTransferred`.


---

### 3. CÁC LOẠI ANOMALY CẦN DETECT

Dataset có **5 anomaly types** + **3 benign events** phải suppress:

| ID | Label | Type | Account | Service | Window | ~Cost |
|----|-------|------|---------|---------|--------|-------|
| A1 | anomaly | `runaway_usage` | ml-research | AmazonEC2 | Apr 08–25 | $6,625 (5× GPU p3.2xlarge) |
| A2 | anomaly | `idle_resource` | staging | AmazonRDS | Mar 20–May 31 | $2,040 (db.r5.2xlarge ~0 conn) |
| A3 | anomaly | `idle_resource` | dev | AmazonEC2 | Mar 01–May 31 | $829 (unattached EBS volumes) |
| A4 | anomaly | `untagged_spend` | prod-payments | AmazonEC2 | Mar 01–May 31 | $13,606 (no team tag) |
| A5 | anomaly | `sudden_spike` | prod-core | AWSDataTransfer | May 12–16 | $2,636 (NAT misconfig) |
| A6 | anomaly | `sudden_spike` | dev | AmazonCloudWatch | Apr 28–May 04 | $1,839 (DEBUG logging) |
| A7 | anomaly | `gradual_drift` | data-analytics | AmazonDynamoDB | Apr 01–May 31 | $13,307 ($60→$320/day drift) |
| B1 | benign | `benign_event` | prod-payments | AmazonEC2 | May 23–26 | $3,655 (planned flash-sale) |
| B2 | benign | `benign_event` | data-analytics | AWSDataTransfer | Mar 28–30 | $1,961 (one-time migration) |
| B3 | benign | `benign_event` | staging | AmazonEC2 | May 06–07 | $973 (load test 2 ngày) |

**Detection signal per type:**
- `runaway_usage`: resource mới + cost cao + `weekend_ratio ≈ 1.0` (không giảm cuối tuần)
- `idle_resource`: cost đều đặn mỗi ngày + metric utilization ≈ 0 (DB conn ≈ 0, VolumeIdleTime cao)
- `untagged_spend`: `resource_tags_user_team IS NULL` + cost ≥ $30/ngày (rule-based, không cần ML)
- `sudden_spike`: cost tăng đột ngột ≥ 3× baseline + kéo dài ≥ 5 ngày (lọc benign B1=2d, B2=3d, B3=4d)
- `gradual_drift`: `mean_7d / mean_28d > 1.15` liên tục ≥ 14 ngày (CUSUM hoặc drift_ratio)


---

### 4. BENIGN TRAPS — BẪY FALSE POSITIVE

Ba sự kiện B1, B2, B3 trông giống anomaly nhưng HỢP LỆ. Pipeline **phải suppress** chúng:

| Benign | Vì sao giống anomaly | Cách distinguish |
|--------|---------------------|-----------------|
| B1 flash-sale (May 23–26, 4 ngày) | Cost EC2 tăng $900/ngày | Chỉ 4 ngày → `duration_threshold ≥ 5` |
| B2 migration egress (Mar 28–30, 3 ngày) | DataTransfer spike $650/ngày | Chỉ 3 ngày → `duration_threshold ≥ 5` |
| B3 load test (May 06–07, 2 ngày) | EC2 staging spike $480/ngày | Chỉ 2 ngày → `duration_threshold ≥ 5` |

**Rule chốt:** `sudden_spike` chỉ alert nếu cost tăng bất thường **kéo dài ≥ 5 ngày liên tục**.
Đây là threshold **có logic kinh doanh** (planned event thường ≤ 3–4 ngày, sự cố kỹ thuật kéo dài hơn).


---

### 5. KIẾN TRÚC PIPELINE YÊU CẦU

Pipeline phải có **8 layer** sau, implement theo thứ tự:

#### LAYER 0 — Data Ingestion & Validation
```python
# Load 3 files CSV, parse dates, check schema
# Flag is_estimated rows (đừng train trên estimated data)
# Merge CE + CUR + metrics theo (account, service/product_code, date)
# Validate: không thiếu ngày, không duplicate
```

#### LAYER 1 — Feature Engineering (KHÔNG dùng identity features)
**Yêu cầu bắt buộc:**
- KHÔNG đưa `account_id`, `resource_id`, `service_name` vào feature vector của ML model
- Được phép dùng `account_freq` (frequency encoding), `service_freq` (tần suất xuất hiện) — đây là continuous prior, không phải identity
- Tất cả features phải là **dynamics/behavioral**, có thể compute trên account/service mới

**Features cần implement (nhóm theo mục đích):**

*Temporal baseline:*
```
rolling_mean_7d, rolling_std_7d   — 7-day baseline
rolling_mean_28d, rolling_median_28d  — 28-day robust baseline  
lag_1, lag_7                      — temporal shift
ewma_7 (span=7)                   — exponential smoothing
```

*Spike / deviation detection:*
```
robust_z_score = (cost - rolling_median_28d) / rolling_std_7d.clip(0.5)
                  — MAD-based, không bị kéo bởi spike chính nó
rate_of_change = pct_change(1)    — day-over-day
delta = cost - rolling_mean_7d    — absolute deviation
stl_residual (STL period=7)       — weekly seasonality removed
stl_trend                         — long-term direction
```

*Sustained drift detection:*
```
cusum_pos = cumulative sum of positive deviations, reset on negative
            — detect gradual drift A7 (DynamoDB $60→$320/day)
drift_ratio = rolling_mean_7d / rolling_mean_28d.clip(1.0)
drift_sustained_days = consecutive days drift_ratio > 1.15
change_point_flag = 1 if cusum_pos > 5 × rolling_std_7d
```

*Idle resource detection (từ metrics.csv):*
```
met_db_conn_mean = mean(DatabaseConnections per resource per day)
met_cpu_mean = mean(CPUUtilization per resource per day)
met_volume_idle = mean(VolumeIdleTime per resource per day)
idle_signal = (met_cpu_mean < 5%) AND (cost > $5/day)
              — zombie resource: costs money but does nothing
```

*Runaway detection:*
```
weekend_ratio_14d = rolling_14d_mean_weekend / rolling_14d_mean_weekday
                    — healthy resource: ratio ≈ 0.7–0.85
                    — runaway GPU: ratio ≈ 1.0 (chạy cả cuối tuần)
resource_age_days = days since first_seen_date
is_new_resource = resource_age_days <= 3 AND first_seen after day 7 of dataset
```

*Cost concentration / untagged:*
```
resource_cost_share = resource_cost / service_total_cost_day
untagged_flag = (resource_tags_user_team IS NULL) AND (cost >= $30/day)
                — pure rule, không cần ML
cost_share_of_account = service_cost / account_total_cost_day
```

*Ranking features (position in distribution):*
```
pct_rank_7d  = percentile rank of today's cost vs last 7 days
pct_rank_30d = percentile rank of today's cost vs last 30 days
               — causal: chỉ dùng past data, không look-ahead
```


#### LAYER 2 — Rule Engine (deterministic, zero ML)
```python
# Rule 1: UNTAGGED SPEND
untagged_alert = (resource_tags_user_team IS NULL) AND (cost >= 30) AND (resource_age_days >= 3)

# Rule 2: SUDDEN SPIKE — với benign suppression
spike_candidate = (robust_z_score > 3.0) OR (rate_of_change > 2.0)
sustained_days  = consecutive days spike_candidate is True per (account, service)
sudden_spike_alert = spike_candidate AND (sustained_days >= 5)
# Lý do: B1=4d, B2=3d, B3=2d đều < 5 → bị lọc

# Rule 3: IDLE RESOURCE  
idle_alert = (idle_signal == True) AND (sustained_stable_days >= 14)
# sustained_stable_days = consecutive days cost > $5 AND |z_score| < 1.5

# Rule 4: RUNAWAY USAGE
runaway_alert = (is_new_resource == True) AND (weekend_ratio_14d > 0.95) AND (cost > 50_per_day)

# Rule 5: GRADUAL DRIFT
drift_alert = (drift_sustained_days >= 14) AND (drift_ratio > 1.15)
```

#### LAYER 3 — Statistical Detectors (Z-score / IQR)
```python
# IQR per (account, service) trên rolling 30-day window
# Flag nếu cost nằm ngoài [Q1 - 1.5×IQR, Q3 + 1.5×IQR]
# Dùng rolling window, không dùng toàn bộ dataset (tránh look-ahead)

# Robust Z-score: dùng median + MAD thay vì mean + std
# mad = median(|x - median(x)|)
# robust_z = (x - median) / (1.4826 × mad)
# Flag nếu robust_z > 3.5
```

#### LAYER 4 — STL Trend Detection
```python
# Áp dụng STL decomposition (statsmodels.tsa.seasonal.STL)
# period=7 (weekly seasonality)
# Compute per (account, service_code) time series
# Outputs: stl_trend, stl_seasonal, stl_residual
# Flag trend anomaly nếu trend tăng ≥ 50% so với baseline 30 ngày đầu
# Flag residual anomaly nếu |stl_residual| > 3 × std(stl_residual[warmup period])
```

#### LAYER 5 — Isolation Forest (per-type, không global)
```python
# Train RIÊNG BIỆT cho từng anomaly type (không mix tất cả features vào 1 IF)

# IF_spike: features = [robust_z_score, rate_of_change, pct_rank_30d, stl_residual]
#           contamination = 0.02 (2%)

# IF_drift:  features = [drift_ratio, drift_sustained_days, cusum_pos, stl_trend]  
#            contamination = 0.03

# IF_idle:   features = [sustained_stable_days, met_cpu_mean, met_db_conn_mean,
#                         met_volume_idle, cost_share_of_account]
#            contamination = 0.02

# IF_runaway: features = [weekend_ratio_14d, resource_age_days, robust_z_score]
#             contamination = 0.02

# KHÔNG train IF trên `untagged_spend` → đã có rule engine đủ mạnh
# KHÔNG đưa account_id / service_name vào IF features
```


#### LAYER 6 — Hybrid Score Fusion & Decision
```python
# Mỗi detector tạo ra một score [0, 1] hoặc binary flag.
# Fusion theo anomaly_type riêng biệt (không blend tất cả thành 1 score):

def fuse_spike(rule_flag, if_score, z_flag):
    # Nếu rule_flag=1: chắc chắn là anomaly (rule đã lọc benign)
    # Nếu if_score > 0.7 AND z_flag=1: high confidence
    # Output: {score, confidence_tier: HIGH/MEDIUM/LOW, predicted_type}

def fuse_drift(rule_flag, if_score, stl_trend_flag):
    # drift_alert = 1 → spike_sustained nên đây là gradual drift
    # Cần cả 2: rule + IF đồng ý → HIGH confidence

def fuse_idle(rule_flag, if_score, metric_flag):
    # idle_alert + metric signal → strong idle signal
    # Chỉ idle_alert (không có metric) → MEDIUM (metrics có thể bị thiếu)

# BENIGN SUPPRESSION LAYER (chạy sau khi fusion):
# Nếu duration < 5 days: hạ tier xuống LOW hoặc suppress hoàn toàn
# Lý do: phân biệt planned event (B1/B2/B3) vs real anomaly

# FINAL DECISION:
# HIGH confidence → alert
# MEDIUM confidence → soft-alert (vào queue review)
# LOW confidence → suppress
```

#### LAYER 7 — Persistence Filter (production simulation)
```python
# Simulate daily cadence: mỗi ngày d, chỉ dùng data ≤ d
# Không look-ahead vào tương lai
# Persistence rule: score[d] >= threshold AND score[d-1] >= threshold → alert fires
# N=2: ngày thứ 2 liên tiếp mới fire → loại bỏ single-day noise
# Walk-forward evaluation, không leak test data vào feature computation
```

#### LAYER 8 — Evaluation & Backtest Report
```python
# Load anomaly_labels_full.csv CHỈ ở bước này
# Per-event evaluation (không phải per-row):
#   detected = any alert fires trong window [start_date, end_date + 2 days grace]
#   detection_delay = first_alert_date - start_date (days)

# Metrics:
#   Precision = TP / (TP + FP)  — trong đó TP/FP là per-event, không per-row
#   Recall    = TP / (TP + FN)
#   FPR       = FP / (FP + TN benign)  — phải ≤ 10%
#   F1        = harmonic mean

# Per-anomaly-type breakdown:
#   Báo cáo riêng: runaway / idle / untagged / sudden_spike / gradual_drift
#   Báo cáo benign: B1/B2/B3 có bị false-positive không?

# Output đẹp:
#   - Confusion matrix (5×5 per type)
#   - Detection timeline chart (matplotlib)
#   - Score distribution per event
#   - "TF2 Gate: PASS / FAIL" summary table
```


---

### 6. ANTI-OVERFITTING REQUIREMENTS

Đây là yêu cầu **bắt buộc** để pipeline generalize sang môi trường mới:

**❌ NGHIÊM CẤM trong feature engineering:**
```python
# KHÔNG làm:
features["account_encoded"] = LabelEncoder().fit_transform(df["account_name"])
features["service_encoded"] = LabelEncoder().fit_transform(df["service_code"])
# Lý do: model học "account X = anomaly" thay vì học pattern cost dynamics

# KHÔNG làm:
df["is_ml_research"] = (df["account_name"] == "ml-research").astype(int)
# Lý do: hardcode tên account cụ thể → fail hoàn toàn trên account khác
```

**✅ ĐƯỢC PHÉP (frequency encoding — continuous prior):**
```python
# OK: tần suất xuất hiện là tín hiệu hành vi, không phải identity
account_freq = df.groupby("account_id")["account_id"].transform("count") / len(df)
service_freq = df.groupby("service_code")["service_code"].transform("count") / len(df)
# Với account mới (unseen): account_freq = 0 → feature hợp lệ, không gây error
```

**✅ ĐƯỢC PHÉP (rule-based dùng threshold, không dùng tên cụ thể):**
```python
# OK: threshold dựa trên cost behavior, không phải account name
untagged_flag = df["resource_tags_user_team"].isna() & (df["cost"] >= 30)
# Pipeline này sẽ hoạt động với bất kỳ account/service nào có untagged resource
```

**Warm-up period handling:**
```python
# Với service/resource mới (cold-start):
# - lag_1 = 0, là_first_seen = 1 (flag binary)
# - rolling features = impute bằng global median của service type đó (không per-account)
# - Không drop row, không raise error
# Lý do: trong production, account mới luôn xuất hiện
```

**Walk-forward validation (bắt buộc, không dùng random split):**
```python
# Train: tháng 1–2 (Mar–Apr)
# Validate: tháng 3 (May) — 1 month hold-out, không overlap
# Evaluate: chạy evaluate_event_detection() trên toàn bộ 3 tháng
# Không shuffle, không K-fold (time series data)
```


---

### 7. LESSONS LEARNED TỪ CÁC PIPELINE TRƯỚC (PHẢI TRÁNH)

> Đây là những lỗi đã xảy ra trong các iteration trước. Pipeline mới phải không mắc lại.

| Lỗi đã xảy ra | Triệu chứng | Fix cần implement |
|---------------|-------------|-------------------|
| Identity overfitting | Train PASS, test FAIL hoàn toàn | Loại `account_encoded`, `service_encoded` khỏi feature vector |
| Account-level labeling | A2 gán y=1 cho toàn bộ account (EC2+S3+RDS) thay vì chỉ RDS | Evaluate per-event ở service level: `mask = account==A AND service==B` |
| Cold-start drop | A6 (CloudWatch mới, 7 ngày) bị drop vì lag=NaN → bị miss hoàn toàn | Impute lag=0 + is_first_seen flag, không drop |
| Global IF contamination | IF với contamination 3–5% global → quá nhiều FP | Train IF riêng per anomaly type với contamination 1–3% |
| Không có benign suppression | B1/B2/B3 bị báo → FPR vượt 10% | Duration filter: spike alert chỉ fire nếu sustained_days ≥ 5 |
| Threshold tìm trên train quá thấp (0.0087) | Quá nhiều FP trên test | Dùng B2 (known benign) làm calibration anchor: threshold = B2_score + margin |
| Precision thấp do label noise | P = 5–6% thay vì 80% | Đánh giá ở event level, không row level |
| `gradual_drift` bị miss | STL trend nhẹ, z-score không đủ | Thêm CUSUM + drift_ratio + drift_sustained_days ≥ 14 |
| `idle_resource` bị miss | Cost ổn định trông như "bình thường" | Cần metric signal: DB conn ≈ 0, VolumeIdleTime cao, cần join metrics.csv |
| Single IF model cho tất cả | IF không phân biệt được idle vs spike | Tách IF theo anomaly type, mỗi type dùng feature subset phù hợp |


---

### 8. CẤU TRÚC NOTEBOOK YÊU CẦU

Viết notebook theo đúng thứ tự cell sau, mỗi section là một markdown header:

```
# FinOps Watch — Unsupervised Hybrid Anomaly Detection Pipeline

## 0. Setup & Constants
## 1. Data Ingestion & Validation
## 2. Feature Engineering — CE Level (account × service × day)
## 3. Feature Engineering — CUR Level (resource × day)
## 4. Metrics Integration
## 5. Rule Engine (untagged / spike / idle / runaway / drift)
## 6. Statistical Detectors (Robust Z-score, IQR)
## 7. STL Trend Detection
## 8. Isolation Forest Ensemble (per anomaly type)
## 9. Score Fusion & Hybrid Decision
## 10. Persistence Filter & Daily Simulation
## 11. Evaluation — Event-Level Backtest
## 12. TF2 Gate Check & Report
## 13. Generalization Test (thay account/service tên khác, xem pipeline có vỡ không)
```

**Section 13 (Generalization Test) là bắt buộc:**
```python
# Rename toàn bộ account_name sang tên fake ("acct-A", "acct-B"…)
# Rename toàn bộ service_code sang tên fake ("svc-1", "svc-2"…)
# Chạy lại pipeline từ Section 1
# Kỳ vọng: kết quả không thay đổi (chứng minh không có identity dependency)
# In ra: "Generalization Test: PASS / FAIL"
```


---

### 9. OUTPUT FORMAT YÊU CẦU

**Console output cuối notebook phải có:**

```
══════════════════════════════════════════════════════
FINOPS WATCH — BACKTEST REPORT (Mar–May 2026, 92 days)
══════════════════════════════════════════════════════

EVENT DETECTION SUMMARY
┌──────┬──────────────────┬──────────────┬──────────────┬────────┬──────────────┐
│ ID   │ Type             │ Detected     │ First Alert  │ Delay  │ Confidence   │
├──────┼──────────────────┼──────────────┼──────────────┼────────┼──────────────┤
│ A1   │ runaway_usage    │ ✅ YES       │ Apr 10       │ +2d    │ HIGH         │
│ A2   │ idle_resource    │ ✅ YES       │ Apr 03       │ +14d   │ HIGH         │
│ A3   │ idle_resource    │ ✅ YES       │ Mar 15       │ +14d   │ MEDIUM       │
│ A4   │ untagged_spend   │ ✅ YES       │ Mar 01       │ 0d     │ HIGH (rule)  │
│ A5   │ sudden_spike     │ ✅ YES       │ May 13       │ +1d    │ HIGH         │
│ A6   │ sudden_spike     │ ✅ YES       │ Apr 30       │ +2d    │ HIGH         │
│ A7   │ gradual_drift    │ ✅ YES       │ Apr 20       │ +19d   │ MEDIUM       │
├──────┼──────────────────┼──────────────┼──────────────┼────────┼──────────────┤
│ B1   │ benign_event     │ ✅ NOT ALERT │ —            │ —      │ SUPPRESSED   │
│ B2   │ benign_event     │ ✅ NOT ALERT │ —            │ —      │ SUPPRESSED   │
│ B3   │ benign_event     │ ✅ NOT ALERT │ —            │ —      │ SUPPRESSED   │
└──────┴──────────────────┴──────────────┴──────────────┴────────┴──────────────┘

OVERALL METRICS
  Precision  : 1.000  ✅ (≥ 0.80)
  Recall     : 1.000
  F1-score   : 1.000
  FPR        : 0.000  ✅ (≤ 0.10)
  TF2 Gate   : ✅ PASS

GENERALIZATION TEST
  Account names anonymized: PASS ✅
  Service names anonymized: PASS ✅
  Detection results unchanged: PASS ✅
══════════════════════════════════════════════════════
```

**Visualizations cần có:**
1. Cost timeline per account với anomaly window overlay (matplotlib subplots)
2. Score heatmap: 92 ngày × 7 anomaly types (seaborn heatmap)
3. Detection delay boxplot per anomaly type
4. Feature importance từ IF (mean decrease impurity hoặc SHAP nếu có)


---

### 10. TECH STACK & CONSTRAINTS

```python
# Bắt buộc:
import pandas as pd
import numpy as np
from statsmodels.tsa.seasonal import STL
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import MinMaxScaler
import matplotlib.pyplot as plt
import seaborn as sns

# Tùy chọn (nếu cần):
# import shap           # SHAP explainability
# from scipy import stats  # IQR, percentile

# KHÔNG dùng (sẽ gây overfit identity):
# from sklearn.preprocessing import LabelEncoder  # ← cấm cho account/service
# import xgboost, lightgbm  # ← tránh nếu dùng identity features

# Environment: Python 3.10+, Jupyter Notebook
# Không cần GPU, chạy được trên laptop thông thường trong < 2 phút
```

**Code quality yêu cầu:**
- Mỗi function có docstring mô tả input/output và unit
- Tất cả thresholds đặt vào `CONSTANTS` dict ở Section 0 (không hardcode giữa code)
- Có `# NOTE:` comment giải thích các threshold có business logic (vd: `# NOTE: ≥5 days filters B1/B2/B3`)
- Reproducibility: `random_state=42` cho tất cả stochastic operations

---

### 11. THỨ TỰ IMPLEMENT (KHUYẾN NGHỊ)

Nếu bạn không biết bắt đầu từ đâu, follow thứ tự này:

1. **Section 0–1**: Load data, validate schema, merge CE + CUR + metrics
2. **Section 5 (Rule Engine trước)**: Untagged + Spike + Idle rules — dễ nhất, kết quả ngay lập tức
3. **Section 2–4 (Features)**: Tính rolling stats, CUSUM, STL, metrics features
4. **Section 6–7 (Statistical)**: Robust Z-score, IQR, STL residual flags
5. **Section 8 (IF)**: Train 4 IF models per anomaly type
6. **Section 9–10 (Fusion)**: Blend scores, persistence filter
7. **Section 11–12 (Eval)**: Load labels, compute metrics, TF2 Gate check
8. **Section 13 (Generalization)**: Rename + rerun, confirm PASS

---

### 12. SELF-CHECK TRƯỚC KHI SUBMIT CODE

Trước khi trả lời, tự kiểm tra:

- [ ] `account_encoded` và `service_encoded` không có trong feature list của IF hoặc bất kỳ ML model nào
- [ ] B1 (4 ngày), B2 (3 ngày), B3 (2 ngày) đều bị suppress bởi `duration_threshold ≥ 5`
- [ ] A4 (untagged) được detect bởi Rule Engine, không phải IF (rule đủ mạnh, IF không cần)
- [ ] A7 (gradual drift) được detect bởi CUSUM/drift_ratio, không phải Z-score spike (vì drift chậm)
- [ ] A2/A3 (idle) được detect có hỗ trợ từ metric signal (CPU/DB conn), không chỉ cost flat
- [ ] Cold-start rows (lag=NaN) không bị drop, được impute hợp lý
- [ ] Walk-forward: không có look-ahead trong feature computation
- [ ] Evaluation là event-level, không row-level
- [ ] Section 13 Generalization Test tồn tại và có thể chạy


---

### 13. FOLLOW-UP QUESTIONS ĐỂ TINH CHỈNH

Sau khi Claude gen code xong, dùng các câu hỏi follow-up này nếu cần:

**Nếu FPR > 10%:**
> "B1/B2/B3 vẫn bị alert. Hãy debug: in ra `sustained_days` của từng benign event và giải thích tại sao threshold=5 không filter được. Sau đó tăng threshold hoặc thêm điều kiện."

**Nếu A7 (gradual drift) bị miss:**
> "A7 DynamoDB cost tăng từ $60 đến $320/ngày trong 8 tuần. In ra `drift_ratio` và `drift_sustained_days` theo từng ngày cho account `data-analytics` / service `AmazonDynamoDB`. Giải thích tại sao CUSUM không bắt được và fix."

**Nếu A2/A3 (idle) bị miss:**
> "A2 là RDS instance với 0 connections, A3 là unattached EBS volume. In ra `met_db_conn_mean`, `met_volume_idle`, `sustained_stable_days` cho các resource này. Nếu metrics.csv không có data cho resource đó, describe làm thế nào để fallback chỉ dùng cost signal."

**Nếu muốn production code (không phải notebook):**
> "Convert pipeline này thành Python package với structure:
>   `finops_watch/ingestion.py`, `feature_engineering.py`, `detectors/rule_engine.py`,
>   `detectors/statistical.py`, `detectors/isolation_forest.py`, `fusion.py`, `evaluator.py`
>   Thêm `__init__.py` và `main.py` để chạy: `python main.py --data-dir ./data`"

**Nếu muốn REST API wrapper:**
> "Wrap pipeline thành FastAPI endpoint: `POST /detect` nhận JSON array của daily cost records,
>   trả về list anomaly alerts với `{event_id, type, confidence, start_date, resource_id, score}`.
>   Bao gồm input validation với Pydantic và error handling."

---

## CÁCH SỬ DỤNG PROMPT NÀY

1. Mở chat Claude Sonnet 4.5+ mới (context sạch, không có lịch sử trước)
2. Paste **System Context** (mục đầu) vào ô system prompt nếu có, hoặc paste vào đầu message
3. Paste toàn bộ **User Prompt** (từ mục 1 đến mục 12) vào message
4. Gửi và chờ — Claude sẽ gen toàn bộ notebook trong một lần trả lời
5. Save output thành `finops_watch_pipeline.ipynb` và chạy từ đầu đến cuối
6. Nếu có lỗi hoặc metric chưa đạt, dùng các follow-up questions ở mục 13

**Tips:**
- Nếu Claude trả lời bị cắt (context limit), hỏi: *"Continue from Section [X]"*
- Nếu muốn giải thích trade-off thay vì code ngay: thêm vào cuối prompt *"Trước khi code, giải thích thiết kế Layer 5 IF và tại sao tách per anomaly type"*
- Nếu cần code ngắn gọn hơn: thêm *"Combine Section 2–4 thành một function `build_features(ce, cur, metrics)`"*

---

*Prompt này được tổng hợp từ: TF2_FINOPS_LEARNER.md, data/README.md,
anomaly_labels_full.csv, hao-aiops/engine/DOCS.md, hao-v2/engine/feature_engineering.py,
hao-aiops/pipeline_v2.md — Capstone Phase 2, FinOps Watch AI Engine.*
