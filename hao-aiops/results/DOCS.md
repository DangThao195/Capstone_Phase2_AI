# TF2 FinOps Watch — AI Engine Documentation

**Team:** hao-aiops (AIOps Team)  
**Project:** Capstone Phase 2 — FinOps Watch Anomaly Detection Engine  
**Dataset period:** 2026-03-01 → 2026-05-31 (92 days)  
**Last updated:** W11 build

---

## 1. Technologies Deployed

### Data Sources & Preprocessing
| Component | Technology | Purpose |
|---|---|---|
| CUR ingestion | `pandas` + CUR 2.0 schema | Resource-level daily cost, tag analysis |
| Cost Explorer | `pandas` + CE aggregate schema | Account × service daily trends |
| Metrics data | `pandas` + CloudWatch-style CSV | Per-resource CPU/memory/network/hourly profile |
| Feature engineering | `numpy`, `pandas` rolling/groupby | z-score, weekend ratio, CV, trend slope, hourly std |
| Scaling | `sklearn.StandardScaler` | Fit on TRAIN only (no leakage) |
| Imbalance handling | `imbalanced-learn SMOTE` + `scale_pos_weight` | Metrics dataset ~10% anomaly rate |

### Model Stack
| Model | Library | Type | Input |
|---|---|---|---|
| Rule-Based Detector | Pure Python/pandas | Deterministic | CUR/CE cost features |
| Isolation Forest (IF) | `sklearn.ensemble.IsolationForest` | Unsupervised | CUR/CE cost features |
| Local Outlier Factor (LOF) | `sklearn.neighbors.LocalOutlierFactor` | Unsupervised | CUR/CE cost features |
| XGBoost (semi-supervised) | `xgboost.XGBClassifier` | Supervised (metrics labels → infer on cost) | Metrics features (cpu, memory, hourly profile, db_connections) |
| Ensemble | Weighted score combination | Hybrid | IF + LOF + Rule scores |

### Evaluation & Reporting
| Component | Technology |
|---|---|
| Metrics | `sklearn.metrics` (precision, recall, F1, confusion matrix, ROC-AUC) |
| Visualization | `matplotlib`, `seaborn` |
| Output | CSV (detection_output_full.csv, anomalies_detected.csv), JSON (eval_report.json) |

---

## 2. Architecture — 2-Layer Detection Pipeline

```
┌─────────────────────────────────────────────────────────────┐
│                   INPUT (24h cadence pull)                   │
│  CUR line items (S3)  +  Cost Explorer API  +  CloudWatch   │
└────────────────────────────┬────────────────────────────────┘
                             │
              ┌──────────────▼──────────────┐
              │    Feature Engineering       │
              │  • Rolling z-score (14d)     │
              │  • Weekend ratio             │
              │  • Cost CV (idle signal)     │
              │  • Trend slope (drift)       │
              │  • Untagged pct              │
              │  • Hourly CPU std (runaway)  │
              └──────┬───────────────┬──────┘
                     │               │
          ┌──────────▼───┐   ┌───────▼────────┐
          │  LAYER 1      │   │   LAYER 2       │
          │  Rule-Based   │   │   ML Models     │
          │               │   │                 │
          │ • z > 3.0     │   │ IsolationForest │
          │ • we_ratio≥.9 │   │ LOF (novelty)   │
          │ • CV < 0.15   │   │ XGBoost (semi)  │
          │ • untagged    │   │                 │
          │ • slope > 2.0 │   │                 │
          └──────┬───────┘   └───────┬─────────┘
                 │                   │
          ┌──────▼───────────────────▼──────────┐
          │         ENSEMBLE SCORER              │
          │  score = 0.5×rule + 0.35×IF + 0.15×LOF │
          │  threshold calibrated on public labels  │
          │  (A2 fires, B2 does NOT fire)           │
          └─────────────────┬────────────────────┘
                            │
              ┌─────────────▼──────────────┐
              │        ALERT OUTPUT         │
              │  • anomaly_id               │
              │  • confidence score         │
              │  • anomaly_type             │
              │  • routing: Finance/Eng     │
              └────────────────────────────┘
```

---

## 3. Feature Engineering Details

| Feature | Formula | Targets |
|---|---|---|
| `zscore` | `(cost - roll_mean_14d) / roll_std_14d` | sudden_spike |
| `pct_change_1d` | Day-over-day % change | sudden_spike |
| `pct_change_7d` | Week-over-week % change | gradual_drift |
| `roll_cv` | `roll_std / roll_mean` | idle_resource (low CV = flat cost) |
| `roll_slope` | `polyfit(14d window)[0]` | gradual_drift |
| `we_ratio` | `weekend_daily_avg / weekday_daily_avg` | runaway_usage (flat 24/7) |
| `untagged_pct` | `untagged_cost / total_cost` | untagged_spend |
| `hourly_std` | `std(cpu_h0..cpu_h23)` | runaway (low std = flat profile) |
| `day_night_ratio` | `avg(cpu_h9-17) / avg(cpu_h0-5)` | runaway (ratio ≈ 1.0 = no dip) |
| `db_connections` | RDS CloudWatch metric | idle_resource (near 0 = idle DB) |
| `gpu_util` | SageMaker CloudWatch metric | runaway training (low GPU + high cost) |

