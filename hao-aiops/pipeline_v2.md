## Pipeline Architecture

╔══════════════════════════════════════════════════════════════════════════════╗
║           FINOPS WATCH — ANOMALY DETECTION PIPELINE v2                       ║
║           Hybrid Statistical + Supervised ML, 24h-cadence                    ║
╚══════════════════════════════════════════════════════════════════════════════╝

LAYER 0 — DATA INGESTION
┌─────────────────────────────────────────────────────────────┐
│  cost_explorer_daily.csv  ──┐                               │
│  cur_line_items.csv        ─┼──► Raw DataFrames             │
│  metrics.csv               ─┘    (92 days training)         │
│                                                             │
│  Tech: pandas read_csv, parse_dates, low_memory             │
│  Fix:  is_estimated flag aware (CUR 2-day lag)              │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
LAYER 1 — FEATURE ENGINEERING (dynamics-only, no identity)
┌──────────────────────────────────────────────────────────────┐
│  daily_metrics_agg()    → met_cpu/mem/net/disk/gpu (mean+max)│
│  cur_daily_agg()        → top_resource_share, n_resources,   │
│                           max_resource_cost, untagged_share  │
│                                                              │
│  Per-(account, service) group rolling stats:                 │
│   • roll_mean/std_{3,7,14,30d}   — multi-scale baseline      │
│   • lag_1/lag_7                  — temporal shift            │
│   • EWMA deviation (span=7)      — exponential smoothing     │
│   • robust z-score (median/MAD)  — outlier-resistant z       │
│   • CUSUM positive-only          — sustained upward drift    │
│   • STL residual (period=7)      — weekly seasonality removal│
│   • causal_pct_rank (30d window) — rank in recent history    │
│   • cost_share_of_account (ratio)— relative importance       │
│   • coherence_cost_cpu/net       — cost vs metric mismatch   │
│   • idle_signal (cpu<5%, cost>0) — zombie resource           │
│                                                              │
│  Cold-start fix: lag_1=NaN → lag_1=0 + is_first_seen flag    │
│  Global median imputation only (no per-group → no ID leakage)│
│                                                              │
│  OUTPUT: 45 dynamic features, ZERO identity features         │
│  Tech: pandas, statsmodels.STL, numpy                        │
└──────────────────────────────────────────────────────────────┘
                          │
                          ▼
LAYER 2 — UNSUPERVISED ANOMALY ENSEMBLE (label-free scoring)
┌─────────────────────────────────────────────────────────────┐
│  robust_z.clip(lower=0)                                     │
│  + ewma_dev.clip(lower=0)          ──► normalize each       │
│  + stl_residual.clip(lower=0)          → z/std              │
│  + cusum_pos                        ──► mean = spike_score  │
│  + idle_signal (binary)             ──► unsup_ensemble_score│
│  + emergence_signal / 50                                    │
│                                                             │
│  Key fix: clip(lower=0) not abs() — cost DROP ≠ anomaly     │
│  Tech: numpy, pandas, custom CUSUM loop                     │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
LAYER 3 — WEAK / PU LABEL CONSTRUCTION (3 known labels only)
┌──────────────────────────────────────────────────────────────┐
│  y_strict = match(account AND service AND date window)       │
│  flat_day_filter: y_strict=1 but score<median → drop         │
│                                                              │
│  pseudo_pos = top 1.5% unsup_score, not y_strict, not B2     │
│  B2 (known benign) → sample_weight = 3.0 hard negative       │
│  pseudo_pos        → sample_weight = 0.4                     │
│                                                              │
│  Fix vs OLD: label match was account-only → 320 wrong rows   │
│  Tech: Positive-Unlabeled learning (PU), sample weighting    │
└──────────────────────────────────────────────────────────────┘
                          │
                          ▼
LAYER 4 — XGBoost SUPERVISED RANKER (weak labels)
┌─────────────────────────────────────────────────────────────┐
│  XGBClassifier(                                             │
│    n_estimators=120, max_depth=3,      ← shallow: 3 labels  │
│    learning_rate=0.06,                                      │
│    reg_lambda=2.0, min_child_weight=5, ← heavy regularize   │
│    scale_pos_weight=neg/pos            ← class imbalance    │
│  ).fit(X, y_weak, sample_weight=w)                          │
│                                                             │
│  Learns COMBINATIONS of statistical signals that            │
│  the flat ensemble average cannot capture                   │
│  Tech: xgboost, shap (explainability)                       │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
LAYER 5 — SCORE BLENDING + THRESHOLD CALIBRATION
┌─────────────────────────────────────────────────────────────┐
│  final_score = 0.5 × XGB_proba + 0.5 × norm(unsup_score)    │
│                                                             │
│  HARD_THRESHOLD = max(anomaly_scores) ≤ B2_score            │
│  SOFT_THRESHOLD = HARD_THRESHOLD × 0.55                     │
│                                                             │
│  Design: B2 acts as calibration anchor (guard-rail first)   │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
LAYER 6 — DAILY-CADENCE STREAMING SIMULATION (production sim)
┌─────────────────────────────────────────────────────────────┐
│  For each day d in test period:                             │
│    cost_slice = all data ≤ d (+ 30d warm-up history)        │
│    → engineer_features() → unsup_score() → XGB.predict()    │
│    → blend → score[d]                                       │
│                                                             │
│  No future leakage. 30-day warm-up prevents cold-start      │
│  at file boundary (real prod requirement).                  │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
LAYER 7 — PERSISTENCE FILTER (decision policy layer)
┌─────────────────────────────────────────────────────────────┐
│  score[d] ≥ SOFT_THRESHOLD → soft_flag = 1                  │
│  consecutive_soft_days ≥ N (default N=3) → ALERT FIRES      │
│                                                             │
│  Eliminates single-day noise FPs without raising threshold  │
│  N=2: faster detection, more FP                             │
│  N=3: balanced (v2 clears TF2 gate, v1 FPR=0.42%)           │
│  N=4: too slow, recall drops with no FPR gain               │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
LAYER 8 — EVALUATION
┌─────────────────────────────────────────────────────────────┐
│  evaluate_event_detection():                                │
│    Per-label: detected? delay_days? score?                  │
│    Row-level: TP/FP/FN/TN → Precision/Recall/FPR            │
│    FP_on_known_benign (B2 must always = 0)                  │
│                                                             │
│  Task 13 (NEW): Training-set backtest — 9 outputs           │
│  Task 10-11:    Hold-out test v1 (June) + v2 (July)         │
│  Sweep: min_consecutive ∈ {1,2,3,4} — trade-off table       │
└─────────────────────────────────────────────────────────────┘

RESULTS SUMMARY:
  Train (in-sample, 3 labels) → Task 13
  Test v1 (June):  P=50%  R=50%  FPR=0.42%  ✅FPR  ❌P
  Test v2 (July):  P=100% R=33%  FPR=0.00%  ✅both gates
