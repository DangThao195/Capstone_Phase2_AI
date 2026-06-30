# Test Results — Redesigned Pipeline (data_test_june)
> Train: Mar–May 2026 (2026-03-01 → 2026-05-31)
> Test : 2026-06-01 → 2026-06-30

## Test Set Distribution
- Total rows : 960
- Anomaly    : 48 (5.0%)
- Normal     : 912 (95.0%)

## Aggregate Metrics
| Model | Precision | Recall | F1 | FPR | ROC-AUC | TF2 Gate |
|---|---|---|---|---|---|---|
| XGBoost (train thr) | 0.000 | 0.000 | 0.000 | 0.035 | 0.289 | ❌ FAIL |
| XGBoost (test-cal)  | 0.050 | 1.000 | 0.095 | 1.000 | 0.289 | ❌ FAIL |
| Isolation Forest    | 0.208 | 0.208 | 0.208 | 0.042 | 0.583 | ❌ FAIL |

## Train vs Test Comparison
| | Train XGB | Test XGB | Train IF | Test IF |
|---|---|---|---|---|
| Precision | 0.851 | 0.000 | 0.000 | 0.208 |
| Recall    | 1.000 | 0.000 | 0.000 | 0.208 |
| F1        | 0.919 | 0.000 | 0.000 | 0.208 |
| FPR       | 0.005 | 0.035 | 0.010 | 0.042 |

## Per-Event Detection Coverage
| Event | Type | Label | Window | Records | XGBoost | IsoForest |
|---|---|---|---|---|---|---|
| T1 | `scheduled_backup` | `benign` | 2026-06-02→2026-06-05 | 16 | ⚠️ FP 1/16 | ✅ TN suppressed |
| T2 | `db_saturation` | `anomaly` | 2026-06-07→2026-06-14 | 48 | ❌ Missed (0/48) | ⚠️ Weak (10/48) |
| T3 | `scheduled_backup` | `benign` | 2026-06-17→2026-06-24 | 48 | ✅ TN suppressed | ⚠️ FP 2/48 |

## Notes
- Features: 44 train features, 44 matched in test
- Missing test features padded with 0: none
- Train threshold: 0.0706 (argmax F1 on val split)
- Test-calibrated threshold: 0.0005 (analysis only)
- Optuna best CV F1: 0.9689