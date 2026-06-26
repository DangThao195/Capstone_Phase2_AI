# Cost Analysis - Task force <N> · CDO <M>

<!-- Doc owner: <Nhóm CDO>
     Status: Skeleton (W11 T6 Pack #1) → Measured actual (W12 T4 Pack #2)
     Word target: 800-1500 từ -->

## 1. Cost model per tenant (forecast)

| Component | Unit cost | Tenant avg usage | $/tenant/month |
|---|---|---|---|
| Compute (Fargate/Lambda) | $X/hr | Y hr | $Z |
| Database (RDS/DynamoDB) | $X/GB-month | Y GB | $Z |
| Storage (S3) | $X/GB-month | Y GB | $Z |
| Data transfer | $X/GB | Y GB | $Z |
| AI inference (Bedrock) | $X/call | Y calls | $Z |
| Observability | $X/log GB | Y GB | $Z |
| **Total / tenant / month** | | | **$N** |

## 2. Cost at scale

| Tenant count | Monthly total cost | Avg per-tenant |
|---|---|---|
| 10 | $X | $N |
| 50 | $X | $N (economies of scale?) |
| 200 | $X | $N |

*Lưu ý: per-tenant cost giảm dần do shared fixed cost amortize.*

## 3. Cost optimization applied

- ☐ Spot instances cho non-critical workload (~70% saving)
- ☐ Reserved capacity cho baseline
- ☐ S3 lifecycle tiering (Standard → IA → Glacier)
- ☐ DynamoDB on-demand vs provisioned
- ☐ Bedrock prompt caching (Anthropic prompt cache)
- ☐ Right-sizing per ECS task/Lambda memory
- ☐ Log retention tiering
- ☐ Data transfer optimization (VPC endpoints to avoid NAT)

## 4. Cost vs alternatives (cùng task force)

| Angle | $/tenant/month forecast | Why diff |
|---|---|---|
| Mine: <angle> | $N | <lý do> |
| Nhóm khác A: <angle> | $X | <lý do> |
| Nhóm khác B: <angle> | $Y | <lý do> |

## 5. Measured actual (Pack #2 only - fill in W12)

### 5.1 2-week capstone spend

| Service | Forecast | Actual | Delta |
|---|---|---|---|
| Compute | $X | $X | ±X% |
| Database | $X | $X | ±X% |
| Storage | $X | $X | ±X% |
| AI inference | $X | $X | ±X% |
| Observability | $X | $X | ±X% |
| **Total** | $X | $X | ±X% |

### 5.2 Per-tenant actual

<!-- Sau khi onboard ≥3 tenant test, measure real consumption -->

| Tenant test | Service mix | $/day | Extrapolate $/month |
|---|---|---|---|
| Tenant-1 | small load | $X | $X |
| Tenant-2 | medium load | $X | $X |
| Tenant-3 | enterprise load | $X | $X |

### 5.3 Cost-per-correct-decision (joint with AI eval)

| Metric | Value |
|---|---|
| Total AI calls in capstone | N |
| Correct decisions | M |
| Total AI cost | $X |
| **Cost per correct decision** | **$X / M** |

## 6. Cost guardrails

- Monthly budget alert at 70%, 90%, 100%
- Per-tenant quota enforced via API rate limit
- Bedrock daily spend cap (CloudWatch alarm)

## 7. Cost recommendations for production

- Reserved capacity sau 3 tháng usage baseline
- Savings Plan cho Fargate
- Cross-region replication chỉ enable cho enterprise tier

## Related documents

- [`02_infra_design.md`](02_infra_design.md) - Infra design drives compute/storage cost
- [`../../ai/docs/03_ai_engine_spec.md`](../../ai/docs/03_ai_engine_spec.md) §8 - AI inference cost feeds row "AI inference" trong §1 doc này
- [`07_test_eval_report.md`](07_test_eval_report.md) - Load test results validate cost assumptions
