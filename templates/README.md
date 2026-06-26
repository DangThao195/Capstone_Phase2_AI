# Capstone Templates

Skeleton templates cho documentation evidence pack. Cover cả Nhóm AI và Nhóm CDO trong 4 task force độc lập.

## Cách dùng

1. Copy folder này vào repo task force của mình:
   ```
   capstone/tf-<N>/ai/         ← copy templates/ai/* vào đây
   capstone/tf-<N>/cdo-<M>/    ← copy templates/cdo/* vào đây
   ```
2. Mở từng file, fill in nội dung theo guidance trong `<!-- comment -->` blocks. Xóa comment sau khi fill.
3. Commit theo từng phase (xem `../reference/CAPSTONE_EVIDENCE_PACK_FORMAT.md` §2 checkpoint cadence).

## Folder structure

```
templates/
├── ai/                                  # 6 docs cho Nhóm AI
│   ├── docs/
│   │   ├── 01_requirements.md
│   │   ├── 02_solution_design.md
│   │   ├── 03_ai_engine_spec.md
│   │   ├── 04_eval_report.md
│   │   └── 05_adrs.md
│   └── contracts/
│       ├── telemetry-contract.md
│       ├── ai-api-contract.md
│       └── deployment-contract.md
└── cdo/                                 # 7 docs cho Nhóm CDO
    └── docs/
        ├── 01_requirements_analysis.md
        ├── 02_infra_design.md
        ├── 03_security_design.md
        ├── 04_deployment_design.md
        ├── 05_cost_analysis.md
        ├── 07_test_eval_report.md
        └── 08_adrs.md
```

## Doc size tiers

Templates có 3 tier theo độ phức tạp. Xem header mỗi template (`Word target: ...`):

| Tier | Word target | Số dòng template (skeleton) | Docs |
|---|---|---|---|
| **Light** | 800-1500 từ | 40-60 dòng | requirements, ADRs, cost analysis, requirements analysis |
| **Medium** | 1000-2500 từ | 60-150 dòng | solution design, eval, ops runbook, infra design, deployment, test eval |
| **Heavy** ⭐ | 2500-4000 từ | 300-460 dòng | **AI engine spec + Security design** - enterprise-aligned |

2 file Heavy có nhiều section (9 và 15). Đọc kỹ `📌 Capstone scope guide` ở đầu mỗi file để biết section nào **MANDATORY Pack #1** vs **Pack #2 full** vs **design-only OK cho capstone**.

## Quy ước

- **Markdown only** (`.md`). Không Google Doc, không PDF rời rạc
- **HTML comments** `<!-- guidance -->` = instruction cho HV, **XÓA SAU KHI FILL**
- **Placeholders** dạng `<...>` hoặc `...` = HV fill in
- **Code blocks** giữ nguyên YAML / JSON structure, chỉ thay value
- **Diagrams** dùng Mermaid inline trong markdown. PNG / draw.io export OK nếu Mermaid không đủ - đặt trong `docs/assets/`
- **ADRs**: format strict (Status / Date / Context / Decision / Consequence / Alternatives). Append-only - không xóa ADR cũ khi superseded, đánh dấu thay
- **Related documents** footer: hầu hết template có cross-link tới docs liên quan - giữ + update khi đổi tên file

## Reference

- `../W11_W12_capstone_announcement.md` - flow + lịch + deliverable checklist
- `../reference/CAPSTONE_EVIDENCE_PACK_FORMAT.md` - format chi tiết + checkpoint cadence
- `../reference/CAPSTONE_TF{1..4}_*_CLIENT_BRIEF.md` - đọc brief đề mình trước khi interview Client T2 W11