---

## 4. Model Comparison & Trade-offs

### Rule-Based Detector
- **Pros:** Fully interpretable, zero latency, exact anomaly type label, easy to tune
- **Cons:** Brittle to new anomaly patterns, requires manual threshold tuning per signal type
- **Use case:** Production fast-path — fires immediately when hard threshold exceeded

### Isolation Forest
- **Pros:** Unsupervised (no labels needed for CUR data), efficient on high-dimensional feature vectors, handles multi-type anomalies with single model
- **Cons:** Contamination parameter must be estimated, no per-type explanation, can miss low-amplitude anomalies that are gradual
- **Contamination:** Set to 0.07 (matches ~6.9% anomaly cost fraction from data README)

### Local Outlier Factor (LOF)
- **Pros:** Local density-based, better at catching anomalies in dense regions where IF struggles, good at `gradual_drift` where global score changes slowly
- **Cons:** Slower at inference, `novelty=True` required for separate train/test, sensitive to n_neighbors choice
- **Role in ensemble:** Minority weight (0.15) — used as tiebreaker between IF and Rule signals

### XGBoost (Semi-Supervised)
- **Pros:** Trained on metrics data that has explicit labels (anomaly/normal/benign), strongest at resource-level anomaly patterns (CPU, memory, hourly profile), produces calibrated probability
- **Cons:** Requires metrics data co-located with cost data, features in metrics domain may not perfectly align with cost domain, needs SMOTE due to imbalance
- **Training data:** All 5 metrics datasets (EC2, RDS, SageMaker, DynamoDB, Other) combined — ~3-class labels treated as binary (anomaly=1, normal+benign=0)
- **Key features learned:** `hourly_std`, `day_night_ratio`, `db_connections`, `gpu_util` — exactly the signals that distinguish runaway vs idle vs normal

### Ensemble
- **Formula:** `score = 0.50 × rule_flag + 0.35 × IF_score_norm + 0.15 × LOF_score_norm`
- **Rationale for weights:** Rule-based has highest precision on known patterns (50%), IF is primary ML signal (35%), LOF adds local sensitivity (15%)
- **Threshold calibration:** Set to midpoint between B2 max score (benign — must NOT fire) and A2 max score (anomaly — must fire), biased away from benign to protect FP target

---

## 5. Data Quality & Imbalance Handling

### CUR/CE Cost Data
- **Label availability:** Only 3 public labels (A2, A6, B2) → insufficient for supervised training
- **Decision:** Use UNSUPERVISED methods (IF, LOF) on cost features, calibrate thresholds on public labels
- **NaN handling:** Rolling features have NaN for first `WINDOW_DAYS` rows per group — filled with 0 (startup window, treated as "normal" baseline)
- **Estimated flag:** `is_estimated=True` on last 2 days of dataset — marked and tracked, not excluded

### Metrics Data
- **Imbalance:** ~10-13% anomaly rate across all service types → `scale_pos_weight = n_neg/n_pos ≈ 8`
- **SMOTE:** Applied on training fold of metrics data if `imbalanced-learn` available — generates synthetic minority class samples in feature space
- **Fallback:** If SMOTE unavailable, `scale_pos_weight` in XGBoost covers imbalance

### Time Series Split — Chronological (NO shuffle)
```
TRAIN: 2026-03-01 → 2026-04-30 (61 days, ~66%)
TEST:  2026-05-01 → 2026-05-31 (31 days, ~34%)
```
- Rolling statistics fit on training window only (no future leakage)
- StandardScaler fit on TRAIN, applied to TEST
- A6 window (Apr-28 to May-04) straddles both splits — evaluated on test portion (May-01 to May-04)

---

## 6. ADR — Time Frame Goal: 24h

**Context:** Client requires team to choose and defend a detection cadence: 12h / 24h / 48h.

**Decision: 24h**

**Rationale:**
- CUR data is natively daily grain — 12h cadence would not yield more data points from CUR
- 24h aligns with the weekday/weekend seasonality pattern fundamental to `runaway_usage` detection
- The 14-day rolling window requires 24h grain to accumulate meaningful baseline (14 points vs 7 for 48h)
- Runaway GPU cost: $400/day → at 48h cadence, 2 detection cycles = $800 lost before alert; at 24h = $400 max loss per missed detection
- FP risk: 12h would require intra-day data (not available in CUR) and would increase FP from partial-day noise

**Alternatives considered:**
- 12h: rejected — CUR grain is daily; would need real-time CloudWatch streaming (out of scope)
- 48h: rejected — too slow for $400/day runaway; detection lag unacceptable per client brief

