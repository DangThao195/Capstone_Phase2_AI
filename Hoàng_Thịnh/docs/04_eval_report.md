# Eval Report - FinOps Watch Sandbox

## 1. Methodology

- Setup: local sandbox, Python 3.13, FastAPI `TestClient`.
- Data source: `data/cost_explorer_daily.csv`, `data/cur_line_items.csv`, `data/metrics/*.csv`, và `data/anomaly_labels_public.csv`.
- Pipeline:
  1. Build robust feature từ Cost Explorer + CUR với `rolling median + MAD`.
  2. Join telemetry metrics và label `metrics.label` ở mức `resource-day`.
  3. Chia `train/test` theo thời gian.
  4. Fit category encoding theo `train window`.
  5. Chạy `walk-forward expanding-window CV` trong train window.
  6. Chọn threshold từ `out-of-fold` prediction nếu đủ fold.
  7. Fit `XGBoost` nhị phân `anomaly` vs `normal/benign` trên full train window.
  8. Score toàn bộ chuỗi, rồi suy ra `anomaly_type` bằng heuristic type mapper.
  9. Chạy FP suppressor, incident grouping và rerank.
  10. Export result + evaluate với public labels.
- Temporal split:
  - `train_end_date = 2026-05-03`
  - `test_start_date = 2026-05-04`
- CV strategy:
  - `walk_forward_expanding_window`
  - `max_folds = 3`
- Label note: bản này là supervised sandbox run vì dùng `metrics.label` để train. Khi báo cáo phải nói rõ điều đó.

## 2. Results

| Metric | Target | Actual | Pass/Fail |
|---|---|---|---|
| Public precision sanity check | Catch public anomalies, avoid benign public label | `A2`, `A6` matched; `B2` not hit | Pass |
| Public recall sanity check | No missed public anomaly | `0` missed | Pass |
| Public false positive rate | `<= 10%` | `0.0%` | Pass |
| Smoke test | API + action flow pass | `4/4` tests pass | Pass |

### 2.1 Detector output snapshot

- Total incidents: `23`
- Public FP gate:
  - `public_false_positive_rate = 0.0`
  - `fp_requirement_max = 0.10`
  - `fp_requirement_passed = true`

### 2.2 Training summary

- `train_labeled_rows = 17073`
- `test_labeled_rows = 7460`
- `train_anomaly_rows = 1061`
- `test_anomaly_rows = 762`
- `threshold_source = walk_forward_oof`
- `threshold = 0.20`

### 2.3 Walk-forward CV summary

- `cv_folds_completed = 3`
- `cv_oof_rows = 11527`
- `cv_oof_metrics`:
  - `precision = 0.9809`
  - `recall = 0.8073`
  - `fpr = 0.0015`
- `cv_oof_pipeline_metrics`:
  - `precision = 0.9777`
  - `recall = 0.6470`
  - `fpr = 0.0014`
- Artifact:
  - `docs/assets/detect/cv_summary.json`
  - `docs/assets/detect/fold_metrics.csv`
  - `docs/assets/detect/cv_oof_predictions.csv`

### 2.4 Holdout test summary

Model-only metrics:

- `precision = 0.9987`
- `recall = 0.9934`
- `fpr = 0.0001`

Pipeline metrics after domain guard:

- `precision = 0.9983`
- `recall = 0.7756`
- `fpr = 0.0001`

Artifact:

- `docs/assets/detect/holdout_test_metrics.json`
- `docs/assets/detect/holdout_predictions.csv`

### 2.5 Separation of outputs

- `detected_events.csv` là incident output trên full sequence để phục vụ demo/API.
- `cv_*` và `holdout_*` là artifact đánh giá supervised, không nên trộn với incident count khi báo cáo metric model.

## 3. Risks

- Đây không còn là unsupervised anomaly detection; model đang học trực tiếp từ `metrics.label`.
- Public labels chỉ là sanity check thưa. Claim cuối cùng cho requirement `FP <= 10%` vẫn phải nói rõ đây là kết quả trên sandbox supervised run.
- Holdout pipeline recall giảm so với model-only vì đã bật domain guard và safe-mode logic; đây là trade-off có chủ ý để giữ FP thấp.
- Vì dùng label nội bộ trong metrics, narrative phù hợp nhất là: `supervised detector for sandbox familiarization / backtest`, không phải `label-free anomaly engine`.
