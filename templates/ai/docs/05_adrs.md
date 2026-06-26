# Architecture Decision Records - <Nhóm AI - Đề tài>

<!-- Doc owner: <Nhóm AI>
     Status: Ongoing log W11-W12
     Format: 1 ADR per major decision. Append-only - không xóa ADR cũ. -->

> **ADR là gì**: Architecture Decision Record. File log mỗi quyết định kiến trúc quan trọng + lý do tại sao chọn cái đó (chứ không phải mấy phương án khác). Mục đích: 6 tháng sau quay lại codebase vẫn nhớ "à hồi đó chọn X vì Y, không phải vì tôi thích".
>
> **Khi nào viết ADR**:
> - Decision có **trade-off thật** (chọn X có cost, chọn Y có benefit).
> - Decision **reversal cost cao** (vd sau ký contract, đổi compute target = rebuild infra).
> - Decision có thể bị hỏi "sao chọn vậy?" trong Individual Defense buổi chấm.
>
> **KHÔNG cần ADR cho**: chuyện nhỏ không có trade-off (tên biến, indent style, vv).
>
> **Khi 1 ADR cũ không còn áp dụng**: đánh dấu `Status: Superseded by ADR-NNN`, KHÔNG xóa ADR cũ. Append-only.

**Target**: ≥3 ADR cho Pack #1 (W11) · ≥5 ADR cho Pack #2 (W12).

**Ví dụ topic cần ADR (Nhóm AI)**:
- Chọn AI provider (Bedrock vs OpenAI vs self-host)
- Chọn AI pattern (single-shot LLM vs agent vs RAG vs statistical)
- Multi-tenant routing strategy
- Safety guard threshold (confidence cut-off)
- Eval methodology (test set source, sample size)
- Cost circuit breaker design

---

## ADR-001 - <Short title, e.g., "Single-shot LLM over agentic">

- **Status**: Accepted | Proposed | Superseded by ADR-NNN | Rejected
- **Date**: 2026-MM-DD
- **Context**: <1-3 câu tại sao có decision này. What forced it?>
- **Decision**: <chốt cụ thể gì>
- **Consequence**:
  - ✅ Pro 1
  - ✅ Pro 2
  - ⚠️ Trade-off 1
  - ⚠️ Trade-off 2
- **Alternatives considered**:
  - Option A: ... (rejected because ...)
  - Option B: ... (rejected because ...)

---

## ADR-002 - <Short title>

- **Status**: ...
- **Date**: ...
- **Context**: ...
- **Decision**: ...
- **Consequence**: ...
- **Alternatives considered**: ...

---

## ADR-003 - ...

---

<!-- Append ADR mới ở dưới. Khi 1 ADR bị superseded, đánh dấu Status + link forward. -->
