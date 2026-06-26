# Task Force 3 - Self-Heal Engine

> Tài liệu giúp HV hiểu đề tài: Client là ai, vấn đề gì, cần build gì, output cuối ra sao.
> Đọc trước T2 W11. KHÔNG phải đáp án Phase 1 Discovery - những gì cần clarify với Client thực sự thì sáng T2 vào phòng họp mới biết.

---

## Bối cảnh

Client là **VP Engineering của một SaaS platform B2B** vận hành 200+ microservice trên Kubernetes (EKS multi-AZ, single region us-east-1). Production traffic peak ~8K RPS, ~120 paying tenant enterprise contract, data layer ~12TB live state (RDS Aurora + DynamoDB). On-call rotation 6 engineer chia 2 tier (primary + secondary).

## Vấn đề

Mỗi đêm on-call nhận 2-4 page, **80% là known patterns** xuất hiện hàng chục lần:

- Pod OOMKilled → adjust memory limit.
- Service stuck → restart deployment.
- Queue backlog → scale worker.
- Cert expiring → rotate secret.

Engineer thức dậy 2h sáng chỉ để click "restart". Burnout đo được: eNPS rớt từ 42 xuống 11 trong 12 tháng, retention rớt **30% YoY**, time-to-hire on-call SRE mới 14 tuần (chậm hơn tốc độ nghỉ).

## Client muốn gì

Build **Self-Heal Engine** tự động hoá 80% known patterns. Pipeline:

```
detect → match runbook → execute (audited) → verify → escalate nếu fail
```

Action trên cluster phải có **audit trail tamper-evident** (Compliance ép commit SOC2 Type II re-cert tháng 9). Phải có **dry-run + rollback + blast-radius config explicit**. Khi escalate cho engineer, message phải kèm full context bundle (logs, metrics, deploy history, attempts đã thử) - engineer xử lý ngay, không tự đào từ zero.

Không phải research project - artefact demo trên sandbox cluster cuối W12, evidence rõ ràng.

## Hard requirements (Client đã chốt)

- **≥3 known patterns implemented + tested + ≥2 designed** (paper playbook + diagram).
- **Auto-resolve rate ≥60%** trên **≥10 scenarios** injected.
- **Scenario simulation ≥4h test window** (KHÔNG yêu cầu 1-week real observation).
- **Zero unsafe action** trong sandbox (no namespace-prod delete, no IAM modify).
- Audit log tamper-evident (S3 Object Lock hoặc append-only DB), retention ≥90 days.
- 5 safety sub-checkpoint mandatory: dry-run · blast-radius · verify post-act · auto rollback · circuit breaker.
- Multi-tenant ≥2 tenants với RBAC isolation.
- Escalation message AI-generated với context bundle đầy đủ.

## Out of scope (Client confirm KHÔNG làm)

- Multi-cluster federation (single cluster sandbox only).
- Auto-discover new pattern (HV define rule explicit).
- Cost-aware routing (chuyển sang TF2).
- Cross-service root cause analysis (engine match pattern, không chain analysis).
- Production traffic (sandbox + synthetic workload only).
- 1-week real observation (scenario simulation ≥4h đủ).
- Real PagerDuty/OpsGenie contract (Slack webhook đủ).
- GitOps full integration (pre-state snapshot trong audit log đủ).
- Predictive lens (chuyển sang TF4).
- Auto-retrain ML model (engine rule-based hoặc hybrid, ML thì batch manual).
- Hash chain crypto signing audit (Object Lock đủ).
- Customer-facing notification (Customer Success owns, engine emit webhook đủ).
- mTLS internal endpoints (bearer token JWT đủ cho capstone).
- Cross-region audit log replication.

## Cần build (deliverable cấp cao)

**Nhóm AI**:
- AI Engine với **≥3 endpoints** (Đề 3 contract specifics):
  - `POST /v1/detect` - anomaly detection.
  - `POST /v1/decide` - match anomaly với runbook → trả action plan + blast-radius check.
  - `POST /v1/verify` - post-action metric check, trả `success / regression / next_action`.
  - Optional `POST /v1/rollback`.
- Mandatory request fields: `idempotency_key`, `dry_run_mode`, `correlation_id`.
- 3 contracts ký với CDO. Deployment contract cần thêm: `kubeconfig` secret, K8s ServiceAccount + RBAC least-privilege, idempotency lock (DynamoDB conditional write hoặc Redis), audit storage encrypted + S3 Object Lock.

**Nhóm CDO**:
- Platform infra hosting engine theo angle riêng (K8s operator pattern hoặc Workflow orchestration hoặc khác).
- Sandbox EKS cluster + RBAC setup + audit log infra.
- E2E test: alert webhook → engine → action → verify → audit log queryable.

## Output phải có cuối W12

- AI engine deployed trên cả 2 CDO platform với 3+ endpoints chạy.
- 3 known patterns implemented + tested trên sandbox cluster.
- 2 patterns designed-only (paper playbook + diagram + ADR).
- Scenario simulation ≥4h: ≥10 scenarios, auto-resolve rate report.
- Audit log tamper-evident query được (Athena hoặc UI).
- Escalation demo: pattern không resolve được → engine bỏ cuộc → message AI-generated với context bundle gửi mock pager.
- 3 contracts ký, FREEZE từ T5 W11.
- 5 ADR cho key decisions (decision engine type, audit storage, runbook DSL, alert source, deployment topology).
- Slides + individual pitches + curveball responses.

## Cần clarify với Client (Phase 1 Discovery T2-T3)

Đây là **danh mục** câu hỏi cần đặt ra trong phòng họp. Mảng phải đào:

- **Cluster scope**: K8s version, node count, pod count, namespace structure.
- **Pattern priority**: "≥5 known patterns" - pick 5 nào trước, business impact thế nào.
- **Action semantics**: "auto-resolved" định nghĩa gì - execute success hay metric returns normal.
- **Blast radius**: max % cluster, max N pod, circuit breaker conditions.
- **Escalation policy**: 1 try / 3 tries / per-pattern, response SLA.
- **Audit requirement**: retention bao lâu, format gì, ai access, SOC2 control specific.
- **Sandbox spec**: K8s version, sample workload, ai inject incident.
- **Rollback method**: Git-based, snapshot revert, per-pattern khác nhau?
- **Decision engine**: bắt buộc dùng LLM không, rule-based hay hybrid.
- **Alert source**: Prometheus AlertManager, CloudWatch, Datadog, custom webhook.
- **API auth**: alert source → engine, engine → K8s.
- **Failure mode**: engine crash giữa action, fallback, recovery.
- **Latency budget**: detect → action → verify, mỗi bước bao nhiêu.

## Trước khi vào phòng họp T2 W11

Đọc kỹ "Bối cảnh + Vấn đề + Client muốn gì" để hiểu landscape. Đem theo 10-15 câu hỏi cụ thể. Sau 90 phút interview, viết debrief "Tôi hiểu là..." gửi mentor confirm trước EOD T2 - tránh hiểu nhầm dẫn đến rework T3-T4.
