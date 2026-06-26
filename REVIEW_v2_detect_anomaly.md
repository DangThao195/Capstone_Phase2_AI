# REVIEW — `detect anomaly v2` (commit `e862c79`, nhánh `ngoc-thao`)

- **Người làm:** Thảo & nhóm AI-RESEARCH · **File chính:** `FEATURE.py`, `DETECT.py`, `DRIFT_PLAN.py` → v2: `FEATURE_v2.py`, `DETECT_v2.py` + `ANALYSIS.py`, `plan.md`
- **Reviewer:** mentor · **Ngày:** 2026-06-26
- **Kết quả dev báo cáo:** Precision (anomaly) 0.97 · Recall 0.99 · ROC-AUC 0.9998

## VERDICT: REQUEST_CHANGES

> Kỹ thuật pipeline **tiến bộ vượt bậc** (leak-free, walk-forward, SHAP, drift, MLflow — đều đúng chuẩn). Nhưng **kết quả đang đo trên metrics + nhãn do chính nhóm tự tạo, không khớp với 7 anomaly thật của đề.** Số 0.97/0.99 là ảo đối với KPI thật; khi chấm trên đáp án gốc, model sẽ báo nhầm hàng loạt resource production.

---

## 1. Tiến bộ thực sự (ghi nhận — bước nhảy lớn so với bản SMOTE)

- ✅ **Đã bỏ hoàn toàn SMOTE** và dữ liệu tương lai bịa → sửa đúng blocker cũ.
- ✅ **Leak-free feature engineering chuẩn mực** (`FEATURE.py:182-290`): split *trước* FE; rolling/lag dùng `.shift(1)`; fit median trên train rồi áp cho test; peer-ratio chỉ trong từng split.
- ✅ **Resource-level** + Walk-Forward CV (`TimeSeriesSplit`), threshold optimization, MLflow tracking.
- ✅ **SHAP feature selection + drift KS/PSI + retrain plan** (`plan.md` Step 8-9) — tư duy MLOps tốt, hiếm ở người mới.
- ✅ v2 dùng SHAP loại 6 feature low-signal, thêm feature có chủ đích.

Về kỹ năng engineering, nhóm tiến bộ rất nhanh, đáng khen.

---

## 2. 🔴 BLOCKING

### 2.1. Metrics (CPU/memory/network/gpu) là **synthetic do nhóm tự thêm** — không có trong dataset gốc
`ec2_metrics.csv`, `rds_metrics.csv`, `ddb_metrics.csv`, `sagemaker_metrics.csv`, `other_services_metrics.csv` lần đầu xuất hiện ở commit `e862c79` (của nhóm). Dataset gốc chỉ có `cur_line_items.csv` + `cost_explorer_daily.csv`. **AWS CUR không chứa CloudWatch metrics.**

**Bằng chứng đây là vấn đề, không phải tính năng** — SHAP cho thấy model chạy chủ yếu bằng sensor synthetic:

```
Top SHAP (anomaly): cpu_min (gấp ~2× mọi feature) > cost_per_unit_usage > cpu_std
                    > disk_io_ops > cpu_variance_24h > cpu_mean > memory_mib ...
Feature chi phí THẬT (robust_z, slope_14d, cost_ratio_to_7d_avg): nằm CUỐI bảng.
```

→ Generator gần như chắc chắn sinh CPU theo nhãn (vd idle → `cpu_min` đặc trưng), nên model học **dấu vân tay của generator**, không phải hành vi anomaly. Dấu hiệu cộng hưởng: **ROC-AUC = 0.9998** (phân tách gần như hoàn hảo) — kinh điển của leakage giữa feature-sinh-theo-nhãn và nhãn.

### 2.2. Nhãn ground-truth **không khớp 7 anomaly thật** — 23 resource bình thường bị gán "anomaly"
Nhãn trong metrics: **1,823 dòng / 34 resource = anomaly; 1,839 dòng / 33 resource = benign.**
Đáp án thật (README + tái lập EDA): **7 anomaly (~$40,882) + 3 benign.**

23 resource "anomaly" thừa toàn là **production bình thường, cost cao**:
```
$8425 db-1025 | $6693 db-1026 | $5981 db-1027 | $2722 i-...3ef | ...
median cost 23 "anomaly" thừa = $2,084   vs   median toàn bộ resource = $363
```
→ Logic gán nhãn ≈ **"cost cao = anomaly"**, gán nhầm database/EC2 production khỏe mạnh. v2 còn thêm `absolute_cost_spike = cost − 3·std` (`FEATURE_v2.py:184`) — **mã hóa thẳng "cost cao = bất thường"** vào model để khớp nhãn sai này.

**Hệ quả:** mọi metric đo khả năng khôi phục **nhãn sai của chính nhóm**. Khi chấm trên answer-key thật → precision sụp vì model báo ~23 resource production.