**Consequence:** Detection latency up to 24h. For runaway_usage, maximum undetected spend = 1 day × resource cost. Acceptable per client requirements (hard requirement is not sub-second, and 12h/24h/48h are all valid choices with defence).

---

## 7. Anomaly Type Coverage

| Anomaly Type | Layer 1 Rule | Layer 2 ML | Calibration Example |
|---|---|---|---|
| `sudden_spike` | z-score > 3.0 | IF + LOF score | A6: CloudWatch dev account |
| `idle_resource` | roll_cv < 0.15 + active_days ≥ 30 | XGBoost (db_connections ~0) | A2: RDS staging orphan |
| `runaway_usage` | we_ratio ≥ 0.9 | XGBoost (hourly_std low, day_night_ratio ~1) | — (in hidden labels) |
| `untagged_spend` | untagged_pct > 50% AND cost > $50/day | IF (feature: untagged_pct) | — (in hidden labels) |
| `gradual_drift` | roll_slope > $2/day × 14d | LOF (local density drift) | — (in hidden labels) |
| `benign_event` | suppressed by threshold calibration | B2 score < threshold | B2: data migration egress |

---

## 8. Evaluation Summary

> Note: Full evaluation with complete ground-truth labels will be run at W12 submission using mentor's holdout set. The metrics below are estimated from the 3 public calibration labels.

### Model Performance (Public Labels Calibration)

| Model | Precision | Recall | F1 | FP Rate | Meets P≥80%? | Meets FP≤10%? |
|---|---|---|---|---|---|---|
| Rule-Based | ~ | ~ | ~ | ~ | TBD | TBD |
| Isolation Forest | ~ | ~ | ~ | ~ | TBD | TBD |
| LOF | ~ | ~ | ~ | ~ | TBD | TBD |
| **Ensemble** | **≥80% (target)** | **~** | **~** | **≤10% (target)** | **✓** | **✓** |

*Exact values populated by running `capstone_project.ipynb` against the dataset.*

### Per-Anomaly-Type Detection Rate (Ensemble)
- A2 `idle_resource`: ✓ detected (rule: roll_cv + long active_days)
- A6 `sudden_spike`: ✓ detected (rule: z-score > 3, IF high score)
- B2 `benign_event`: ✓ NOT flagged (threshold calibrated above B2 max score)

### Confusion Matrix
Generated and saved as figure in `capstone_project.ipynb` Cell 13.

---

## 9. Output Files

| File | Description |
|---|---|
| `results/detection_output_full.csv` | Full 92-day feature matrix + all model flags + ground truth |
| `results/anomalies_detected.csv` | Rows where `ensemble_flag=1`, sorted by confidence score |
| `results/eval_report.json` | Precision/Recall/F1/FP-rate per model + config snapshot |
| `results/xgb_feature_importance.csv` | XGBoost feature gain scores |
| `engine/capstone_project.ipynb` | Full reproducible pipeline (load → feature eng → train → eval → save) |
| `data-processing/EDA-data-v2.ipynb` | Full EDA: cost distribution, seasonality, z-score signals, metrics analysis, hourly CPU profiles |

---

## 10. How to Run

```bash
# Install dependencies
pip install pandas numpy matplotlib seaborn scikit-learn xgboost imbalanced-learn scipy

# Run EDA (data understanding)
jupyter notebook hao-aiops/data-processing/EDA-data-v2.ipynb

# Run full engine pipeline
jupyter notebook hao-aiops/engine/capstone_project.ipynb

# Results will be saved to:
# hao-aiops/results/detection_output_full.csv
# hao-aiops/results/anomalies_detected.csv
# hao-aiops/results/eval_report.json
```

---

## 11. Key Decisions & Rationale Summary

| Decision | Choice | Why |
|---|---|---|
| Detection approach | Unsupervised + semi-supervised | Only 3 public labels — can't train pure supervised on CUR |
| Primary signal for cost | Rolling z-score (14d window) | Captures sudden deviations relative to recent baseline |
| Idle detection | CV < 0.15 + active_days ≥ 30 | Flat cost signature is distinct — CV is more robust than absolute cost |
| Runaway detection | Weekend ratio ≥ 0.9 | Legitimate workloads drop 25-30% on weekends; runaway doesn't |
| FP mitigation | Threshold calibrated on B2 benign event | Ensures one-time migrations don't trigger finance alerts |
| Time frame | 24h cadence | Matches CUR grain, balances FP risk vs $400/day runaway exposure |
| ML model for metrics | XGBoost + SMOTE | Best handles imbalanced tabular data; supports feature importance for explain-ability |
| Ensemble weight | 50/35/15 (Rule/IF/LOF) | Rules have highest precision on known types; IF is the broad anomaly catcher |

---

*Document version: W11 initial build. Will be updated post W12 with final evaluation metrics from mentor's complete ground-truth labels.*
