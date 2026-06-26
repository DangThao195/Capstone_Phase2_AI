# Capstone Evidence Pack - Format & Checkpoint Guide

> Hướng dẫn HV về documentation artifacts cần submit trong 2 tuần capstone.
> Áp dụng cho cả Nhóm AI và Nhóm CDO trong 4 task force độc lập.

---

## 1. Mục đích

Document trail của bạn = công cụ chính để show **process thinking**, không chỉ "đoạn code chạy được". Reviewer chấm dựa trên **3 layer evidence**:

1. **Document quality** - thinking, trade-off analysis, justification (đây là evidence pack)
2. **Build artifacts** - code, infra, AI engine working
3. **Buổi chấm performance** - present + Q&A + individual defense

Doc artifact đóng góp **~40% điểm W11** và **~30% điểm W12** (plus là input chính để reviewer hiểu trước buổi chấm T5 W12).

**Quy tắc gốc**:
- Doc viết **live trong repo** (markdown), git history = evidence cho process - không phải "viết một phát cuối"
- Doc phải show **WHY**, không chỉ WHAT - tại sao chọn cái này, không chọn cái kia
- Doc phải **implementable** - đọc xong người khác build lại được, không chỉ "high-level handwave"

---

## 2. Timeline Checkpoint

```
W11 T2 ─── T3 ─── T4 ─── T5 ─── T6           W12 T2 ─── T3 ─── T4
                  ▲              ▲                  ▲              ▲
              [Progress #1]  [Evidence #1]    [Progress #2]   [Evidence #2]
              light check    MAIN ⭐           light check     MAIN ⭐
```

### 4 Checkpoint

| # | Khi | Bắt buộc gì | Scoring |
|---|---|---|---|
| **Progress #1** (light) | EOD T4 W11 | Requirements + Solution/Infra Design draft + 3 contracts draft | Sanity check, không chấm chính thức |
| **Evidence Pack #1** ⭐ | EOD T6 W11 | TẤT CẢ doc Pack #1 + base infra chạy được | **~40% điểm W11** |
| **Progress #2** (light) | EOD T2 W12 | Progress demo + intermediate docs updated | Sanity check |
| **Evidence Pack #2** ⭐ | EOD T4 W12 (cùng code freeze 18h) | TẤT CẢ doc final + test + eval + cost + ops | **~30% điểm W12** + input chính cho buổi chấm T5 |

**Light progress check**: review qua repo commits + WhatsApp standup, không cần buổi formal. Mục đích phát hiện task force tụt sớm, không phải gate.

---

## 3. Document Set per Role

### 3.1 Nhóm AI - 6 documents

| File | Pack #1 W11 | Pack #2 W12 | Mục tiêu |
|---|---|---|---|
| `01_requirements.md` | ✓ draft | ✓ refined | Restate đề tài Client + scope + success criteria |
| `02_solution_design.md` | ✓ draft | ✓ updated | High-level architecture + alternatives considered |
| `03_ai_engine_spec.md` | ✓ | ✓ updated | Model choice + safety + multi-tenant routing |
| `04_eval_report.md` | (skeleton) | ✓ **full results** | Eval methodology + precision/recall/latency/cost |
| `05_adrs.md` | ✓ ongoing | ✓ final | Architecture Decision Records |
| `06_contracts/*.md` | ✓ signed T5 | (locked) | telemetry · ai-api · deployment contracts |

### 3.2 Nhóm CDO - 7 documents

| File | Pack #1 W11 | Pack #2 W12 | Mục tiêu |
|---|---|---|---|
| `01_requirements_analysis.md` | ✓ | ✓ | Phân tích đề tài từ infra perspective |
| `02_infra_design.md` | ✓ draft | ✓ updated | Architecture + differentiation angle + multi-tenant approach |
| `03_security_design.md` | ✓ draft | ✓ refined | IAM · secrets · network · audit · tenant isolation |
| `04_deployment_design.md` | ✓ draft | ✓ working | IaC + CI/CD + GitOps + canary |
| `05_cost_analysis.md` | (skeleton) | ✓ measured | Per-tenant cost model + monthly forecast |
| `07_test_eval_report.md` | - | ✓ **new** | SLO evidence + load test + multi-tenant isolation |
| `08_adrs.md` | ✓ ongoing | ✓ final | Architecture Decision Records |

---

## 4. Document Templates

Mỗi doc có structure tối thiểu dưới. Copy section headers vào file, fill in nội dung.

**Word count theo 3 tier** (xem header mỗi template để biết tier cụ thể):