> ⚠️ **Cần xem lại nguồn gốc metrics+nhãn.** Bằng chứng (commit của nhóm + nhãn mâu thuẫn README) nghiêng mạnh về *nhóm tự sinh*. Nếu đúng → foundation không hợp lệ; phải dựng lại nhãn ở mức resource×ngày bám đúng các anomaly **đã thực sự được inject** (3 nhãn public + tự tìm phần còn lại như đề yêu cầu), KHÔNG gán theo heuristic "cost cao".

### 2.3. Con số "đạt KPI" không vững ngay cả trên nhãn tự tạo
Chạy lại `DETECT_v2.py` (nguyên pipeline của nhóm):
```
Classification report (argmax, 3 lớp):
  anomaly: precision 0.7740  recall 0.9980   <-- argmax precision DƯỚI 0.80
Confusion matrix dùng threshold=0.910: precision ~0.97
Walk-Forward CV F1-macro: 0.877 ± 0.162   (Fold 2 sụp còn 0.554)
```
→ Precision 0.97 chỉ đạt được khi đẩy threshold lên **0.91** (rất cao); với argmax thì **0.774 < KPI 0.80**. Phương sai CV cực lớn (Fold 2 = 0.55) → model không ổn định.

### 2.4. BẰNG CHỨNG QUYẾT ĐỊNH — chấm lại chính model v2 trên 7 nhãn THẬT
Mentor lấy **đúng model + đúng tập test** của nhóm, nhưng chấm prediction trên **ground-truth thật** (7 anomaly gốc) thay vì nhãn tự tạo:

| Chấm trên | Precision | Recall |
|---|---|---|
| Nhãn của nhóm (tái hiện báo cáo) | 0.774 | 0.998 |
| **Nhãn THẬT (7 anomaly gốc)** | **0.118** ❌ | 0.987 |

```
Trong 646 dòng model BÁO ANOMALY trên test:
  - anomaly THẬT          :  76  (11.8%)
  - bẫy FP benign         :   0  (0.0%)
  - resource BÌNH THƯỜNG  : 570  (88.2%)  <-- báo nhầm
Top báo nhầm: res-amazondynamodb-1230, bucket-1250/1054, db-1025/1026/1027 (RDS prod),
              res-amazoncloudwatch-1046, res-awslambda-1214 ... (đều là production khỏe mạnh)
```
→ **Ngoài đời ~88% cảnh báo là sai.** Precision thật = **0.118**, không phải 0.97. Đây là hệ quả trực tiếp của việc gán nhãn "cost cao = anomaly": model học cách báo mọi resource đắt tiền.

---

## 3. 🟡 SHOULD-FIX (kể cả sau khi sửa nhãn)

- **`ddb_flag` là anti-pattern "học danh tính"** (`FEATURE_v2.py:237`): hardcode `service == 'AmazonDynamoDB'` để dập FP cho DDB = đúng kiểu `cost_X_staging` bản trước, chỉ đổi chỗ. Vá triệu chứng, không sửa gốc (gradual_drift của ddb bị nhận diện kém).
- **`scale_pos_weight` vô hiệu với `multi:softprob`** (`DETECT_v2.py:108`): tham số này chỉ cho binary; train 3 lớp nên bị bỏ qua. Thực tế chỉ `sample_weight='balanced'` (`:80`) có tác dụng → hiểu lầm khái niệm + double-weighting.
- **Threshold không nhất quán với báo cáo** (`DETECT_v2.py:165-168`): `best_threshold` chỉ áp cho `prob[:,1]` khi vẽ confusion matrix, nhưng `classification_report` dùng `model.predict()` (argmax). Hai cách quyết định khác nhau → số khó diễn giải; lớp `benign` bị gộp vào "False" khi đánh giá nhị phân.
- **v2 "augmentation" tiêm nhiễu Gaussian rồi nhân đôi train** (`DETECT_v2.py:85-97`): ít giá trị; perturbation test sau đó (drop 0.003) gần như chắc pass vì model đã thấy chính loại nhiễu đó → không chứng minh được robustness thật.

---

## 4. 🟢 NIT

- `w1.py` chỉ là script scaffolding (ghi header `FEATURE.py`) — không nên commit.
- `__pycache__/`, `mlflow.db`, `mlruns/` (12 model artifacts ~25MB), 5 CSV metrics (~20MB), nhiều PNG — nên đưa vào `.gitignore`, tách khỏi git.

---

## 4b. Tư vấn: về việc "generate thêm data vì dataset imbalance"

Động cơ của nhóm là đúng (dữ liệu ít positive, mất cân bằng), nhưng đang **gộp 2 vấn đề khác nhau** và chọn sai thuốc. Cần tách bạch:

**Vấn đề 1 — Mất cân bằng lớp (chỉ ~1.7% positive).** Đây là vấn đề **dễ, không cần sinh data**:
- `scale_pos_weight` (binary) / `sample_weight='balanced'` (multiclass) — phạt nặng lỗi trên lớp hiếm.
- Chọn ngưỡng trên **PR-curve**; đánh giá bằng **PR-AUC + precision/recall của lớp anomaly**, KHÔNG dùng accuracy/weighted-avg.
- *Khuyến nghị:* hãy **tự thực nghiệm** class-weight + tuning ngưỡng trước khi nghĩ tới sinh data — đây mới là cách xử lý imbalance chuẩn, và thường cải thiện đáng kể mà không cần thêm 1 dòng nào.

