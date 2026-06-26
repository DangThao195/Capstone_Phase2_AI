# Nhận xét chi tiết — FinOps Watch Anomaly Detection
### Gửi: Thảo & nhóm AI-RESEARCH (Trường, Hảo) · Về: `data/detect.ipynb`

Chào em,

Anh đã đọc kỹ cả hai bản em push (`48a43e0` và bản `d482463 "fix split train/test"`). Bản nhận xét này **dài và chi tiết có chủ đích** — vì mục tiêu không phải chỉ chấm đúng/sai, mà để em **hiểu bản chất** và tự làm lại cho chuẩn. Em đọc từ từ, mỗi phần đều có "vì sao" và "sửa thế nào".

> **Tóm tắt 1 câu:** Hướng đi của em (boosting / XGBoost) là **đúng**. Nhưng cách *dựng dữ liệu* và *đánh giá* đang làm cho con số 0.99 trở thành **ảo** — model thực ra chưa giải được bài toán. Tin tốt: anh đã thử lại đúng cách và **đạt KPI (precision 0.92)**, nên bài toán giải được, chỉ cần làm lại phương pháp.

---

## 1. Những điều em đã làm ĐÚNG (giữ lại)

Trước khi nói cái sai, ghi nhận cái đúng để em biết mình đang đứng ở đâu:

- ✅ Dùng **rolling median + MAD** để đo độ lệch chi phí — tư duy rất đúng cho anomaly.
- ✅ Biết **không được random split chuỗi thời gian**, đã chuyển sang **walk-forward** — đúng tinh thần backtest.
- ✅ Dùng **SHAP** để giải thích model — rất tốt, sẽ dùng lại cho phần RCA.
- ✅ Chọn **gradient boosting** — phù hợp với bài toán bảng (tabular) + imbalanced.
- ✅ Có ý thức về imbalance (dùng `sample_weight`).

Em có nền tốt. Vấn đề nằm ở vài **hiểu lầm nền tảng** — lấp xong là lên trình hẳn.

---

## 2. Lỗi gốc rễ #1 — Em đang "tự ra đề rồi tự chấm" 🔴 (quan trọng nhất)

### Hiện tượng
Trong notebook, em dùng **SMOTE** để sinh ~538 "ngày giả" (gán ngày tương lai từ 01/06/2026), rồi **trộn chung** với 92 ngày thật, **sau đó mới** chia train/test.

### Vì sao sai (đọc kỹ phần này)
Anh đã kiểm tra: **cả 3 fold của em test 100% trên dữ liệu giả**. Toàn bộ 92 ngày thật rơi hết vào tập train của Fold_1. Nghĩa là:

```
Fold_1: TEST -> 0 ngày THẬT / 94 ngày GIẢ
Fold_2: TEST -> 0 ngày THẬT / 95 ngày GIẢ
Fold_3: TEST -> 0 ngày THẬT / 126 ngày GIẢ
```

Dữ liệu giả đó được **sinh ra từ chính 3 nhãn** em có. Nên khi em chấm model trên đó, em đang hỏi model: *"đoán lại cái tao vừa bịa ra từ đáp án xem"*. Model đoán đúng → 0.99. Nhưng đây là **đề thi do chính em ra từ đáp án**, không đo được năng lực thật.

> 🎓 **Khái niệm: train/test split tồn tại để làm gì?**
> Để ước lượng model chạy thế nào trên **dữ liệu thật mà nó CHƯA TỪNG thấy**. Quy tắc bất di bất dịch:
> **Tập test phải là dữ liệu thật, độc lập, và KHÔNG được sinh ra từ thông tin nhãn.**
> SMOTE (nếu dùng) **chỉ áp lên tập train, SAU KHI đã chia**. Không bao giờ trước.

### Bằng chứng ngay trong notebook em
Nhìn lại `classification_report` của em: dòng `anomaly` có **precision = 0.41 / 0.48 / 0.36**. Con số `0.99` em thấy là **weighted average** — bị lớp `normal` (mấy nghìn dòng) kéo lên. Precision **thật** của việc bắt anomaly chỉ **~0.4**, tức cứ 10 lần em báo "bất thường" thì 6 lần **báo nhầm**. Đây mới là con số đúng để so với KPI 0.80.

