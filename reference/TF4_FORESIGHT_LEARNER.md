# Task Force 4 - Foresight Lens

> Tài liệu giúp HV hiểu đề tài: Client là ai, vấn đề gì, cần build gì, output cuối ra sao.
> Đọc trước T2 W11. KHÔNG phải đáp án Phase 1 Discovery - những gì cần clarify với Client thực sự thì sáng T2 vào phòng họp mới biết.

> TF4 có 3 nhóm CDO (không phải 2 như TF1/TF2/TF3). 3 CDO compete cùng đề.

---

## Bối cảnh

Client là **Head of SRE tại một fintech mid-size**, ~3.5 triệu user active, ~2.8k RPS peak ngày thường, ~9k RPS peak Black Friday. Vận hành **~120 microservice production** (payment gateway, KYC, ledger, reporting, fraud detection) trên ECS Fargate + RDS Aurora MySQL + DynamoDB + SQS.

## Vấn đề

3 tháng vừa rồi miss SLO **7 lần liên tiếp** (target 99.9% monthly availability) - **không phải vì incident catastrophic**, mà vì **capacity exhaustion silent**:

- RDS CPU bò lên 100% trong 90 phút trước khi connection pool exhaust.
- Queue worker backlog âm thầm 6× rồi consumer timeout.
- ALB connection limit chạm trần lúc traffic spike chiều thứ Sáu.

Mỗi lần đều phát hiện **sau khi user complain qua support ticket** (18-25 ticket trước khi internal alert fire). Không phải từ monitoring.

Vấn đề không phải thiếu dashboard - đã có Grafana (12 board), CloudWatch (>2k metric), DataDog trial. Vấn đề là **không ai ngồi nhìn 24/7** và **threshold tĩnh thì hoặc quá nhạy (alert fatigue) hoặc quá tù (miss drift slow)**.

## Client muốn gì

Build **Foresight Lens** - system proactive:

1. Tự nhìn time-series metrics per-service 24/7.
2. Học baseline normal của từng service.
3. **Chủ động ping** khi predict drift hoặc capacity exhaustion sắp xảy ra.
4. Warning kèm **capacity recommendation cụ thể** (scale RDS lên class X, tăng worker concurrency lên Y, retire queue Z không còn dùng) - không phải chỉ "service A có vẻ bất thường".

**Predictive lens THUẦN** - predict + recommend, manual approval gate OK. Không auto-remediation (đó là vấn đề khác).

## Hard requirements (Client đã chốt)

- **Lead time ≥15 phút** trước SLO breach (test window ≥2h).
- **FP rate ≤12%**, **catch ≥80% drift** trên test scenarios.
- **Multi-tenant ≥3 services** (TF4 phải test đa-service vì chính là điểm bài toán).
- **Per-service baseline** (không 1-size-fits-all), manual refresh OK (weekly cadence documented).
- **$200/tháng AWS bill** budget rough cho capstone.
- **Manual baseline train** 1 lần + ADR retrain trigger logic (không cần build auto-retrain pipeline).
- Capacity recommendation phải actionable (action verb + target + from→to + confidence + evidence link).
- Audit log mỗi prediction call, encrypted at rest, retention spec'd.

## Out of scope (Client confirm KHÔNG làm)

- Auto-remediation (predict + recommend only).
- Cross-service root cause analysis (per-service drift only).
- Cost forecasting (chuyển sang TF2).
- Auto-retrain pipeline build (design ADR đủ).
- Multi-region deployment (single region, DR design-only).
- Production traffic mirror (HV tự load test với k6/Locust).
- Custom business metrics (transaction count, fraud rate) - infra metric only.
- SLO 99.99% availability (99.5% demo-quality OK).
- >3 tenant/service test (3 tier-1 service đủ).
- Historical 6-month training data (synthetic + 2-7 day generated OK).
- Closed-loop full Predict → Detect → Heal → Verify → Learn (capstone Predict + recommend only).
- Real customer PII trong metric (schema whitelist, reject ingest).
- Adversarial input defense (note future work).
- LLM-based prediction (cost prohibitive, statistical/ML đủ).
- Mobile/web UI dashboard mới (embed annotation vào Grafana existing).

