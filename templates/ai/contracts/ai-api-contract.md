# AI API Contract - Task force <N>

<!-- Owner: Nhóm AI <N>
     Signed by: AI Lead + CDO Leads × 2-3 + Reviewer panel
     Date signed: 2026-06-25 (W11 T5)
     🔒 FREEZE - no change without formal change request -->

## Mục đích

Định nghĩa **API endpoints** mà Nhóm AI expose, Nhóm CDO consume. Là service contract giữa AI engine và platform infra.

## Versioning

- **Current version**: `v1.0` (in path `/v1/`)
- **Breaking changes** → new version path `/v2/`, both versions support cùng lúc tối thiểu 30 ngày
- **Non-breaking** (add optional field, add new endpoint) → minor bump, no path change

## Authentication

- **Inter-service**: IAM SigV4 (no API keys)
- **Cross-account**: STS assume-role với session tag `tenant_id`
- **Audit**: every auth event logged

## Rate limiting

- **Per tenant**: N requests/minute (config trong API Gateway usage plan)
- **Global**: M requests/minute (circuit breaker nếu vượt)
- **Response on hit**: `429` với header `Retry-After: <seconds>`

---

## Endpoint 1: `POST /v1/detect`

**Mục đích**: detect anomaly + suggest action từ telemetry signals.

### Request headers

| Header | Type | Required | Description |
|---|---|---|---|
| `X-Tenant-Id` | UUID v4 | ✓ | Tenant identifier |
| `Authorization` | IAM SigV4 | ✓ | Inter-service auth |
| `X-Correlation-Id` | UUID | optional | Trace correlation (auto-generated nếu thiếu) |

### Request body

| Field | Type | Required | Description |
|---|---|---|---|
| `signal_window` | array | ✓ | Time-series datapoints (xem Telemetry Contract) |
| `signal_window[].ts` | RFC3339 | ✓ | Event timestamp UTC |
| `signal_window[].signal_name` | string | ✓ | Tên signal (khớp với Telemetry Contract) |
| `signal_window[].value` | float | ✓ | Measurement value |
| `signal_window[].labels` | object | optional | Additional context labels |
| `context.deployment_version` | string | ✓ | Current deploy SHA hoặc version tag |
| `context.time_range.start_ts` | RFC3339 | ✓ | Analysis window start |
| `context.time_range.end_ts` | RFC3339 | ✓ | Analysis window end |

**Request example**:

```json
{
  "signal_window": [
    {"ts": "2026-06-25T10:00:00Z", "signal_name": "api_latency_ms", "value": 1200},
    {"ts": "2026-06-25T10:01:00Z", "signal_name": "api_latency_ms", "value": 1800}
  ],
  "context": {
    "deployment_version": "v2.3.1",
    "time_range": {
      "start_ts": "2026-06-25T09:55:00Z",
      "end_ts": "2026-06-25T10:01:00Z"
    }
  }
}
```

### Response body

| Field | Type | Description |
|---|---|---|
| `anomaly` | bool | True nếu detect anomaly |
| `severity` | float 0.0-1.0 | Severity score |
| `suggested_action` | enum | `SCALE_UP` / `ROLLBACK` / `ALERT_ONLY` / `INVESTIGATE` |
| `reasoning` | string (≤300 chars) | Human-readable rationale |
| `confidence` | float 0.0-1.0 | Model confidence - CDO dùng cho gating |
| `audit_id` | UUID | Reference cho audit trail lookup |

**Response example**:

```json
{
  "anomaly": true,
  "severity": 0.78,
  "suggested_action": "SCALE_UP",
  "reasoning": "Latency tăng 50% trong 1 phút sau deploy v2.3.1 - likely correlated.",
  "confidence": 0.82,
  "audit_id": "audit-xyz789"
}
```

### SLA

| Metric | Target |
|---|---|
| P99 latency | < 500 ms |
| Throughput | 100 RPS |
| Availability | 99.5% |

### Error codes

| Code | Meaning | CDO action |
|---|---|---|
| `400` | Invalid input schema | Fix client code, KHÔNG retry |
| `401` | Auth failed | Refresh credential, retry once |
| `429` | Rate-limited | Exponential backoff (1s → 2s → 4s ...) |
| `503` | AI engine unavailable | Fallback to rule-based alert (CDO **bắt buộc** có fallback path) |

---

## Endpoint 2: `POST /v1/verify`

**Mục đích**: verify state sau khi CDO execute 1 action (chỉ dùng cho engine type Self-Heal có action).

### Request body

| Field | Type | Required | Description |
|---|---|---|---|
| `action_taken.type` | enum | ✓ | Action type đã execute |
| `action_taken.params` | object | ✓ | Params dùng cho action |
| `action_taken.ts` | RFC3339 | ✓ | Khi action execute |
| `post_state.signal_window` | array | ✓ | Signals sau action (verify window) |

### Response body

| Field | Type | Description |
|---|---|---|
| `success` | bool | Action có thành công không |
| `regression_detected` | bool | Có regression nào không (vd metric khác bị tệ đi) |
| `next_action` | enum | `DONE` / `RETRY` / `ESCALATE` |

### SLA

- P99 latency < 800 ms

---

## Open questions

- [ ] Q1: Có cần endpoint streaming/SSE cho realtime monitoring không?
- [ ] Q2: Webhook callback từ AI sang CDO khi async - có cần không?
