# Requirements - <Đề tài>

<!-- Doc owner: <Nhóm AI leader name>
     Status: Draft (W11 T2-T3) → Final (W11 T6 Pack #1)
     Word target: 800-1500 từ
     BA methodology: dùng 5W2H làm khung khi interview Client T2 W11 -->

## 1. Khách hàng nói

> <!-- Quote nguyên văn client narrative từ playbook §3 -->

## 2. Outcomes mong muốn (restate own words)

<!-- Restate những gì client muốn, bằng từ ngữ của team -->

- Outcome 1: ...
- Outcome 2: ...
- Outcome 3: ...

## 3. Success criteria (measurable)

<!-- Cụ thể, measurable. Tránh adjective. -->

| Metric | Target | How to measure |
|---|---|---|
| ... | ... | ... |
| ... | ... | ... |

## 4. Constraints

- **Budget**: $X cho 2 tuần build
- **Timeline**: W11-W12, code freeze T4 W12 18h
- **Tooling**: AWS only, no multi-cloud
- **Compliance**: <nếu có - vd SOC2, GDPR>

## 5. Out of scope

<!-- Cái KHÔNG làm. List explicit để tránh scope creep -->

- ❌ ...
- ❌ ...
- ❌ ...

## 6. Non-functional requirements

- **SLO platform**: p99 latency < Xms · availability ≥ 99.5% · error rate < 0.5%
- **Multi-tenant scale**: ≥ N tenant cùng instance
- **Security baseline**: IAM least privilege · secrets via Secrets Manager · audit 90+ days
- **Cost target**: $X/tenant/month

## 7. Open questions

<!-- Câu cần hỏi Client clarification. Update status khi resolved -->

- [ ] Q1: ... - *Asked T2, awaiting response*
- [ ] Q2: ... - *Resolved: <answer>*