| Tier | Word target | Docs |
|---|---|---|
| **Light** | 800-1500 từ | `01_requirements`, `05_adrs` (AI) · `01_requirements_analysis`, `05_cost_analysis`, `08_adrs` (CDO) |
| **Medium** | 1000-2500 từ | `02_solution_design`, `04_eval_report` (AI) · `02_infra_design`, `03_security_design`, `04_deployment_design`, `07_test_eval_report` (CDO) |

Cả 2 tier viết theo skeleton template trong `templates/`. Viết WHY (lý do quyết định), không phải WHAT (mô tả lại code).

### 4.1 Nhóm AI Templates

#### `01_requirements.md`

```markdown
# Requirements - <Đề tài>

## 1. Khách hàng nói
Quote nguyên văn client narrative.

## 2. Outcomes mong muốn (restate own words)
- Outcome 1: ...
- Outcome 2: ...

## 3. Success criteria (measurable)
- Metric 1: target = X
- Metric 2: target = Y

## 4. Constraints
Budget · timeline · tooling · compliance.

## 5. Out of scope
Cái KHÔNG làm (avoid scope creep).

## 6. Non-functional requirements
SLO · multi-tenant scale · security baseline · cost target.

## 7. Open questions
Câu cần hỏi Client clarification. Update khi resolved.
```

#### `02_solution_design.md`

```markdown
# Solution Design - <Đề tài>

## 1. High-level architecture
Mermaid diagram or embedded image - show major components + data flow.

## 2. Component breakdown
| Component | Responsibility | Tech choice | Why |
|---|---|---|---|

## 3. Data flow
Step-by-step: input signal → process → output.

## 4. Alternatives considered (KEY section)
Cho mỗi quyết định lớn, viết:
- Option A: ... (pros/cons)
- Option B: ... (pros/cons)
- Chosen: <which> - Reason: ...

## 5. Risk + mitigation
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|

## 6. Open design questions
Câu chưa quyết, plan để resolve.
```

#### `03_ai_engine_spec.md`

```markdown
# AI Engine Spec - <Đề tài>

## 1. Model architecture
Single-shot LLM / agent / classifier ML / hybrid - justify choice.

## 2. Model selection
Bedrock model / OpenAI / etc. + version + region + cost-per-call estimate.

## 3. Multi-tenant routing
Làm sao tenant A's data không leak sang tenant B?

## 4. Prompt engineering / RAG strategy
(If applicable) prompt structure + system message + RAG index design.

## 5. Safety guardrails
- Schema validation: ...
- Confidence threshold: ...
- Hallucination guard: ...
- Refuse logic: ...

## 6. Eval methodology
- Test set: synthetic / real / mixed - số scenario, source
- Metrics: precision · recall · F1 · latency · cost-per-correct-decision
- Acceptance threshold: ...

## 7. Cost model
- Per-call cost estimate
- Per-tenant monthly forecast
- Bedrock prompt cache strategy

## 8. Deployment topology
ECS Fargate / Lambda / SageMaker - justify.
```

#### `04_eval_report.md` (Pack #2)

```markdown
# Eval Report - <Đề tài>

## 1. Test scenarios
List ≥10 scenarios cover happy path + edge cases.

## 2. Methodology
Setup · test data · run procedure · metrics measured.

## 3. Results
| Metric | Target | Actual | Pass/Fail |
|---|---|---|---|
| Precision | ≥0.8 | 0.85 | ✓ |
| Recall | ≥0.7 | 0.65 | ✗ |
| P99 latency | <500ms | 420ms | ✓ |
| Cost/call | <$0.01 | $0.008 | ✓ |

## 4. Failure analysis
Scenarios fail → root cause → fix attempted → result.

## 5. Curveball impact
3 curveball - pass/fail mỗi cái + lessons learned.

## 6. Improvement next iteration
Top 3 gap + plan.
```

#### `05_adrs.md`

```markdown
# Architecture Decision Records

## ADR-001 - <Title>
- **Status**: Accepted | Superseded
- **Context**: tại sao có decision này
- **Decision**: chốt gì
- **Consequence**: trade-off + impact downstream

## ADR-002 - ...
```

### 4.2 Nhóm CDO Templates

#### `01_requirements_analysis.md`

