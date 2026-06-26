# Task Force 1 - Triage Hub

> Tài liệu giúp HV hiểu đề tài: Client là ai, vấn đề gì, cần build gì, output cuối ra sao.
> Đọc trước T2 W11. KHÔNG phải đáp án Phase 1 Discovery - những gì cần clarify với Client thực sự thì sáng T2 vào phòng họp mới biết.

---

## Bối cảnh

Client là **CTO của một SaaS startup B2B**, ~20.000 user active, ~50 microservice production. On-call team gồm **8 engineer** đang burnout nặng. Board hỏi mỗi quý "MTTR sao tăng?" mà CTO không trả lời được vì không có data đo lường.

## Vấn đề

Mỗi tuần on-call nhận **~50+ alert**. Mỗi alert tốn 30-60 phút từ lúc ping Slack đến lúc xác định được nguyên nhân. 80% công việc là dig log, query metric, mở Jira ticket viết tay, ping team owner. Repetitive, không thinking-heavy nhưng tốn người. Engineer kiệt sức, MTTR tăng dần.

## Client muốn gì

Build **Triage Hub**: khi alert fire, system tự động:

1. Gather context - logs + metrics + recent deploys + alert metadata.
2. Gọi AI diagnose root cause + đề xuất remediation.
3. Tạo Jira ticket có structure đầy đủ.
4. Ping team owner qua Slack với 1-click acknowledge.

Engineer chỉ **confirm + act**, không dig from zero. Đặc biệt: **KHÔNG auto-remediation** - AI chỉ diagnose + suggest, human-in-the-loop luôn giữ.

## Hard requirements (Client đã chốt)

- **MTTA giảm ≥50%**, **MTTR giảm ≥30%** measured trên test scenarios.
- **3 incident scenario E2E**: high-severity (critical service down) + medium-severity (latency degradation) + ambiguous signal (noisy alert / false alarm).
- AI suggestion actionable - có command/steps cụ thể, không generic.
- Context isolation per-tenant (không leak cross-tenant).
- Confidence score correlate với accuracy (low confidence → INVESTIGATE thay vì guess).
- Audit trail mọi AI decision link ticket field, traceability đầy đủ.

## Out of scope (Client confirm KHÔNG làm)

- Auto-remediation (chỉ diagnose + suggest).
- Multi-region (single-region us-east-1).
- ServiceNow integration build (Jira-first; ServiceNow design-only).
- PagerDuty paging integration (Slack đủ).
- Historical ticket migration / backfill (forward-process new ticket only).
- Auto-retrain pipeline (collect feedback + design retrain trigger OK, build pipeline thì không).
- Real production data (synthetic / sanitized only).
- Custom UI / dashboard build (Jira + Slack interactive đủ).
- Cost forecasting / capacity prediction / self-heal (đề tài của TF2/TF3/TF4).
- GDPR Article 17 erasure API build (design-only).

## Cần build (deliverable cấp cao)

**Nhóm AI**:
- AI Engine implement scenario E2E: alert → context aggregation → diagnose → ticket payload → notify.
- 3 contracts ký với CDO: Telemetry (signals platform emit), AI API (endpoint CDO call), Deployment (engine sống ở đâu).
- Eval report: precision/recall/latency trên test set.

**Nhóm CDO**:
- Platform infra hosting AI engine theo angle riêng (serverless-first hoặc streaming-first hoặc khác).
- IaC + CI/CD + observability + integrate AI endpoint.
- E2E test per platform: signal → AI → ticket → notify chạy thật.

## Output phải có cuối W12

- AI engine deployed trên cả 2 CDO platform trong task force.
- Eval report (precision, recall, F1, latency) trên 3 incident scenarios + ≥5-10 test cases.
- Demo video E2E 3 scenarios chạy thật từ alert → ticket → notify.
- 3 contracts ký, FREEZE từ T5 W11.
- Slides + individual pitches.
- Curveball responses (3 lần inject scope change).
- Eval evidence: MTTA/MTTR before-after data trên consistent test scenarios.

## Cần clarify với Client (Phase 1 Discovery T2-T3)

Đây là **danh mục** câu hỏi cần đặt ra trong phòng họp - đừng xài checklist 10 câu y nguyên, mỗi team phải tự sinh câu hỏi specific dựa trên context. Một số mảng phải đào:

- **Tool stack hiện tại**: ticketing (Jira/ServiceNow/khác), chat (Slack/Teams), alert source (CloudWatch/Prometheus/custom), log storage, metric storage.
- **Compliance + data**: PII trong alert payload, retention policy, SOC2 controls, data residency.
- **Failure mode**: Bedrock down, Slack down, Jira down - system fallback thế nào.
- **Scale + multi-tenant**: tenant count cho demo, burst handling (alert spike), audit storage cost.
- **Routing + on-call**: ai nhận escalation khi nào, team-owner mapping, severity tier.
- **Cost ceiling**: Bedrock invocation budget, AWS demo cap, production projection.
- **Eval data**: synthetic OK hay phải real, ai validate test set.
- **Curveball-readiness**: nếu Client thêm severity classify, region switch, schema change - system flex được không.

## Trước khi vào phòng họp T2 W11

Đọc kỹ "Bối cảnh + Vấn đề + Client muốn gì" để hiểu landscape. Đem theo 10-15 câu hỏi cụ thể (không phải "anh muốn gì"). Sau 90 phút interview, viết debrief "Tôi hiểu là..." gửi mentor confirm trước EOD T2 - tránh hiểu nhầm dẫn đến rework T3-T4.
