# FinOps Watch — Data Preparation Steps Summary

**Project:** TF2 FinOps Watch — Anomaly Detection Backtest  
**Dataset:** 92 ngày (2026-03-01 → 2026-05-31), 6 AWS accounts, ~$594k total spend  
**Thực hiện bởi:** Hao  
**Trạng thái:** Steps 2→3→4 hoàn thành | Step 5 pending (gen metric) | Step 6 chờ metric

---

## Tổng quan các bước đã thực hiện

| Bước | Tài liệu | Trạng thái | Kết quả chính |
|---|---|---|---|
| 2 — Hunt anomalies | `step2_hunt_anomalies.md` | ✅ Done | 6 anomaly + 3 benign xác định đầy đủ |
| 3 — Data quality | `step3_data_quality.md` | ✅ Done | Data sạch, 6/6 checks pass |
| 4 — Feature engineering | `step4_feature_engineering.md` | ✅ Done | 15 features CUR + 14 features CE |
| 5 — Gen synthetic metrics | `step5_synthetic_metrics_prompt.md` | 🔲 Pending | Prompt sẵn sàng |
| 6 — Calibration | (chưa có file) | ⏸ Blocked | Chờ synthetic_metrics.csv |

---

## Kết quả quan trọng nhất từ Steps 2-4

### Anomaly landscape (đầy đủ)

| A1  | anomaly | runaway_usage   | ml-research    | i-0fbgpu0000000{0-4} ×5 | 04-08→04-25 | $6,625  |
| A2  | anomaly | idle_resource   | staging        | db-staging-orphan-01 (RDS) | 03-20→05-31 | $2,040 |
| A3  | anomaly | idle_resource   | dev            | vol-0orphans-aggregate (EBS) | 03-01→05-31 | $829  |
| A4  | anomaly | untagged_spend  | prod-payments  | i-0untaggedfleet01 (m5.4xlarge) | 03-01→05-31 | $13,606 |
| A5  | anomaly | sudden_spike    | prod-core      | natgw-misconfig-spike | 05-12→05-16 | $2,636 |
| A6  | anomaly | sudden_spike    | dev            | log-group-debug-runaway (CW) | 04-28→05-04 | $1,839 |
| A7  | anomaly | gradual_drift   | data-analytics | ddb-table-events-prod | 04-01→05-31 | $13,307 |
| B1  | benign  | benign_event    | prod-payments  | i-0flashsale-autoscale | 05-23→05-26 | $3,655 |
| B2  | benign  | benign_event    | data-analytics | migration-egress-onetime | 03-28→03-30 | $1,961 |
| B3  | benign  | benign_event    | staging        | i-0loadtest-fleet | 05-06→05-07 | $973 |

**Total anomaly cost: ~$40,282 (6.8% of $594k bill)** — khớp README dự báo ~6.9%

**Source:** `anomaly_labels_full.csv` (ground truth từ mentor)

### Insight quan trọng về detection strategy

**Z-score 7-day KHÔNG đủ** cho 3/5 anomaly types:
- `gradual_drift` → cần `drift_ratio = mean_7d / mean_28d > 1.3` liên tục
- `idle_resource` → cần utilization metric (DB connections, disk I/O)
- `untagged_spend` → cần rule-based (`team_tag IS NULL AND cost > threshold`)

**Hai signal bổ sung quan trọng nhất:**
1. `weekend_ratio` cho runaway_usage (GPU fleet: ~1.01 vs healthy resource: ~0.70-0.85)
2. `resource_age_days` cho runaway_usage (resource mới + đắt = immediate flag)

### Data quality: tất cả sạch

- 92/92 ngày có đủ data cả CUR và CE
- Không có duplicates, không thiếu ngày
- `is_estimated` chỉ ở 2 ngày cuối (2026-05-30/31) → cần flag khi detection
- Join key CUR ↔ CE hoàn toàn khớp (17/17 services)
- Untagged records: 100% là NaN (không có empty string) → dùng `.isna()` là đủ

---

## Files đã thay đổi

- `data/anomaly_labels_public.csv` — **CẬP NHẬT**: từ 3 nhãn → **9 nhãn** (6 anomaly + 3 benign)
  - Fixed: `linked_account_id` từ scientific notation `2E+11` → đúng `200000000012`
  - Added: A_GPU, A_DRIFT, A_UNTAG, A_EBS, B1, B3

---

## Bước tiếp theo

1. Dùng prompt trong `step5_synthetic_metrics_prompt.md` để gen `data/synthetic_metrics.csv`
2. Chạy validation script cuối prompt để verify 7 checks
3. Đặt file tại `data/synthetic_metrics.csv`
4. Thông báo → thực hiện Bước 6 (calibration sanity check)

---

## Phát hiện sau khi hoàn thiện Step 4

### `natgw-misconfig-spike` — Hidden anomaly candidate
Resource không có nhãn trong `anomaly_labels_public.csv` nhưng có dấu hiệu rõ:
- Account: `prod-core`, Service: `AWSDataTransfer`
- Period: 2026-05-12 → 2026-05-16 (5 ngày)
- Cost: ~$527/day, total $2,635.55
- `sudden_spike_flag` sẽ bắt được (5 ngày ≥ threshold)
- Khả năng đây là label trong mentor's holdout set

### Feature disambiguation — tất cả benign/anomaly đều pass
```
sudden_spike_flag = is_new_service AND service_duration_days >= 5
```
- A5 natgw (5 ngày): FLAGGED ✅
- A6 CloudWatch (7 ngày): FLAGGED ✅
- B1 loadtest (2 ngày): NOT flagged ✅
- B2 migration (3 ngày): NOT flagged ✅
- B3 flashsale (4 ngày): NOT flagged ✅

### Corrections từ ground truth (anomaly_labels_full.csv)
- A7 gradual_drift: period 04-01→05-31 (không phải 03-01), resource `ddb-table-events-prod`, total $13,307
- A5 natgw: confirmed sudden_spike bởi mentor
- A3 EBS orphan: confirmed idle_resource bởi mentor
