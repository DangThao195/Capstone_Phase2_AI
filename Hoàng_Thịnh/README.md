# FinOps Watch Sandbox

Prototype detector cho bài toán FinOps Watch trên bộ dữ liệu sandbox 3 tháng. Phiên bản hiện tại dùng pipeline `XGBoost supervised`: feature engineering từ CUR/Cost Explorer, baseline robust `median/MAD`, chia `train/test` theo thời gian, `walk-forward cross-validation`, FP suppressor, incident grouping, re-ranking và action payload an toàn.

## Thành phần chính

- `engine-skeleton/main.py`: FastAPI app và local runner.
- `engine-skeleton/detect_core.py`: core detector dùng chung cho API và notebook-style flow.
- `engine-skeleton/detect_cells.py`: file `# %%` để chạy từng bước như notebook.
- `data/`: sandbox dataset gồm `cost_explorer_daily.csv`, `cur_line_items.csv`, `anomaly_labels_public.csv`, và telemetry metrics.
- `docs/assets/detect/`: output mẫu từ local run.

## Detector hiện tại

- Bắt 5 nhóm anomaly: `untagged_spend`, `idle_resource`, `runaway_usage`, `sudden_spike`, `gradual_drift`.
- XGBoost học trực tiếp trên label `metrics.label` ở mức `resource-day`, với target nhị phân `anomaly` vs `normal/benign`.
- Train/test split theo thời gian:
  - `train_end_date = 2026-05-03`
  - `test_start_date = 2026-05-04`
- Cross-validation trong train window dùng `walk-forward expanding window`.
- Threshold được chọn từ `out-of-fold` prediction nếu đủ fold; nếu không đủ dữ liệu thì fallback về `in-sample train`.
- Category encoding được fit theo `train window` để giảm contamination từ future categories.
- Baseline rolling cho pair/resource dùng `rolling median + MAD`.
- FP suppressor xử lý các context kiểu `migration`, `load test`, `campaign`, `benign growth`, và dữ liệu `estimated`.
- RCA có 2 mode:
  - mặc định: fallback deterministic theo rule template
  - tùy chọn: Bedrock LLM để viết `executive_summary` và `technical_reason`
- Action output dùng payload an toàn `action_type + resource_id + parameters`, không sinh AWS CLI trực tiếp.

## Cách chạy

Chạy detector local và sinh artifact:

```powershell
python engine-skeleton\main.py
```

Chạy bản theo từng cell:

```powershell
python engine-skeleton\detect_cells.py
```

Bật API local:

```powershell
uvicorn engine-skeleton.main:app --reload
```

Smoke test:

```powershell
python -m unittest tests.test_engine_api tests.test_llm_rca
```

## Bật LLM RCA

LLM là tùy chọn. Nếu không set env vars bên dưới, engine tự fallback về RCA deterministic.

```powershell
$env:FINOPS_LLM_PROVIDER='bedrock'
$env:FINOPS_BEDROCK_REGION='ap-southeast-1'
$env:FINOPS_BEDROCK_MODEL_ID='amazon.nova-lite-v1:0'
python engine-skeleton\main.py
```

## Kết quả sandbox hiện tại

- `total_anomalies_found = 23`
- Public sanity check:
  - bắt `A2`
  - bắt `A6`
  - không bắn nhầm `B2`
- Public FP gate hiện tại:
  - `public_false_positive_rate = 0.0`
  - `fp_requirement_max = 0.10`
  - `fp_requirement_passed = true`
- Temporal split hiện tại:
  - `model_type = xgboost_supervised_binary`
  - `feature_scaler = not_required_tree_model`
  - `cross_validation_strategy = walk_forward_expanding_window`
- Evaluation summary:
  - `threshold_source = walk_forward_oof`
  - `threshold = 0.20`
  - holdout pipeline metrics: `precision = 0.9983`, `recall = 0.7756`, `fpr = 0.0001`

## Artifact đầu ra

Incident output:

- `docs/assets/detect/demo_result.json`
- `docs/assets/detect/detected_events.csv`
- `docs/assets/detect/public_eval.json`

Evaluation bundle:

- `docs/assets/detect/split_summary.json`
- `docs/assets/detect/cv_summary.json`
- `docs/assets/detect/fold_metrics.csv`
- `docs/assets/detect/holdout_test_metrics.json`
- `docs/assets/detect/holdout_predictions.csv`
- `docs/assets/detect/cv_oof_predictions.csv`

## Ghi chú

- Đây là detector supervised cho sandbox vì đang dùng `metrics.label` làm nhãn train.
- Khi trình bày phải nói rõ đây không còn là unsupervised anomaly detector nữa.
- `detected_events.csv` là incident output trên full sequence, còn `holdout_*` và `cv_*` là artifact đánh giá supervised.
- `S3_POINTER` trong local skeleton hỗ trợ đọc file local theo path tương đối, `file://...`, hoặc `s3://local/...`.