**Vấn đề 2 — Quá ít "sự kiện" độc lập (chỉ 7 anomaly).** Đây mới là ràng buộc thật và là cái nhóm đang cố vá:
- 420 dòng positive nhưng chỉ từ **7 câu chuyện**. Sinh thêm dòng **không tạo thêm thông tin** — chỉ nhân bản (hoặc bịa ra) đúng 7 pattern đó. Fold 2 CV sụp còn 0.55 chính là biểu hiện của ít-sự-kiện.
- Sinh data **không bao giờ** chữa được scarcity của nhãn; nó chỉ làm metric *trông* đẹp hơn.

**3 quy tắc vàng nếu vẫn muốn augment** (hiện nhóm vi phạm cả 3):
1. Chỉ áp lên **tập train**, tuyệt đối không để synthetic chạm test/val.
2. Chỉ sinh cho **lớp thiểu số**, và phải **giữ cấu trúc thật** (time-series → jitter nhẹ trên chuỗi của *chính resource đó*, không SMOTE/nội suy chéo resource).
3. **Không bao giờ bịa ra tín hiệu nhãn** — đừng sinh sensor/feature mang sẵn đáp án (đây là lỗi nặng nhất hiện tại: CPU/mem được sinh theo nhãn).

**Hướng đúng cho "ít sự kiện" (khuyến nghị):**
- **Right-size framing:** với 7 sự kiện, nên nghiêng về **phát hiện thống kê/unsupervised theo từng cơ chế** (robust z cho spike, slope cho drift, cost/usage cho idle, tag rỗng cho untagged, weekend cho runaway). Các detector này **không cần nhãn để train**, chỉ dùng 3 nhãn public để calibrate; để dành 7 anomaly cho **đánh giá**.
- **Hybrid:** detector thống kê sinh cảnh báo ứng viên → một lớp lọc (rule hoặc supervised nhẹ) dùng 3 bẫy FP để dập false positive.
- Nếu vẫn supervised: **GroupKFold/leave-one-anomaly-out** (không random), báo cáo **per-mechanism**, chấp nhận sai số rộng một cách trung thực.

**Câu chốt để nói với nhóm:** *"Thêm dòng ≠ thêm thông tin. Khi các dòng mới đến từ cùng vài pattern (hoặc bị bịa), các em chỉ đang dạy model một đáp án giả rồi tự chấm trên nó. Bằng chứng: model đạt 0.97 trên nhãn tự tạo nhưng chỉ 0.118 trên nhãn thật."*

---

## 5. Việc cần làm (ưu tiên)

1. **Xác nhận nguồn gốc metrics** với mentor. Nếu nhóm tự sinh → đây là dạng leakage mới: tự tạo cảm biến + nhãn rồi tự chấm.
2. **Dựng nhãn bám đúng anomaly đã thực sự inject** (resource×ngày), bẫy FP gán negative — KHÔNG dùng heuristic "cost cao".
3. **Không dùng sensor synthetic** trừ khi đề cấp chính thức; thử hướng **feature hành vi chi phí** (robust_z, slope, peer_ratio, cost_per_usage, tag…) và **tự thực nghiệm** để tìm tập feature + ngưỡng đạt KPI.
4. Bỏ `ddb_flag` / `absolute_cost_spike` (band-aid); sửa `scale_pos_weight`; thống nhất threshold ↔ classification_report.
5. Báo cáo **per-anomaly-type** + xác nhận **không báo 3 bẫy FP**; nhìn precision/recall của riêng lớp anomaly, không nhìn weighted-avg/accuracy.

---

## Phụ lục — Bằng chứng & cách tái lập

| Bằng chứng | Cách tái lập |
|---|---|
| Nhãn 34 anomaly / 33 benign (vs thật 7/3); 23 resource normal cost cao bị gán anomaly | Đối chiếu cột `label` trong các `*_metrics.csv` với `cur_line_items.csv` (xếp theo tổng cost) |
| SHAP top = sensor synthetic; ROC-AUC 0.9998 | `data/shap_bar.png`, `data/roc_pr_curves_v2.png` (do nhóm sinh) |
| anomaly precision 0.774 (argmax), CV Fold 2 = 0.554 | Chạy lại `DETECT_v2.py` |
| **Re-score trên nhãn THẬT: precision 0.118 (88% cảnh báo sai)** | Lấy model + tập test của `DETECT_v2.py`, chấm prediction theo đáp án 7 anomaly gốc |
| 7 anomaly thật (~$40,882) + 3 bẫy FP | EDA `cur_line_items.csv`: dò resource_id + trend slope (mentor giữ đáp án) |
