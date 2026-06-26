# Architecture Decision Records - CDO <M> · Task force <N>

<!-- Doc owner: <Nhóm CDO>
     Status: Ongoing log W11-W12
     Format: 1 ADR per major decision. Append-only - không xóa ADR cũ. -->

> **ADR là gì**: Architecture Decision Record. File log mỗi quyết định kiến trúc quan trọng + lý do tại sao chọn cái đó (chứ không phải mấy phương án khác). Mục đích: 6 tháng sau quay lại codebase vẫn nhớ "à hồi đó chọn X vì Y, không phải vì tôi thích".
>
> **Khi nào viết ADR**:
> - Decision có **trade-off thật** (chọn X có cost, chọn Y có benefit).
> - Decision **reversal cost cao** (vd đổi compute target = rebuild infra).
> - Decision có thể bị hỏi "sao chọn vậy?" trong Individual Defense buổi chấm.
>
> **KHÔNG cần ADR cho**: chuyện nhỏ không có trade-off (tên resource, naming convention, vv).
>
> **Khi 1 ADR cũ không còn áp dụng**: đánh dấu `Status: Superseded by ADR-NNN`, KHÔNG xóa ADR cũ. Append-only.

**Target**: ≥3 ADR cho Pack #1 (W11) · ≥5 ADR cho Pack #2 (W12).

**Ví dụ topic cần ADR (Nhóm CDO)**:
- Infra angle pick (serverless / K8s / streaming / lakehouse / managed observability)
- Compute target (Lambda vs ECS Fargate vs EKS)
- Data storage (DynamoDB vs RDS vs S3+Athena)
- CI/CD strategy (GitHub Actions vs CodePipeline, canary vs blue-green)
- Observability stack (Prometheus+Grafana vs CloudWatch native)
- Security baseline (IAM scope, secrets injection pattern, network isolation)
- Cost trade-off (Reserved Instance vs On-demand cho demo)

---

## ADR-001 - <Short title, e.g., "Lambda over Fargate for compute layer">

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

<!-- Append ADR mới ở dưới. Khi 1 ADR bị superseded, đánh dấu Status + link forward.

Suggested ADR areas (tham khảo, không bắt buộc đủ):
- Compute layer choice (Lambda vs Fargate vs EKS)
- Database choice + multi-tenant pattern (silo/pool/bridge)
- Event bus (EventBridge vs Kinesis vs MSK)
- IaC tool (Terraform vs CDK)
- GitOps tool (ArgoCD vs Flux)
- Observability stack (CloudWatch vs Grafana stack)
- Tenant isolation depth (compute level vs data level vs network level)
- Cost optimization trade-off (cold start vs always-on)
-->