```markdown
# Requirements Analysis - Task force <N> Infra

## 1. Đề tài context
(Refer Nhóm AI's requirements doc - restate ngắn gọn)

## 2. Infra non-functional requirements
- Multi-tenant scale: 50+ tenant
- SLO platform: p99 latency · availability · error rate
- Security baseline: ...
- Cost target: $/tenant/month
- Onboarding SLA: < 30 min

## 3. Differentiation angle (KEY)
- Angle chọn: serverless-first / K8s-heavy / managed-services / event-driven
- Why this angle: cost / reliability / ops / scalability
- Trade-off chấp nhận: ...

## 4. Comparison với 2 nhóm cùng task force
- Nhóm khác A's angle: ... → khác nhau ở chỗ ...
- Nhóm khác B's angle: ... → khác nhau ở chỗ ...
- Cạnh tranh trên axis nào?
```

#### `02_infra_design.md`

```markdown
# Infrastructure Design

## 1. Architecture diagram
Mermaid or PNG - major components + AWS services + data flow.

## 2. Component table
| Component | Service | Reason | Cost note |
|---|---|---|---|

## 3. Differentiation angle deep-dive
- Tại sao serverless-first / K8s-heavy / managed-services...?
- Vượt trội ở đâu cụ thể (số liệu nếu có)?

## 4. Multi-tenant approach
- Tenant model: tenant_id format · X-Tenant-Id header · subscription tier
- Isolation pattern: silo / pool / bridge - justify
- Tenant onboarding flow + SLA (target < 30 min)
- Noisy neighbor mitigation (quota / rate-limit / reservation)

## 5. Alternatives considered
- Option A: ... (pros/cons)
- Option B: ... (pros/cons)
- Chosen: <which> - Reason: ...

## 6. Scaling strategy
- Vertical scale: ...
- Horizontal scale: ...
- Auto-scaling triggers: ...

## 7. Failure modes + recovery
| Failure | Detection | Recovery | RTO/RPO |
|---|---|---|---|
```

#### `03_security_design.md`

```markdown
# Security Design

## 1. IAM model
- Roles: ... (least privilege)
- Cross-account assume-role pattern
- Service-to-service auth: IAM SigV4
- **Per-tenant IAM role + permission boundary** (tenant isolation)

## 2. Secrets management
- AWS Secrets Manager / Parameter Store
- Rotation policy

## 3. Network policy
- VPC topology
- Security groups + **inter-tenant communication blocked**
- WAF / Shield configuration

## 4. Audit trail
- Format (JSON schema)
- Storage (S3 Object Lock, retention 90+ days)
- Query interface

## 5. Compliance touch
- SOC2 controls touched
- Data residency considerations
- **GDPR-style tenant data deletion + retention policy**

## 6. Threat model (STRIDE)
| Threat | Component | Mitigation |
|---|---|---|
| Spoofing | API GW | JWT + IAM |
| Tampering | Audit log | Object Lock |
| ... | | |

## 7. Incident response runbook (high-level)
- Detection
- Containment
- Eradication
- Recovery
- Post-mortem
```

#### `04_deployment_design.md`

```markdown
# Deployment & CI/CD Design

## 1. IaC strategy
- Tool: Terraform / CDK / CloudFormation
- Module structure
- State management

## 2. CI/CD pipeline
- Build → Test → Scan → Deploy
- GitHub Actions / GitLab / Jenkins
- Quality gates

## 3. GitOps
- ArgoCD / Flux
- Sync waves
- Drift detection

## 4. Deployment strategy
- Canary / Blue-green / Rolling
- Abort criteria
- Rollback method + RTO

## 5. Environment separation
- Sandbox / Staging / Prod
- Promotion process

## 6. Secrets in pipeline
- How CI access secrets without leak
```

#### `05_cost_analysis.md` (Pack #2 - measured)

```markdown
# Cost Analysis

## 1. Cost model per tenant
| Component | Unit cost | Tenant avg usage | $/tenant/month |
|---|---|---|---|
| Compute | $X/hr | Y hr | $Z |
| Storage | $X/GB | Y GB | $Z |
| AI inference | $X/call | Y calls | $Z |
| **Total** | | | **$N** |

## 2. Cost at scale
- 10 tenants: $N × 10 = $...
- 50 tenants: ...
- 200 tenants: ...

## 3. Cost optimization applied
- Spot instances
- Reserved capacity
- Prompt caching
- Storage tiering

## 4. Cost vs alternatives (cùng task force)
- Nhóm khác A: ước tính $X/tenant
- Nhóm khác B: ước tính $Y/tenant
- Mình: $Z/tenant - lý giải trade-off

## 5. Measured actual (capstone)
$ spent over 2-week build period - split by service.
```

#### `07_test_eval_report.md` (Pack #2)

