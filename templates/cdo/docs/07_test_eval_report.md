# Test & Eval Report - Task force <N> · CDO <M>

<!-- Doc owner: <Nhóm CDO>
     Status: NEW (W12 T4 Pack #2 only)
     Word target: 1000-1800 từ -->

## 1. Test coverage

| Test type | Tool | Coverage / Scope |
|---|---|---|
| Unit test | <pytest / go test> | <X%> |
| Integration test | <custom / Postman> | Tenant provision flow + AI integration |
| E2E test | <Playwright / k6> | Happy path 3 scenarios |
| Load test | <k6 / Locust> | Sustained 100 RPS for 10 min |
| Chaos test | <Litmus / manual> | 3 curveball scenarios |

## 2. SLO evidence

| SLO | Target | Measured | Window | Pass/Fail |
|---|---|---|---|---|
| API availability | ≥ 99.5% | X% | 2 weeks build period | ✓/✗ |
| P99 latency | < 1000ms | Xms | Last 24h | ✓/✗ |
| Error rate | < 0.5% | X% | Last 24h | ✓/✗ |
| Tenant onboarding | < 30 min | X min | 3 test tenants | ✓/✗ |

### 2.1 SLO breach analysis

<!-- Nếu có SLO miss, phân tích root cause -->

## 3. Load test results

### 3.1 Test setup

- **Load profile**: ramp-up 0 → 100 RPS over 5 min, sustained 100 RPS for 10 min
- **Tenants simulated**: 10 concurrent
- **Tool**: <k6 / Locust>

### 3.2 Results

| Metric | Target | Achieved |
|---|---|---|
| RPS sustained | 100 | X |
| P99 latency at peak | < 1500ms | Xms |
| Error rate at peak | < 1% | X% |
| Auto-scale triggers | scale to ≥ 5 tasks | ✓/✗ |

### 3.3 Bottleneck identified

<!-- DB connection pool? AI engine throttle? Compute? -->

## 4. Security test

### 4.1 Penetration touch points

- ☐ API auth bypass attempt
- ☐ Cross-tenant data leak attempt
- ☐ SQL injection / NoSQL injection
- ☐ IAM privilege escalation
- ☐ Secret exposure via logs

### 4.2 Vulnerability scan

- **Tool**: Trivy / Snyk / AWS Inspector
- **CRITICAL findings**: 0 (must be 0 by pack #2)
- **HIGH findings**: ≤ 3 with documented mitigation
- **Report**: `<repo>/security/scan-results.json`

## 5. Multi-tenant isolation test

<!-- Critical - multi-tenant data leak = cap T3 per playbook §10.4 -->

| Test | Method | Result |
|---|---|---|
| Tenant A reads Tenant B data via API | Inject A's token, request B's resource | ❌ Should fail with 403 |
| Tenant A IAM role accesses B's S3 prefix | Assume A's role, attempt B access | ❌ Should fail |
| Cross-tenant queue contamination | Tenant A enqueue with B's tenant_id | Audit log catches mismatch |
| DB row-level security | Query without tenant_id filter | Should return empty / error |

**All tests must pass** - any leak = SEV1 incident.

## 6. Failure analysis

### 6.1 Failures encountered during 2-week build

| # | Failure | Root cause | Fix | Time to fix |
|---|---|---|---|---|
| 1 | <description> | ... | ... | X hours |
| 2 | ... | ... | ... | X hours |

### 6.2 Test gaps acknowledged

<!-- Honest: cái gì chưa test đủ, sẽ test post-capstone -->

- Gap 1: ...
- Gap 2: ...

## Related documents

- [`02_infra_design.md`](02_infra_design.md) - SLO targets validated trong §3 doc này
- [`03_security_design.md`](03_security_design.md) §14 - Risk registry mitigated bởi test results §6 doc này
- [`../../ai/docs/04_eval_report.md`](../../ai/docs/04_eval_report.md) - Joint eval: AI engine quality + CDO infra integration
