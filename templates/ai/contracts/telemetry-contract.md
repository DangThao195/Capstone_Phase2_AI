# Telemetry Contract - Task force <N>

<!-- Owner: Nhóm AI <N>
     Signed by: AI Lead + CDO Leads × 2-3 + Reviewer panel
     Date signed: 2026-06-25 (W11 T5)
     🔒 FREEZE - no change without formal change request -->

## Mục đích

Định nghĩa **signals nào CDO emit từ infra** → AI engine consume. Là handshake giữa platform layer (CDO) và intelligence layer (AI).

## Versioning

- **Current version**: `v1.0`
- **Evolution**: backward-compatible additions only. Breaking change → new contract version + migration window
- **Change request process**: raise trong WhatsApp group task force → task force meeting discuss → bump version + notify all parties

---

## Signals required

> List signals AI engine cần để analyze. Mỗi signal có schema + frequency + emit point + SLA.

### Signal 1: `<signal_name>` - ví dụ `api_latency_ms`

| Attribute | Value |
|---|---|
| **Type** | histogram / gauge / counter / event |
| **Labels** | service, endpoint, region, tenant_id (mandatory) |
| **Unit** | milliseconds / bytes / count |
| **Frequency** | 1 giây / 30 giây / on-event |
| **Emit point** | mô tả pipeline (vd: ALB access log → Lambda → Kinesis) |
| **Retention** | 7 ngày hot, 30 ngày cold |
| **Used for** | AI consumer mục đích gì (vd: anomaly detection sliding window 5min) |
| **Emit SLA** | p99 latency từ event → AI consumable |
| **Volume SLA** | events/sec peak |
| **Cost estimate** | $X / tháng |

**Schema example** (concrete JSON payload AI nhận được):

```json
{
  "ts": "2026-06-25T10:30:00Z",
  "tenant_id": "tnt-abc123",
  "service": "checkout-api",
  "endpoint": "/v1/orders",
  "value": 1250.5,
  "labels": {"region": "ap-southeast-1"}
}
```

### Signal 2: `<signal_name>` - ví dụ `pod_oom_event`

| Attribute | Value |
|---|---|
| **Type** | event |
| **Labels** | namespace, deployment, container, memory_limit_mb, tenant_id |
| **Frequency** | on-event |
| **Emit point** | K8s events watcher → EventBridge → SQS |
| **Used for** | OOMKilled pattern detection |
| **Emit SLA** | < 30 giây |

**Schema example**:

```json
{
  "ts": "2026-06-25T10:30:00Z",
  "tenant_id": "tnt-abc123",
  "namespace": "prod-app",
  "pod_name": "checkout-api-7f8d9-xyz",
  "container": "main",
  "memory_limit_mb": 512,
  "exit_code": 137,
  "last_logs_snippet": "..."
}
```

### Signal N: ... (add as needed)

<!-- Lặp pattern trên cho mỗi signal cần thêm. Tối thiểu 2-3 signals cho capstone scope. -->

---

## Cross-cutting requirements

Mọi signal phải comply:

- **Tenant scoping**: mọi signal payload **bắt buộc** có `tenant_id` field - AI engine không accept signal thiếu tenant_id
- **Time precision**: timestamp RFC3339 UTC, millisecond precision
- **Schema validation**: AI ingestion layer validate schema; reject malformed → log to dead-letter queue
- **PII**: KHÔNG được chứa PII (email / phone / name) trong signal value hoặc labels - anonymize ở ingestion layer của CDO

---

## Open questions

- [ ] Q1: Signal nào cần đảm bảo exactly-once delivery vs at-least-once OK?
- [ ] Q2: Có signal nào cần encryption-in-transit ngoài TLS chuẩn?