## Cần build (deliverable cấp cao)

**Nhóm AI**:
- AI Engine: time-series metric ingest → per-service baseline → drift detect → capacity recommendation.
- Endpoint `POST /v1/predict`: input time-series window, output drift + recommendation + confidence.
- 3 contracts ký với CDO. Lưu ý TF4 contract specifics:
  - **Telemetry**: high-volume time-series với schema bắt buộc `service_id` + `metric_type` + `tenant_id`. Volume cao hơn default (vd 50k events/sec peak). Retention 90 ngày minimum. Storage phải support time-series query hiệu quả (Timestream/InfluxDB/Prometheus, không phải raw S3).
  - **Deployment**: tách 2 topology - Model Serving (build thật) + Model Training (design-only OK).

**Nhóm CDO** (3 nhóm trong TF4):
- Platform infra hosting engine theo angle riêng (TSDB-centric, lakehouse, managed observability, hoặc khác).
- Test environment với synthetic workload + load test simulate drift/spike.
- Integrate AI endpoint + dashboard annotation overlay.

## Output phải có cuối W12

- AI engine deployed trên cả 3 CDO platform.
- 3 tier-1 service với per-service baseline working.
- 4 test scenario chạy thật: gradual drift / sudden spike / slow leak / noisy baseline.
- Test window ≥2h với lead time ≥15min trên ít nhất 1 scenario.
- Eval report: precision/recall/F1 measured, FP ≤12%, catch ≥80%, confusion matrix.
- Capacity recommendation đủ 5 phần (action verb + target + from→to + confidence + evidence link).
- Audit log mỗi prediction call (≥6 field), encrypted at rest.
- Manual baseline train 1 lần + ADR retrain trigger logic.
- Cost circuit breaker $200 cap test demo được.
- Confidence calibration evidence (Brier score hoặc reliability diagram).
- Fail-open fallback to static threshold khi serving endpoint down.
- 3 contracts ký, FREEZE từ T5 W11.
- Slides + individual pitches + curveball responses.

## Cần clarify với Client (Phase 1 Discovery T2-T3)

Đây là **danh mục** câu hỏi cần đặt ra trong phòng họp. Mảng phải đào:

- **Service scope**: 120 service nhưng baseline cho tất cả hay subset tier-1 nào.
- **Metric priority**: CPU/memory/latency/throughput/queue/custom - cái nào critical nhất.
- **Lead time**: ≥15min là minimum, có service nào cần ≥1h (vd RDS scale up takes time).
- **Recommendation actionability**: auto-apply hay manual approve, scope tới đâu.
- **Training data**: historical metrics có sẵn, retention bao lâu, granularity.
- **Baseline refresh**: weekly OK hay drift-triggered, ai trigger.
- **Model deployment**: multi-region cho redundancy, fail-open fallback strategy.
- **Test environment**: production-like traffic không, load test tooling.
- **Compliance**: drift detection có touch PII không, SOC2 controls, retention.
- **Failure mode**: engine down, fallback, alert routing.
- **Cost guard**: per inference cost cap, circuit breaker threshold.
- **Conflict handling**: 2 service đề xuất scale conflict, surface cả 2 hay resolve.
- **Seasonality**: daily/weekly pattern, business hours vs weekend, holiday.
- **Onboarding**: time per new service từ register đến baseline ready.

## Trước khi vào phòng họp T2 W11

Đọc kỹ "Bối cảnh + Vấn đề + Client muốn gì" để hiểu landscape. Đem theo 10-15 câu hỏi cụ thể. Sau 90 phút interview, viết debrief "Tôi hiểu là..." gửi mentor confirm trước EOD T2 - tránh hiểu nhầm dẫn đến rework T3-T4.