### Sửa thế nào
1. **Bỏ hoàn toàn SMOTE và dữ liệu tương lai bịa.** Backtest chỉ chạy trên **92 ngày thật**.
2. Imbalance xử lý bằng **`scale_pos_weight`** của XGBoost (giải thích ở Mục 7) — boosting tự cân bằng, không cần SMOTE.
3. Khi đọc kết quả: **luôn nhìn precision/recall của riêng lớp anomaly**, đừng nhìn accuracy hay weighted-avg.

---

## 3. Lỗi gốc rễ #2 — Bản "fix split" chưa chạm đúng chỗ 🔴

Mình rất quý việc em chủ động sửa, nhưng bản `d482463` mới chỉ đổi *single-split → walk-forward 3 fold*. Lỗi thật (SMOTE trước split) **vẫn nguyên**, nên kết quả vẫn ảo y như cũ.

Ngoài ra em **thêm 2 feature mới** mà anh phải nói thẳng là **đi lùi**:

```python
df['cost_X_staging']  = df['unblended_cost'] * (df['linked_account_name'] == 'staging')
df['deviation_X_rds'] = df['cost_deviation']  * (df['service_code'] == 'AmazonRDS')
```

Đây là em đang **mã hóa thẳng đáp án** vào feature: anomaly A2 đúng là ở `staging` + `AmazonRDS`, nên 2 feature này = "mách nước" cho model. Trên dữ liệu này nhìn có vẻ tăng điểm, nhưng khi anh chấm trên **đáp án đầy đủ** (anomaly ở account/service khác), model sẽ **mù hoàn toàn**. (Xem tiếp Mục 4.)

---

## 4. Lỗi gốc rễ #3 — Model học "DANH TÍNH", không học "HÀNH VI" 🔴

### Hiện tượng
Em one-hot toàn bộ `account / service / region`, cộng thêm 2 feature `cost_X_staging` / `deviation_X_rds`. Kết quả: model học được luật kiểu *"hễ là staging + RDS thì là anomaly"*.

### Vì sao sai
Bài toán không phải "staging-RDS có xấu không", mà là **"chi tiêu này có BẤT THƯỜNG so với chính nó/đồng loại không"**. Có rất nhiều staging-RDS **bình thường**. Nếu model học theo danh tính, nó sẽ:
- Báo nhầm **mọi** staging-RDS bình thường → FP nổ.
- Bỏ sót anomaly ở account/service khác (4/5 loại còn lại nó chưa từng "được mách").

> 🎓 **Học hành vi (behavioral features), không học danh tính (identity).**
> Thay vì "đây là resource nào", hãy hỏi "**resource này hôm nay lệch bao nhiêu so với chính nó tuần trước / so với đồng loại?**". Loại feature này khái quát được sang resource mới — đó là thứ anh sẽ chấm.

### Sửa thế nào
- **Bỏ** one-hot `resource`/`region` identity và 2 feature hardcode.
- Dùng feature **tương đối** (Mục 6). Trong baseline của anh, top feature là `peer_ratio`, `cost_per_usage`, `usage_density` — toàn feature hành vi, không có cái nào là "danh tính".

---

## 5. Hiểu dữ liệu trước khi code — 7 anomaly thuộc 5 "kiểu", mỗi kiểu cần tín hiệu khác nhau

Đây là phần em (và nhóm) **bỏ qua** và là lý do sâu xa khiến model chỉ bắt được 1 loại. Anh đã EDA và tìm ra đủ 7 anomaly + 3 bẫy FP (em chỉ có 3 nhãn, nhưng cả 7 đều tìm được từ dữ liệu — file `review/eda/out/ground_truth_registry.csv`):

| Anomaly | Kiểu | Nhận ra bằng tín hiệu gì | Cảnh báo |
|---|---|---|---|
| untagged fleet ($13.6k) | untagged_spend | **Tag `team` rỗng** | KHÔNG phải spike — chạy đều đều. Model nhìn "cost tăng" sẽ KHÔNG thấy. |
| ddb drift ($13.3k) | gradual_drift | **Độ dốc xu hướng** (60→325$/ngày qua nhiều tuần) | Mỗi ngày chỉ +$5 → nhìn 1 ngày là vô hình. |
| GPU cluster ($6.6k) | runaway_usage | **Chạy cả cuối tuần** + máy mới + GPU đắt | |
| orphan RDS ($2k) | idle_resource | **Cost cao / usage thấp**, kéo dài | |
| orphan EBS ($0.8k) | idle_resource | Volume mồ côi, cost đều | Cost nhỏ → dễ sót. |
| natgw spike ($2.6k) | sudden_spike | Cost vọt lên rồi về (vs rolling median) | |
| debug logs ($1.8k) | sudden_spike | Cost vọt lên rồi về | |

