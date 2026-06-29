# FinOps Watch - Operating Flow

> Purpose: shared briefing for AIOps, DevOps, and CloudOps teams before W11-W12 delivery.
> This document explains the product flow, team responsibilities, integration points, milestones, and final outputs.

## 1. Product Goal

FinOps Watch is a continuous AWS cost monitoring and anomaly response system for a mid-size company running multi-account AWS.

The client problem is not "lack of dashboard only". The real problem is that Finance and Engineering discover abnormal spend too late. Last month, AWS cost spiked from about USD 180k to USD 420k, and one forgotten training cluster burned about USD 400/day for 18 days before anyone found it.

The product must help the client:

- ingest AWS cost data continuously by a defended cadence: 12h, 24h, or 48h;
- detect cost anomalies with measurable precision and false positive rate;
- route alerts to the right audience: Finance or Engineering;
- safely contain obvious non-prod patterns such as idle resources, mis-tagged spend, or runaway training;
- provide a Finance-readable dashboard and 3-month backtest evidence.

## 2. End-to-End Operating Flow

```text
[AWS Organizations (Multi-account AWS)]
                │
                ▼
  [CUR on S3 & Cost Explorer API]
                │
                ▼
[Scheduled Ingestion Pipeline (EventBridge 12h/24h/48h)]
                │
                ▼
[CDO Data Ingestion & Enrichment Pipeline]
(Chuẩn hóa schema, khử trùng lặp qua Idempotency Key, bổ sung tags/owners)
                │
                ▼
   [Cost Data Store (S3, Athena, Warehouse)]
                │
                ▼
   [AIOps AI Engine (POST /v1/finops/detect)]
                │
                ├─► [Phân loại chi phí / Phân tuyến Alert]
                │         ├─► Chi phí bình thường (Normal Spend)
                │         │         └─► Cập nhật Dashboard (Spend Trend & Baseline)
                │         │
                │         └─► Phát hiện bất thường (Anomaly Detected)
                │                   └─► Phân tuyến Alert:
                │                             ├─► Kênh Finance: Cảnh báo tác động chi phí & Spend Trend
                │                             └─► Kênh Engineering: Cảnh báo chi tiết (Owner, Service, Actions)
                │
                └─► [Quyết định ngăn chặn / Safe Containment Decision]
                          ├─► Tài nguyên Production (Prod)
                          │         └─► Chỉ đề xuất gợi ý / tag-for-review / dry-run (Tuyệt đối không can thiệp tự động)
                          │
                          └─► Tài nguyên non-Prod (Dev/Sandbox)
                                    └─► Kích hoạt Dry-run containment
                                              │
                                              ▼
                                       [Dry-run Passed?]
                                              ├─► No  ──► Chuyển tiếp duyệt thủ công (Escalate human approval)
                                              └─► Yes ──► Thực thi Safe Action (Shutdown / Cap Quotas)
                                                            │
                                                            ▼
                                                     [Verify post-action]
                                                            │
                                                            ▼
                                                     [Audit Trail Log] (Lưu trữ >= 90 ngày cho SOC2)
                                                            │
                                                            ▼
                                                 [Finance-friendly Dashboard]
```

## 3. Data and Decision Flow

1. CDO platform pulls CUR data from S3 and Cost Explorer API on the agreed cadence.
2. Pipeline validates schema, deduplicates by idempotency key, and enriches records with account, service, environment, tag, owner, and cost period.
3. Normalized cost records are stored for dashboarding, AI detection, and backtest.
4. AIOps AI Engine analyzes the cost window against baseline and anomaly rules/model.
5. Engine returns:
   - anomaly status;
   - anomaly type;
   - severity;
   - confidence;
   - reasoning;
   - alert route;
   - suggested containment action;
   - audit id.
6. CDO platform routes alerts to Finance or Engineering.
7. If the pattern is safe and in scope, containment runs through dry-run first.
8. Every action writes an audit trail with retention of at least 90 days.

## 4. Recommended Cadence Decision

Recommended default: 24h.

Reasoning:

- 12h catches spend faster but increases false positive risk because cost data can lag.
- 48h is more stable but too slow for runaway cost such as USD 400/day training clusters.
- 24h is a defensible balance between detection speed, data freshness, and false positive control.

This decision must be captured in ADR because the client explicitly requires the team to choose and defend the time frame goal.

## 5. Team Responsibilities

### AIOps Team

Owns the product problem and intelligence layer.

- Clarify client requirements and define success metrics.
- Own AI Engine design and implementation.
- Draft and negotiate the 3 contracts with DevOps and CloudOps.
- Implement anomaly detection, confidence scoring, alert routing logic, and safe containment decisioning.
- Build 3-month backtest report with precision, recall, F1, false positive rate, confusion matrix, and per-anomaly-type breakdown.
- Deploy engine skeleton by W11 T5/T6 so both CDO platforms have a real endpoint.
- Keep API schema stable after contract freeze.
- Present AI design, evaluation evidence, safety guardrails, and recommendation between the two CDO platforms.

### DevOps Team / CDO Platform 1

Owns one platform implementation angle, for example serverless scheduled batch.

- Build EventBridge scheduled pull for CUR and Cost Explorer API.
- Build data landing, validation, transformation, and idempotency handling.
- Integrate with AI API contract.
- Build Finance-friendly dashboard and alert routing.
- Provide IaC, CI/CD, observability, rollback, and test evidence.
- Demonstrate the platform differentiation clearly in the final pitch.

### CloudOps Team / CDO Platform 2

Owns a competing platform implementation angle, for example warehouse-centric, container-based, or lakehouse-centric design.

