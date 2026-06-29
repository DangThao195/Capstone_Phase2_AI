# Task Force 2 - FinOps Watch

> Tài liệu giúp HV hiểu đề tài: Client là ai, vấn đề gì, cần build gì, output cuối ra sao.
> Đọc trước T2 W11. KHÔNG phải đáp án Phase 1 Discovery - những gì cần clarify với Client thực sự thì sáng T2 vào phòng họp mới biết.

---

## Bối cảnh

Client là **CFO của một công ty mid-size** đang chạy multi-account AWS (Organizations đã set up nhưng chưa tight), Engineering ~80 người chia 12 squad. Finance team có dashboard riêng, Engineering có console riêng - không bên nào nhìn cost continuous, chỉ weekly snapshot.

## Vấn đề

Tháng trước AWS bill spike **2.3×**, từ baseline ~$180k lên ~$420k trong một tháng. Finance team mất gần một tuần mới truy ra nguyên nhân: một dev quên tắt training cluster, đốt **$400/day suốt 18 ngày**. Đến khi phát hiện thì đã rớt mất ~$7k tiền oan.

## Client muốn gì

Build **FinOps Watch system** chạy continuous:

1. Ingest cost data theo cadence rõ ràng (CUR + Cost Explorer API).
2. Detect anomaly với precision + FP rate đo lường được.
3. Alert đúng người (Finance vs Engineering tách routing).
4. Với pattern obvious (idle resource, mis-tagged spend, runaway training) → auto-containment **SAFE** (tag-for-review, quota cap, schedule shutdown trên dev/sandbox).

Plus dashboard Finance-friendly + backtest evidence trên 3 tháng historical data. Client không quan tâm thuật toán cụ thể, nhưng quan tâm **guard rail**.

## Hard requirements (Client đã chốt)

- **Precision ≥80%, FP ≤10%** trên backtest 3 tháng.
- **Time frame goal**: team chọn 12h / 24h / 48h + defend trade-off (data freshness vs detection speed vs FP risk) - không có "đáp án đúng", team defend lý do.
- **NEVER terminate prod, NEVER delete data, NEVER modify IAM** - 3 boundary cứng.
- ≥1 containment pattern implemented + ≥2 designed (dry-run mode mandatory cho tất cả).
- Dashboard Finance-friendly - không full technical dump, Finance phải đọc được không cần SQL.
- Audit trail mọi containment action: actor, before/after state, rollback path. Retention ≥90 days.

## Out of scope (Client confirm KHÔNG làm)

- Multi-cloud (AWS only).
- Forecasting future cost (3-month prediction, budget planning).
- RI/SP recommendation engine (chỉ right-sizing suggest).
- Auto-trade RI/SP.
- Integration với CloudHealth/Apptio/Vantage.
- Auto-act trên prod resource (chỉ tag/suggest/dry-run trên prod).
- Auto IAM/security modification (never touch).
- Cost showback/chargeback billing.
- Multi-currency (USD only).
- Real-time streaming detection sub-second (cadence 12h/24h/48h).
- Self-service tenant onboarding UI.
- Multi-region active-active (single-region demo, DR design-only).
- Real AWS bill access (synthetic-only, mentor cung cấp seed).
- Auto-retrain pipeline (design-only OK).

## Cần build (deliverable cấp cao)

**Nhóm AI**:
- AI Engine: data ingestion → anomaly detect → confidence score → alert routing.
- 3 contracts ký với CDO. Lưu ý: telemetry contract của TF2 khác default - data source là CUR (S3) + Cost Explorer API, CDO **PULL** theo cadence chứ không emit.
- Backtest report đầy đủ: precision/recall/F1, confusion matrix, per-anomaly-type breakdown.

**Nhóm CDO**:
- Platform hosting AI engine theo angle riêng (data warehouse-centric, streaming + ML, hoặc khác).
- Scheduled batch pattern (EventBridge cron) thay vì request-response.
- Idempotency key tránh double-run cùng cost period.
- Dashboard Finance-friendly + alert routing 2 channel.

## Output phải có cuối W12

- AI engine deployed trên cả 2 CDO platform.
- Backtest report: precision/recall/F1, confusion matrix, ≥2 anomaly type caught.
- Demo E2E: synthetic anomaly inject → detect → alert → containment action triggered.
- 1+ containment implemented + 2+ designed (dry-run path documented).
- Dashboard Finance-readable với spend trend + anomaly overlay + confidence visual.
- 3 contracts ký, FREEZE từ T5 W11.
- ADR cho time frame goal (12h/24h/48h) + lý do chốt.
- Slides + individual pitches + curveball responses.

## Cần clarify với Client (Phase 1 Discovery T2-T3)

Đây là **danh mục** câu hỏi cần đặt ra trong phòng họp - đừng xài checklist y nguyên. Các mảng phải đào:

- **AWS landscape**: account count, Organizations setup, CUR data có active chưa, Cost Explorer API access.
- **Cost data access**: real bill hay synthetic, lag thế nào, retention.
- **Tagging strategy**: cost allocation tags hiện có, gaps đâu.
- **Anomaly definition**: threshold % deviation, time window, per-service vs global.
- **Routing + escalation**: Finance vs Engineering ranh giới, ai approve containment.
- **Safe containment scope**: NEVER list (terminate prod, delete data, modify IAM đã chốt) - còn gì NEVER?
- **Dashboard audience**: Finance Ops dùng UI gì, working hours, có quen Athena/SQL không.
- **Failure mode**: Cost Explorer rate limit, CUR delay, pipeline down - handle thế nào.
- **Compliance**: SOC2 controls, retention, audit format.
- **Cost ceiling**: budget cho FinOps Watch system tự nó (eat own dog food).
- **Multi-tenant scope**: tenant định nghĩa thế nào trong demo, isolation hard requirement gì.

## Trước khi vào phòng họp T2 W11

Đọc kỹ "Bối cảnh + Vấn đề + Client muốn gì" để hiểu landscape. Đem theo 10-15 câu hỏi cụ thể. Sau 90 phút interview, viết debrief "Tôi hiểu là..." gửi mentor confirm trước EOD T2 - tránh hiểu nhầm dẫn đến rework T3-T4.