**3 bẫy False Positive (benign — TUYỆT ĐỐI không được báo):**
- flash-sale `$913/ngày × 4 ngày` (spike còn to hơn anomaly thật!), load test, migration egress.

> 💡 **Hai bài học rút ra:**
> 1. **Không một feature nào bắt được cả 5 kiểu.** Em phải làm feature phủ đủ: độ lệch (spike), xu hướng (drift), cuối tuần (runaway), cost/usage (idle), tag rỗng (untagged).
> 2. **3 bẫy FP chính là phép thử KPI `FP ≤ 10%`.** Model nào cứ "thấy cost tăng là báo" sẽ báo cả 3 → trượt KPI. Phân biệt *bất thường* vs *tăng có kế hoạch* là phần khó nhất của đề.

---

## 6. Sai lầm về GRANULARITY — em đang nhìn ở tầng quá thô 🟠

Em detect ở mức `ngày × account × service` (file `cost_explorer_daily.csv`, ma trận 92×32). Vấn đề: ở tầng gộp này, nhiều anomaly **biến mất**:

```
untagged $148/ngày  ->  chìm trong bucket prod-payments×EC2 ($416-1480/ngày), chỉ 26.7%
gradual_drift +$5/ngày  ->  vô hình khi nhìn theo ngày
```

> 🎓 **Unit of analysis (đơn vị phân tích).** Anomaly xảy ra ở mức **resource**, nên đơn vị đúng là **(resource × ngày)** — lấy từ `cur_line_items.csv`, KHÔNG phải aggregate.
> Mỗi dòng = "1 tài nguyên trong 1 ngày", có nhãn 0/1. File mẫu: `review/eda/out/labels_resource_day.csv` (24,533 dòng).

---

## 7. Một cái bẫy ML tinh vi: "7 sự kiện, 420 dòng" 🟠

Khi em dựng nhãn ở mức resource×ngày, sẽ có **420 dòng anomaly** — nhưng chúng đến từ chỉ **7 sự kiện** (7 nhóm anomaly). 

Nếu em chia train/test kiểu thường (hoặc random K-fold), **cùng một resource sẽ nằm ở cả train lẫn test**. Model chỉ cần "nhớ mặt" resource đó là đoán đúng test → điểm cao giả, nhưng sụp trên đáp án anh.

> 🎓 **Effective sample size = số sự kiện độc lập, không phải số dòng.** Em có 420 dòng nhưng chỉ 7 "ví dụ" thật để học khái quát. Vì vậy:
> - **Temporal split**: train tháng 3–4, test tháng 5 (đúng tinh thần "phát hiện sớm").
> - **GroupKFold theo `resource_id`**: đảm bảo resource ở test chưa từng xuất hiện trong train.
> Đây gọi là **leakage qua nhóm** — lỗi kinh điển, nhớ kỹ.

---

## 8. Cách làm ĐÚNG — blueprint từng bước (em làm theo thứ tự này)

### Bước 1 — Dữ liệu & nhãn
- Đọc `cur_line_items.csv`, gom về **(resource_id, ngày)**.
- Gán nhãn: `1` nếu resource×ngày nằm trong cửa sổ 1 trong 7 anomaly; `0` nếu không.
- **3 bẫy FP gán `0`** (chúng là "negative khó" — cố tình cho model học cách KHÔNG báo).

### Bước 2 — Feature HÀNH VI (phủ đủ 5 kiểu)

| Nhóm | Feature cụ thể | Bắt kiểu nào |
|---|---|---|
| Độ lệch | `cost_z = (cost − rolling_median_28d) / (1.4826·MAD)` của **chính resource** | spike, idle |
| Xu hướng | `slope` hồi quy 14 ngày; `% thay đổi` vs 28 ngày trước | **drift** |
| Lịch | `is_weekend`; tỉ lệ cost cuối tuần / ngày thường | **runaway** |
| Hiệu suất | `cost / (usage + ε)`; `usage_density = usage/24` | **idle** |
| Tag | `team_missing`, `owner_missing` | **untagged** |
| Đồng loại | `peer_ratio = cost / median(cost của resource cùng account+service trong ngày)` | idle, runaway |
| Vòng đời | `age_days` (resource mới?) | runaway, spike |

→ **KHÔNG one-hot resource identity. KHÔNG feature hardcode account/service cụ thể.**