```markdown
# Test & Eval Report

## 1. Test coverage
- Unit / integration / E2E
- % coverage if measured

## 2. Chaos test results
- Curveball #1 small: response + outcome
- Curveball #2 medium: response + outcome
- Curveball #3 chaos: response + outcome

## 3. SLO evidence
| SLO | Target | Measured | Pass/Fail |
|---|---|---|---|

## 4. Load test results
Synthetic load + observed behavior.

## 5. Security test
- Penetration touch points
- Vulnerability scan results

## 6. Failure analysis
Failures encountered → root cause → fix.
```

#### `08_adrs.md`

Same template as Nhóm AI `05_adrs.md`.

---

## 5. Format Conventions

### File format
- **Markdown only** (`.md`) - không Google Doc / PDF rời rạc
- File live trong repo, không attach binary
- Char encoding UTF-8

### Diagrams
- **Mermaid** preferred - embed inline trong markdown, version-controllable
- PNG / draw.io export OK nếu Mermaid không đủ - place trong `assets/` folder
- Mọi diagram phải có caption + 2-3 dòng giải thích

### Code blocks
- Use fenced code blocks với language tag (` ```yaml `, ` ```python `, ` ```hcl `)
- Code snippet trong doc = pseudo-code OK; full implementation đặt trong source folder riêng

### ADR format (strict)
```markdown
## ADR-NNN - <Short title>
- **Status**: Proposed | Accepted | Superseded | Rejected
- **Context**: 1-3 câu tại sao có decision
- **Decision**: chốt cụ thể gì
- **Consequence**: trade-off + impact downstream
- **Alternatives considered**: bullet list
```

### Word count guideline (3 tier - xem §4)
- **Light** (800-1500): requirements, ADRs, cost analysis - concise
- **Medium** (1000-2500): solution design, infra, deployment, ops, eval
- **Heavy** (2500-4000): AI engine spec, Security design - enterprise spec depth
- **< 500 từ trong Light/Medium tier** = thường thiếu depth
- **> word target × 1.5** = fluff hoặc nên split sub-doc

### Cross-references
- Refer đến doc khác qua relative path: `[xem 02_solution_design.md](02_solution_design.md)`
- Refer đến ADR cụ thể: `ADR-005`

---

## 6. Scoring Integration

Doc artifacts map vào pillar **đã có sẵn** trong playbook rubric:

### Nhóm AI

| Rubric pillar (playbook §9.1) | Doc đóng góp |
|---|---|
| W11 P1 "Module Spec + Design" (45%) | `01_requirements.md` + `02_solution_design.md` + `03_ai_engine_spec.md` |
| W11 P2 "3 Contracts Quality" (40%) | `06_contracts/*.md` |
| W11 P3 "Client Behavior" (15%) | Q&A response quality (không từ doc) |
| W12 P1 "AI Engine Quality" (45%) | Build + `04_eval_report.md` |
| W12 P2 "AI Chốt Quality" (25%) | Buổi chấm + `05_adrs.md` |
| W12 P3 "Present Performance" (15%) | Buổi chấm |
| W12 P4 "Individual Defense" (15%) | Buổi chấm |

### Nhóm CDO

| Rubric pillar (playbook §9.2) | Doc đóng góp |
|---|---|
| W11 P1 "Platform Design Doc" (35%) | `01-04` + `08_adrs.md` |
| W11 P2 "Contract Acceptance" (20%) | Contract review notes |
| W11 P3 "Base Infra Ready" (35%) | Build + `04_deployment_design.md` |
| W11 P4 "Task force Sync" (10%) | Commit T3 angle no-overlap |
| W12 P1 "Infrastructure Quality" (40%) | Build + `02_infra_design.md` updated |
| W12 P2 "AI Engine Integration" (25%) | E2E demo + `06` + `07` |
| W12 P3 "Present Performance" (25%) | Buổi chấm |
| W12 P4 "Individual Defense" (15%) | Buổi chấm |

---

## 7. Repo Structure

```
capstone/
└── tf-<N>/                          # 4 task force: tf-1, tf-2, tf-3, tf-4
    ├── ai/
    │   ├── docs/
    │   │   ├── 01_requirements.md
    │   │   ├── 02_solution_design.md
    │   │   ├── 03_ai_engine_spec.md
    │   │   ├── 04_eval_report.md
    │   │   ├── 05_adrs.md
    │   │   └── assets/               # diagrams, screenshots
    │   ├── contracts/
    │   │   ├── telemetry-contract.md
    │   │   ├── ai-api-contract.md
    │   │   └── deployment-contract.md
    │   ├── ai-engine/                # source code
    │   └── standup-notes.md
    │
    └── cdo-<M>/                       # 2-3 Nhóm CDO / task force
        ├── docs/
        │   ├── 01_requirements_analysis.md
        │   ├── 02_infra_design.md
        │   ├── 03_security_design.md
        │   ├── 04_deployment_design.md
        │   ├── 05_cost_analysis.md
        │   ├── 07_test_eval_report.md
        │   ├── 08_adrs.md
        │   └── assets/
        ├── infra/                    # Terraform / IaC
        ├── manifests/                # K8s / app configs
        └── standup-notes.md
```

---

## 8. Checklist nhanh per checkpoint

### Progress #1 (EOD T4 W11) - light

**Nhóm AI**:
- [ ] `01_requirements.md` (draft)
- [ ] `02_solution_design.md` (draft, can be sketchy)
- [ ] `06_contracts/*.md` (draft, ready for T5 sign)

**Nhóm CDO**:
- [ ] `01_requirements_analysis.md` (draft)
- [ ] `02_infra_design.md` (draft + angle declared + multi-tenant approach)
- [ ] `08_adrs.md` (≥2 ADR cho key decisions)

### Evidence Pack #1 (EOD T6 W11) - MAIN ⭐

**Nhóm AI**:
- [ ] `01_requirements.md` (final)
- [ ] `02_solution_design.md`
- [ ] `03_ai_engine_spec.md`
- [ ] `04_eval_report.md` (skeleton with planned metrics)
- [ ] `05_adrs.md` (≥3 ADRs)
- [ ] `06_contracts/*.md` 🔒 signed T5
- [ ] AI module skeleton deployed sandbox

**Nhóm CDO**:
- [ ] `01_requirements_analysis.md`
- [ ] `02_infra_design.md` (with multi-tenant approach)
- [ ] `03_security_design.md` (draft)
- [ ] `04_deployment_design.md` (draft)
- [ ] `05_cost_analysis.md` (skeleton)
- [ ] `08_adrs.md` (≥3 ADRs)
- [ ] Base infra (VPC + cluster + observability) chạy được

### Progress #2 (EOD T2 W12) - light

**Nhóm AI**:
- [ ] AI module integrated với ≥1 CDO platform
- [ ] `04_eval_report.md` (intermediate results)

**Nhóm CDO**:
- [ ] AI engine integration started
- [ ] Tenant onboarding flow draft

### Evidence Pack #2 (EOD T4 W12) - MAIN ⭐ + code freeze 18h

**Nhóm AI**:
- [ ] All docs final
- [ ] `04_eval_report.md` **full results**
- [ ] `05_adrs.md` final (≥5 ADRs)
- [ ] AI engine deployed multi-tenant
- [ ] git tag `final` per nhóm

**Nhóm CDO**:
- [ ] All docs final
- [ ] `05_cost_analysis.md` **measured**
- [ ] `07_test_eval_report.md` **new** với chaos response evidence
- [ ] `08_adrs.md` final (≥5 ADRs)
- [ ] Platform infra deployed + integrated với AI engine
- [ ] git tag `final` per nhóm

---

## 9. Anti-patterns (tránh)

- ❌ Doc viết một phát cuối T6/T4 → git history thấy ngay
- ❌ Doc Light/Medium tier < 500 từ - thiếu depth, không pass
- ❌ Doc vượt word target × 1.5 (vd Light > 2250, Medium > 3750, Heavy > 6000) - fluff hoặc nên split
- ❌ Diagram không có caption / giải thích - vô nghĩa
- ❌ ADR chỉ "we chose X" without alternatives + reasoning
- ❌ Copy-paste content giữa task force (CDO trong cùng task force phải differentiation rõ)
- ❌ Doc Pack #2 không update sau curveball - show không adapt
- ❌ Cost analysis Pack #2 chỉ estimate, không measured actual
- ❌ Eval report chỉ có metric, không có failure analysis

---

## 10. Tips để pass nhanh

1. **Bắt đầu với template** - copy section headers, fill in từng phần
2. **Viết "WHY first, WHAT after"** - reviewer care về reasoning hơn là implementation detail
3. **Diagram > prose** ở chỗ phù hợp - Mermaid sequence diagram thường rõ hơn 5 đoạn văn
4. **Cập nhật doc theo build** - every commit có thể trigger 1 doc edit (good for git history evidence)
5. **Cross-link** giữa docs - show coherent thinking
6. **Numbers > adjectives** - "p99 latency 420ms" beat "performance is good"
7. **Honesty about trade-off** - "we chose X but accepted Y limitation" beats "X is the best"
8. **Address curveball trong doc** - show how design adapted

Đọc kỹ playbook §9 Rubric trước khi viết - biết reviewer chấm gì.