- Consume the same AI contracts.
- Build an alternative reliable platform for ingestion, storage, dashboarding, and AI integration.
- Focus on operational quality: security baseline, multi-account access, auditability, cost control, and failure handling.
- Provide E2E test, chaos response, SLO evidence, and cost analysis.
- Explain why this platform is stronger for a specific production operating model.

## 6. Contract Boundaries

The 3 contracts are the most important integration artifacts. They are frozen after W11 T5 approval.

### Telemetry Contract

For TF2, this must be adapted from generic telemetry to FinOps cost data.

The contract should define:

- data source: CUR on S3 and Cost Explorer API;
- pull cadence;
- cost record schema;
- required fields: tenant_id, account_id, service, region, cost_usd, usage_type, tag map, environment, owner, cost_period_start, cost_period_end;
- idempotency key format;
- freshness SLA;
- retention;
- malformed record handling;
- dead-letter handling.

### AI API Contract

Recommended endpoint:

```http
POST /v1/finops/detect
```

Request should include:

- tenant_id;
- cost window;
- baseline window metadata;
- account/service/tag dimensions;
- detection cadence;
- optional containment policy.

Response should include:

- anomaly;
- anomaly_type;
- severity;
- confidence;
- reasoning;
- finance_summary;
- engineering_summary;
- alert_route;
- suggested_action;
- dry_run_required;
- audit_id.

### Deployment Contract

Defines how the shared AI Engine is deployed and consumed by both CDO platforms.

Key points:

- AI Engine is hosted once per task force.
- Both CDO platforms call the same endpoint.
- Tenant/platform isolation is enforced by tenant_id or platform_id.
- Endpoint URL and schema must not change after freeze.
- CDO can test with skeleton response before real AI logic is complete.

## 7. Safe Containment Scope

Hard boundaries:

- never terminate production resources;
- never delete data;
- never modify IAM;
- never auto-act on prod except tag/suggest/dry-run.

Recommended containment implementation:

- Implemented pattern: schedule shutdown or quota cap for runaway dev/sandbox training workload.
- Designed pattern 1: tag-for-review for mis-tagged spend.
- Designed pattern 2: idle resource recommendation for EC2/RDS/EBS/NAT/Load Balancer.

All containment patterns must support dry-run and write audit trail.

## 8. Milestones

```text
Tuần W11: Giai đoạn Thiết kế & Ký kết Contracts (22/06 - 26/06)
─────────────────────────────────────────────────────────────────────────────────
[T2 22/06] Kickoff & Bốc đề ──► Phỏng vấn Client ──► Gửi Debrief cho Mentor duyệt
[T3 23/06] Soạn Requirements & Solution Design (AIOps & CDO)
[T4 24/06] AIOps công bố Draft 3 Contracts (Telemetry, API, Deployment)
[T5 25/06] [Sáng] Co-design & đàm phán ──► [Chiều] KÝ FREEZE CONTRACTS & Deploy Skeleton
[T6 26/06] AI phát triển lõi logic ──► CDO dựng Base IaC (VPC, Cluster, DB, Pipeline)

Tuần W12: Giai đoạn Xây dựng & Tích hợp E2E (29/06 - 02/07)
─────────────────────────────────────────────────────────────────────────────────
[T2 29/06] AI: Safety Guard, Multi-tenant routing ──► CDO: CI/CD Canary, tích hợp Skeleton ──► 16h: Curveball #2 (Med)
[T3 30/06] AI: Eval Set & scenarios ──► CDO: E2E testing ──► 14h-16h: INTEGRATION SESSION (Real API)
[T4 01/07] Tối ưu E2E, chuẩn bị Slides & Video ──► 14h: Curveball #3 (Chaos) ──► Dry-run & fix bug
[T5 02/07] 08h00: CODE FREEZE (Git tag 'final') ──► PITCHING & DEFENSE PANEL (09h00 - 17h00)
```

## 9. Final Deliverables

By code freeze on 2026-07-02 08:00, the task force must have:

- deployed AI Engine integrated by both CDO platforms;
- E2E demo: synthetic anomaly injection -> detection -> alert -> containment dry-run/action -> audit log;
- 3-month backtest report;
- precision >= 80%;
- false positive rate <= 10%;
- confusion matrix and per-anomaly-type breakdown;
- at least 2 anomaly types caught;
- at least 1 containment pattern implemented;
- at least 2 containment patterns designed;
- Finance-readable dashboard;
- signed and frozen telemetry, AI API, and deployment contracts;
- ADR for cadence decision and other major architecture decisions;
- curveball responses;
- slides, demo video, and individual pitches;
- Jira evidence links for closed tasks.

## 10. Demo Script

Recommended final demo flow:

1. Show baseline spend dashboard.
2. Inject synthetic runaway training anomaly in dev/sandbox account.
3. Run scheduled or manual trigger for cost ingestion.
4. Show normalized cost record in data store.
5. Call AI Engine and show response with anomaly type, confidence, severity, and reasoning.
6. Show alert routed to Engineering and Finance with different message content.
7. Run containment dry-run.
8. Execute safe containment only if non-prod and policy allows.
9. Show audit trail with actor, before/after state, rollback path, and retention policy.
10. Show backtest metrics proving precision and false positive target.

## 11. Open Clarification Questions for Client

- How many AWS accounts are in scope for the demo?
- Which cost allocation tags are considered mandatory?
- What are the current Finance and Engineering escalation channels?
- Which environment labels are trusted: prod, staging, dev, sandbox?
- What exact approval rule is required before containment?
- What cost threshold defines runaway training?
- What dashboard tool does Finance prefer?
- What is the acceptable cost ceiling for running FinOps Watch itself?
- What additional actions are forbidden beyond terminate prod, delete data, and modify IAM?
- What audit format is expected for SOC2-style evidence?