### Bước 3 — Model & imbalance (KHÔNG SMOTE)
```python
neg, pos = (y_train == 0).sum(), (y_train == 1).sum()
model = xgb.XGBClassifier(
    n_estimators=400, max_depth=4, learning_rate=0.03,
    subsample=0.8, colsample_bytree=0.7,
    reg_alpha=1.0, reg_lambda=2.0, min_child_weight=3,
    scale_pos_weight=neg/pos,   # <-- xử lý imbalance ĐÚNG CÁCH, thay cho SMOTE
    eval_metric='aucpr')        # PR-AUC, hợp cho imbalanced (không dùng accuracy)
```
> 🎓 `scale_pos_weight` bảo cây boosting "coi mỗi mẫu anomaly nặng gấp ~N lần mẫu normal". Hiệu quả hơn và an toàn hơn SMOTE rất nhiều cho dữ liệu chuỗi thời gian.

### Bước 4 — Chọn ngưỡng theo Precision–Recall (đây là chìa khóa chạm KPI)
KPI là bài toán **ưu tiên precision**. Đừng hardcode 0.45. Làm thế này:
```python
from sklearn.metrics import precision_recall_curve
prec, rec, thr = precision_recall_curve(y_val, prob_val)
ok = np.where(prec[:-1] >= 0.80)[0]          # các ngưỡng cho precision >= 80%
chosen = thr[ok[np.argmax(rec[ok])]]         # trong số đó, lấy cái recall cao nhất
```
Chọn ngưỡng trên **validation**, rồi chạy **1 lần** trên test → đó là số báo cáo.

### Bước 5 — Báo cáo (đúng yêu cầu đề)
- Precision/Recall/F1 của **riêng lớp anomaly** (bỏ weighted-avg).
- **Confusion matrix** + **breakdown theo từng `anomaly_type`** (bắt được idle? drift? runaway?).
- Kiểm tra rõ: model có **KHÔNG báo** 3 bẫy FP không.
- SHAP để giải thích từng cảnh báo → nối sang phần RCA (Amazon Nova) nhóm đã thiết kế.

---

## 9. Mục tiêu thực tế & lời nhắc thật lòng

Anh đã chạy thử đúng phương pháp trên: đạt **Precision 0.92 / FP-rate 0.001** trên backtest tháng 5 **thật**. Vậy **KPI hoàn toàn trong tầm tay** — nhưng chỉ khi đo trung thực.

Hai chỗ sẽ khó, em chuẩn bị tinh thần:
1. **Bẫy FP benign** (flash-sale, load test): rất khó phân biệt với anomaly thật nếu chỉ nhìn cost. Cần nghĩ thêm feature: nó có lặp lại theo lịch không? có tag/ticket không? kéo dài bao lâu? Ai giải quyết được phần này là **xuất sắc**.
2. **gradual_drift**: dễ bị sót vì tăng quá chậm — feature xu hướng (slope) phải đủ tốt.

> ⚠️ Một lời nhắc quan trọng về **thái độ làm ML**: khi gặp KPI khó, phản xạ vừa rồi của em là *"chỉnh cho số đẹp lên"* (nâng SMOTE ×3, thêm feature mách nước). Hãy đổi sang tư duy ngược lại: **đo cho thật trước, rồi mới tối ưu**. Một con số 0.41 **trung thực** quý hơn một con số 0.99 **ảo** — vì cái 0.41 nói cho em biết phải sửa gì, còn cái 0.99 chỉ giấu vấn đề tới ngày bị anh chấm.

---

## 10. Checklist tự kiểm trước khi nộp lại

- [ ] Đã bỏ SMOTE và toàn bộ dữ liệu tương lai bịa?
- [ ] Detect ở mức **resource × ngày** (từ CUR), không phải aggregate?
- [ ] Nhãn đủ 7 nhóm anomaly; 3 bẫy FP gán negative?
- [ ] Feature toàn **hành vi**; đã bỏ one-hot identity + `cost_X_staging`/`deviation_X_rds`?
- [ ] Split **temporal** + thử **GroupKFold theo resource**; test chỉ trên dữ liệu thật?
- [ ] Dùng `scale_pos_weight` thay SMOTE?
- [ ] Chọn ngưỡng bằng **PR-curve** (không hardcode)?
- [ ] Báo cáo **per-anomaly-type** + xác nhận **không báo 3 bẫy FP**; bỏ weighted-avg?
- [ ] Viết ADR cho time-frame (12/24/48h) & granularity?