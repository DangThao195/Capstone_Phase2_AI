# Eval Report - <Đề tài>

<!-- Doc owner: <Nhóm AI>
     Status: Skeleton (W11 T6 Pack #1) → Full results (W12 T4 Pack #2)
     Word target: 1000-1800 từ -->

## 1. Test scenarios

<!-- ≥10 scenarios cover happy path + edge cases + adversarial -->

| # | Scenario | Type | Expected output |
|---|---|---|---|
| 1 | <vd: high-severity OOMKilled> | Happy | SCALE_UP suggestion |
| 2 | <vd: noisy alert spam> | Edge | ALERT_ONLY |
| 3 | <vd: ambiguous signal> | Adversarial | INVESTIGATE (refuse decide) |
| ... | ... | ... | ... |

## 2. Methodology

- **Setup**: <local sandbox / staging cluster>
- **Test data**: <source - synthetic / real-anonymized>
- **Run procedure**:
  1. Load eval set
  2. Invoke AI engine endpoint per scenario
  3. Compare output vs expected
  4. Record metric
- **Metrics measured**: precision · recall · F1 · P50/P99 latency · cost/call

## 3. Results

| Metric | Target | Actual | Pass/Fail |
|---|---|---|---|
| Precision | ≥ 0.8 | 0.XX | ✓/✗ |
| Recall | ≥ 0.7 | 0.XX | ✓/✗ |
| F1 | ≥ 0.75 | 0.XX | ✓/✗ |
| P50 latency | < Xms | Xms | ✓/✗ |
| P99 latency | < Xms | Xms | ✓/✗ |
| Cost per correct | < $X | $X | ✓/✗ |

### 3.1 Confusion matrix

```
                Predicted
              | Anomaly | Normal
Actual ─────┼─────────┼────────
   Anomaly   |   TP    |   FN
   Normal    |   FP    |   TN
```

| | Predicted Anomaly | Predicted Normal |
|---|---|---|
| Actual Anomaly | <TP> | <FN> |
| Actual Normal | <FP> | <TN> |

## 4. Failure analysis

<!-- Scenarios fail → root cause → fix attempted → result -->

### 4.1 Failure case 1: <description>

- **Expected**: ...
- **Got**: ...
- **Root cause**: ...
- **Fix**: <prompt tweak / threshold change / schema update>
- **Result after fix**: <pass / partial / still fail>

### 4.2 Failure case 2: ...

## 5. Curveball impact

<!-- 3 curveball - pass/fail mỗi cái + lessons learned -->

| Curveball | Tier | Response | Outcome | Lesson |
|---|---|---|---|---|
| #1 small (T5 W11) | Small | <how engine adapted> | Pass/Partial/Fail | ... |
| #2 medium (T2 W12) | Medium | ... | ... | ... |
| #3 chaos (T4 W12) | Chaos | ... | ... | ... |

## 6. Cost vs forecast

| Phase | Forecast | Actual | Delta |
|---|---|---|---|
| Dev (W11) | $X | $X | ±X% |
| Testing | $X | $X | ±X% |
| Buổi chấm demo | $X | $X | ±X% |

## 7. Improvement next iteration

<!-- Top 3 gap + plan (cho post-capstone production roadmap) -->

1. **Gap**: <description> → **Plan**: ...
2. **Gap**: ... → **Plan**: ...
3. **Gap**: ... → **Plan**: ...
