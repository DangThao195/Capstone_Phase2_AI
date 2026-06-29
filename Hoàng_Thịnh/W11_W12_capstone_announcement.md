# Capstone Phase 2 (W11-W12)

> Gửi cuối T6 W10 (19/06/2026) hoặc sáng T2 W11 (22/06).

Chào cả nhà,

Hai tuần cuối rồi nhé. Capstone bắt đầu sáng T2 22/06, kéo dài tới hết T5 02/07.

Anh nói trước cho cả nhà đỡ ngợp: capstone không giống mấy tuần trước đâu. Không còn lab có sẵn answer key, không còn checkpoint chấm likert từng ngày. Sáng T2 cả nhà vào phòng họp gặp "Client" lần đầu, hai tuần sau cuối T5 W12 phải ra khỏi phòng họp với một sản phẩm chạy được, đứng trước panel pitch. Y như một consulting engagement thật ngoài kia.

Cách mình tổ chức: chia thành 4 task force độc lập, mỗi task force build một sản phẩm AIOps riêng. Mỗi task force gồm 1 nhóm AI + 2 hoặc 3 nhóm Cloud/DevOps cùng làm:

- **Nhóm AI** (4 nhóm AIO-01..04) Các bạn own đề tài, thiết kế AI engine, viết 3 contracts cho CDO consume, rồi deploy engine cho 2-3 CDO trong task force dùng chung.
- **Nhóm Cloud/DevOps** (9 nhóm CDO-01..09) Mỗi nhóm build infra hosting AI engine theo góc nhìn riêng (serverless, K8s, streaming, lakehouse, vv...). Trong task force, 2-3 CDO cùng đề nhưng compete trên execution quality.

Bốn task force không liên quan gì nhau cả. TF1 không cần care TF2 làm gì. Mỗi task force là một sản phẩm hoàn chỉnh đứng riêng.

Buổi chấm duy nhất là T5 02/07/2026 onsite. Bốn task force pitch tuần tự, mỗi buổi khoảng 100-120 phút. Đầu buổi sáng tới cuối chiều xong là biết tier.

---

## Tổng quan 2 tuần

W11 mục tiêu là nộp bộ tài liệu (Problem Statement, Solution Design, AI Engine Spec, ADRs, 3 Contracts cho AI; Design Doc, Differentiation Angle, ADRs, base IaC cho CDO) và lấy approve onsite T5. Sau approve T5 chiều thì ký 3 contracts giữa AI và CDO. T6 sang giai đoạn build chính thức.

Team nào muốn pre-build trước trong W11 rồi viết doc theo cái đã build cũng OK. Muốn viết doc trước rồi build sau cũng OK. Anh không áp đặt thứ tự. Anh chỉ quan tâm tới T5 cầm bản tài liệu approve trong tay là được, mọi chuyện trong team tự sắp xếp.

W12 là build, integrate, chaos test, polish slides. 8h sáng T5 02/07 code freeze (ngay trước buổi chấm), sau giờ đó chỉ sửa slides với script. T5 onsite buổi chấm full day, cuối ngày biết tier.

| Tuần | Milestone |
|---|---|
| W11 | Tài liệu approve T5, ký 3 contracts, T6 build chính thức |
| W12 | Build + integrate + chaos. 8h sáng T5 freeze code. T5 buổi chấm. |

T6 03/07 cả nhà nghỉ hoặc wrap-up casual.

---

## 4 Đề tài

Bốn đề cố định, đến sáng T2 8h30 mới biết nhóm AI nào bốc trúng đề nào. Random pick tại kickoff luôn, không ai pre-assign.

| Task force | Đề tài | Khách hàng | CDO slot |
|---|---|---|---|
| TF1 | Triage Hub | CTO SaaS startup B2B, 20k user, 50 microservice. On-call burnt out, MTTR tăng | 2 CDO |
| TF2 | FinOps Watch | CFO mid-size company, multi-account AWS. Bill spike 2.3x không rõ nguyên nhân | 2 CDO |
| TF3 | Self-Heal Engine | VP Engineering, 200+ microservice K8s. 80% on-call alert là known patterns | 2 CDO |
| TF4 | Foresight Lens | Head of SRE fintech, ~120 service. Miss SLO 7 lần do capacity exhaustion silent | 3 CDO (+0.1 base bonus) |

Chi tiết từng đề ở 4 file `TF{1..4}_*_LEARNER.md` trong folder `reference/` của repo `xbrain-learners` trên GitHub. Sáng T2 sau khi random pick, team truy cập link repo và đọc file `TFx_*_LEARNER.md` tương ứng với đề mình bốc. Không phải đọc cả 4. Phòng họp Client sẽ mở chiều T2 sau khi team đã có 2-3 tiếng đọc + thảo luận nội bộ.

---

## Lịch chi tiết

### W11 (22/06-26/06)

Đầu tuần T2 22/06 8h30 cohort-wide kickoff. Announce capstone, công bố 4 đề tài, nhóm AI bốc thăm random. Sau bốc đề, team truy cập link GitHub repo `xbrain-learners`, mở file `TFx_*_LEARNER.md` tương ứng đề mình. Sáng + đầu chiều T2 team đọc đề + thảo luận. Chiều T2 mở phòng họp Client (mentor đóng vai) interview.

Mục tiêu tuần này: tới chiều T5 25/06 onsite, mỗi team phải có bộ tài liệu **achievable** (tức là có thể thực sự build trong W12) và lấy được approve. Đây giống POC để trúng thầu, không phải lock blueprint cứng cho W12.

Khái niệm achievable - quan trọng cả nhà hiểu rõ:

- Tài liệu phải show được: "Trong 6 ngày W12, với resource hiện có, team này build được architecture này." Không phải "đây là production-ready blueprint."
- Mentor approve nghĩa là: "Đề xuất của em hợp lý, build được, tôi tin." Không phải "em phải execute đúng từng dòng trong doc này."
- W12 build có quyền iterate. Phát hiện angle khác tốt hơn? Cập nhật ADR + push commit. Discover constraint mới? Re-design. Doc theo code, không phải code theo doc.
- Chỉ có **3 contracts AI-CDO bị FREEZE T5**. Lý do: contract là interface giữa AI và CDO, đổi giữa chừng là cả 2 vỡ trận. Architecture nội bộ của từng nhóm thì flex được.

Cả nhà tự quyết thứ tự làm trong tuần. Pre-build trước rồi viết doc theo cái đã làm cũng được. Viết doc kỹ trước rồi build sau cũng được. Vừa làm vừa viết song song càng tốt. Miễn là tới T5 có bộ doc approve.

Cột mốc cố định trong tuần:

- T2 sáng 8h30: kickoff + random pick đề tài. Team truy cập repo `xbrain-learners` mở file đề mình bốc.
- T2 chiều: phòng họp Client mở. Team interview mentor-as-Client.
- T4 chiều: AI publish 3 contracts draft.
- T5 sáng: mentors di chuyển bay ra Đà Nẵng. HV tự sắp xếp: AI-CDO co-design contracts nội bộ + finalize tài liệu. Không có session với mentors.
- T5 chiều onsite: mentors ngồi chấm tài liệu + approve achievable + ký contracts (witness) + curveball #1 nhẹ (15p). Đây là ngày approve duy nhất, không có cơ hội thứ 2.
- T6 onsite: build chính thức, AI engine core + CDO base IaC.

Mỗi ngày 14h có standup 15 phút per task force. Còn lại team tự sắp xếp.

### W12 (29/06-03/07): tuần build

| Ngày | Sáng | Chiều |
|---|---|---|
| T2 29/06 online | AI: safety guard, multi-tenant routing. CDO: CI/CD canary, observability, integrate AI endpoint. | 14h standup. 16h curveball #2 medium (30p). |
| T3 30/06 online | AI: eval set, scenario testing. CDO: E2E test per platform. | 14-16h per-task force integration session. 16h task force sync. |
| T4 01/07 online | Polish E2E, slides draft. | 14h curveball #3 chaos (60p). 16-17h respond + final polish. 17-22h dry-run + buffer fix bug. |
| T5 02/07 onsite (BUỔI CHẤM) | 🛑 **8h CODE FREEZE**, git tag `final` (sau giờ này chỉ sửa slides + script). 8h30 opening. 9-10h45 TF1 (Triage). 10h45-12h30 TF2 (FinOps). | 13h30-15h15 TF3 (Self-Heal). 15h15-17h TF4 (Foresight, 3 CDO). 17-18h calibration + tier reveal. |
| T6 03/07 | Không có hoạt động capstone. | Wrap-up casual hoặc nghỉ. |

---

## 3 Contracts (core deliverable của W11)

Trong task force, AI xây engine, CDO xây platform. Hai bên làm song song hai tuần. Nếu không thống nhất trước, AI build cái CDO không xài được, hoặc CDO emit data AI không hiểu, vỡ trận luôn.

Contract = thỏa thuận viết ra, ký xong là đóng đinh. Sau khi ký T5 W11, ai muốn sửa phải qua curveball chính thức.

Có 3 contract phải ký:

1. **Telemetry Contract**: signals nào platform phải emit để AI có data (logs, metrics, events, format, SLA, cost). AI draft.
2. **AI API Contract**: endpoint AI expose để CDO call (URL, input schema, output schema, SLA, error handling). AI draft.
3. **Deployment Contract**: AI engine sống ở đâu (compute, scale, secrets, network, rollback). AI draft.

Timeline ký contract:

- EOD T4 W11: AI publish 3 contracts dạng draft.
- T4 chiều - T4 đêm: CDO review draft, note push-back.
- T5 sáng (HV tự tổ chức, không mentor): AI và CDO trong task force co-design, push-back, sửa contracts.
- T5 chiều onsite: trình mentor + ký xong, đóng đinh.
- W11 T6 trở đi: cả hai nhóm build độc lập theo contract.

Lúc co-design T5 sáng, CDO cứ thoải mái push-back. Không có chuyện AI bảo gì CDO làm nấy. Push-back productive đúng chỗ thì sản phẩm cuối mới tốt.

### AI API Contract = endpoint pre-defined trước, KHÔNG phải engine chạy thật

> Đây là chỗ dễ hiểu nhầm nhất. Đọc kỹ.

Sau khi ký T5 W11, AI API Contract định nghĩa:
- URL path (vd `POST /v1/diagnose` cho TF1).
- Request schema (input fields, types).
- Response schema (output JSON structure, vd `{anomaly: bool, severity: float, confidence: float, reasoning: string}`).
- SLA (P99 latency, throughput, availability).
- Error codes (400/401/429/503, CDO handle thế nào).

CDO build platform infra theo spec này **mà không cần AI engine chạy thật**. CDO test integration bằng **mock endpoint**: dùng curl trả về fake response đúng schema, verify code path của mình.

**Nhưng W12 T3 integration session**: CDO PHẢI call AI endpoint THẬT (không mock nữa) để test E2E. Đây là chỗ dependency thật xuất hiện.

### Mitigation: AI deploy skeleton T5 chiều, CDO có endpoint thật từ T6

Để CDO không phải đợi AI logic complete, AI nhóm deploy **engine skeleton** ngay T5 W11 chiều (cùng lúc với ký contract):

- Lambda/Fargate task minimal với **dummy logic**: return hardcoded JSON đúng schema (vd luôn trả `{anomaly: true, severity: 0.7, confidence: 0.85, reasoning: "skeleton response"}`).
- Endpoint URL fix, accessible từ CDO subnet trong task force.
- Logic bên trong sẽ replace dần trong W12 bằng real AI inference.

→ Từ T6 W11, CDO có endpoint thật để call. Không depend AI logic finished.
→ W12 AI thay dummy bằng real inference + safety guards + multi-tenant routing. Endpoint URL không đổi, schema không đổi.
→ W12 T3 integration session: CDO call endpoint thật, response thật, eval E2E.

### Tóm tắt dependency thật

| Khi nào | CDO depend AI? | Note |
|---|---|---|
| T6 W11 - T2 W12 | Không (chỉ depend endpoint URL + schema, có skeleton từ T5) | CDO build infra + integrate code path theo contract spec |
| T3 W12 (integration session) | **CÓ** | CDO call AI endpoint thật, verify E2E. Nếu engine chưa work → cap T2-T3 buổi chấm |
| T4-T5 W12 (polish + buổi chấm) | CÓ (depend final engine quality) | AI eval + safety + multi-tenant complete |

### Tóm 1 câu

> **AI define endpoint spec trước. CDO build infra theo spec, không depend logic. AI deploy skeleton T5 để CDO có endpoint thật. W12 AI thay dummy bằng real inference - URL không đổi, schema không đổi, CDO không phải redo.**

---

## Curveballs

Ba lần inject scope change có chủ đích trong 2 tuần. Mô phỏng real-world client change request. Mục đích là tập phản xạ adapt scope mà không panic, không cãi cọ, không miss deadline.

- **#1 Nhẹ** (W11 T5 cuối, 15p). Ví dụ: "Client thêm severity classify low/med/high, contract sửa gì?" hoặc "Region switch ap-southeast-1 sang us-east-1, impact ra sao?"
- **#2 Medium** (W12 T2 chiều, 30p). Ví dụ: "Data schema `latency_ms` đổi sang `latency_us`, migrate không downtime kiểu gì?" hoặc "Traffic 5x tuần tới, scale ra sao?"
- **#3 Chaos** (W12 T4 chiều, 60p). Ví dụ: "Region down 30 phút, failover thế nào?" hoặc "Bedrock throttling 60% calls, fallback code path?"

Mỗi lần curveball xong, document response vào `curveball-responses.md`. Buổi chấm panel sẽ hỏi team xử lý ra sao.

---

## Hỏi mentor

Có gì cần clarify, ping trực tiếp một trong 4 mentor: **anh Khánh, anh Nam Hồng, anh Toàn, anh Nghĩa**. Không phải tag cả 4 cùng lúc - pick 1 người, đợi response, nếu không phản hồi trong nửa ngày thì thử người khác.

Format câu hỏi nên có: **Context (1 câu)** + **Câu hỏi cụ thể** + **What you've tried** (nếu là technical block). Tránh "anh ơi giúp em với", chỉ cần "TF2 đang chốt time frame goal giữa 24h và 48h, lý do em đang weigh là X vs Y, anh có insight gì không?"

---

## BA skill phase (W11 T2-T3)

Trước khi build, các bạn phải clarify brief đã. Đặt câu hỏi đúng, debrief với Client, lock scope rồi mới sờ vào code. Bỏ qua bước này tốn 1-2 ngày rework giữa W11-W12, tức là mất 20-30% time budget.

70% học viên năm nào cũng nhảy thẳng vào solution sau khi nghe brief:

- "OK, anomaly detection thì mình train IsolationForest."
- "Self-heal thì build K8s operator."

Tới T3-T4 mới phát hiện scope sai, miss constraint, hiểu nhầm priority của Client. Lúc đó sửa thì đã mất 2 ngày rồi.

Sáng T2 sau random pick, team mở file `TFx_*_LEARNER.md` của đề mình trên GitHub repo `xbrain-learners`. Đọc + thảo luận trong sáng + đầu chiều T2. Phòng họp Client mở chiều T2 sau khi team đã có 2-3 tiếng tiêu hoá đề.

Trước khi vào phòng họp Client, mỗi bạn cần:

- Đọc kỹ file đề tài team mình bốc.
- Chuẩn bị sẵn 10-15 câu hỏi specific. Dùng 5W2H làm khung, đừng improvise hỏi linh tinh. "Anh muốn gì" là câu hỏi tệ nhất.

---

## Activity Tracking trên Jira (bắt buộc)

Mỗi task force có 1 Jira project riêng. Đã setup sẵn từ Phase 2 rồi, không phải tạo mới.

Mỗi bạn bắt buộc làm 4 việc:

1. **Pick task** hàng ngày. Pull từ backlog, assign cho mình, move To Do sang In Progress.
2. **Update status** theo flow: To Do → In Progress → In Review → Done.
3. **Daily comment** ít nhất 1 lần/ngày. Progress note, blocker (nếu có), ETA update.
4. **Evidence link** khi close. Mỗi task Done phải link commit SHA, PR URL, doc commit, hoặc screenshot.

Task size đúng chuẩn là 30 phút tới 1 ngày. Nếu nhỏ hơn 30p thì gộp vào task lớn hơn. Lớn hơn 1 ngày thì split nhỏ ra.

Vài rule chống gaming, mọi người nên biết trước:

- Task không có evidence link khi close: mentor reject, move ngược lại In Review.
- Backdating (task Done có timestamp trước Assigned): bị flag, investigate.
- Batch fake (5+ tasks Done cùng 1 phút): suspicious, mentor sẽ probe.
- Done ngay sau Assigned không có In Progress: probe.

Jira log là input cho Individual Defense Q&A buổi chấm luôn. Mentor sẽ pick random 2-3 bạn, hỏi: "Task X bạn close ngày Y, walk me through commit, em quyết technical thế này vì sao?" Bạn nào claim Done nhưng không walk through được code thật, ăn cap T3 free-rider luôn.

Pattern free-rider điển hình: dưới 5 task Done trong 2 tuần, không có evidence link, hoặc mọi task chỉ là "doc edit / standup note" mà không có work thật. Gặp dấu hiệu này thì Individual Defense cap T3, không gánh được bằng group score.

---

## Daily Standup

14h hàng ngày, 15 phút strict. Per-task force, không cross-task force. AI lead + 2-3 CDO leads.

Mỗi bạn nói ≤30 giây: Done, Doing, Blocker. Có thế thôi.

Log vào `capstone/tf-<N>/standup-notes.md` (append-only, không xoá).

Mấy red flag tự escalate khi gặp:

- 2 ngày liên tiếp 1 bạn vẫn cùng 1 blocker chưa resolve.
- AI và CDO disagree contract interpretation, không thống nhất được.
- Build progress dưới 50% expected mid-week.

Gặp 1 trong 3 dấu hiệu này, ping mentor ngay. Đừng chờ thêm 1 ngày nữa.

---

## Deliverable Checklist

Toàn bộ doc bên dưới đã có template sẵn trong folder `templates/`. Copy vào repo task force của mình rồi fill in, đừng tự tạo file mới từ đầu. Xem `templates/README.md` để biết cách dùng (HTML comment guidance, placeholders, doc size tier).

> **ADR là gì?** Architecture Decision Record. File log mỗi quyết định kiến trúc quan trọng (vd "chọn Bedrock thay vì OpenAI", "compute Fargate thay vì Lambda", "time frame goal 24h thay vì 12h") kèm lý do tại sao chọn cái đó + alternatives đã consider. Format 4 phần: Context · Decision · Consequence · Alternatives. Append-only - không xóa ADR cũ khi đổi, đánh dấu `Superseded by ADR-NNN`. Mọi quyết định có trade-off thật phải có ADR; buổi chấm panel sẽ probe "sao chọn X?" - không có ADR = mentor probe sâu hơn. Chi tiết xem header trong `templates/ai/docs/05_adrs.md` hoặc `templates/cdo/docs/08_adrs.md`.

### W11, EOD T6 26/06

Nhóm AI (template `templates/ai/`):

- `docs/01_requirements.md`
- `docs/02_solution_design.md`
- `docs/03_ai_engine_spec.md`
- `docs/05_adrs.md`
- `contracts/telemetry-contract.md` (đã ký)
- `contracts/ai-api-contract.md` (đã ký)
- `contracts/deployment-contract.md` (đã ký)
- `engine-skeleton/`: module structure + dummy logic deployed

Nhóm CDO (template `templates/cdo/`):

- `docs/01_requirements_analysis.md`
- `docs/02_infra_design.md`
- `docs/03_security_design.md`
- `docs/04_deployment_design.md`
- `docs/08_adrs.md`
- `infra/`: Terraform base VPC + cluster + observability chạy được
- `standup-notes.md`

### W12, 8h sáng T5 02/07 (code freeze)

Nhóm AI (template `templates/ai/`):

- `final-build/`: AI engine deployed
- `docs/04_eval_report.md`: precision, recall, latency
- `SLIDES.pdf`
- `demo-video.mp4`
- `curveball-responses.md`
- `individual-pitches.md`

Nhóm CDO (template `templates/cdo/`):

- `final-build/`: IaC + manifests integrated
- `docs/05_cost_analysis.md`
- `docs/07_test_eval_report.md` (SLO evidence: p99 latency, availability, cost + multi-tenant isolation test)
- `SLIDES.pdf`
- `demo-video.mp4`
- `curveball-responses.md`
- `individual-pitches.md`
- `retrospective.md`

Format Evidence Pack chi tiết, đọc `reference/CAPSTONE_EVIDENCE_PACK_FORMAT.md` trước T2 W11. Một điểm quan trọng: doc phải viết live trong repo, không phải "viết một phát cuối". Git history là evidence cho process.

---

## Buổi chấm (T5 02/07)

Format 1 buổi chấm, ~105 phút cho task force 2-CDO, ~120 phút cho TF4 3-CDO:

1. 5 phút mở: context + đề tài recap.
2. 15 phút × 2-3: CDO present lần lượt. Mỗi nhóm CDO trả lời: "Infra của tôi tốt ở đâu, vượt trội gì so với nhóm cùng task force?" Demo + differentiation.
3. 20 phút: Nhóm AI chốt. Trình bày design AI engine + đánh giá 3 CDO solution (trade-off, recommend angle).
4. 25 phút: Panel Q&A. Random pick 2-3 HV để individual defense.
5. 15 phút: chấm closed. Panel chấm AI design + AI chốt + CDO solutions.

Panel chấm 2-3 reviewer cùng ngồi, chấm độc lập per criterion, lấy average. Cuối ngày 17-18h cross-task force calibration review tier distribution toàn cohort, có thể adjust nhẹ cap ±0.2.

---

## Rubric (overview, không weight chi tiết)

Anh chỉ nói overview thôi, không dump weight chi tiết vì rubric là tài liệu của panel. Cái cần biết:

**Nhóm AI**

- W11 (35% total): chất lượng spec + design đề tài, chất lượng 3 contracts, behavior với CDO trong Q&A.
- W12 (65% total): chất lượng AI engine (eval metrics + safety guards + deploy được trên cả 2-3 CDO platforms), chất lượng "chốt" buổi chấm, present performance, individual defense.

**Nhóm CDO**

- W11 (25% total): platform design doc, contract acceptance, base infra ready EOD T6, task force sync.
- W12 (75% total): infrastructure quality (IaC + GitOps + observability + security baseline), AI engine integration, present performance differentiation, individual defense. TF4 cộng thêm 0.1 base bonus vì 3-CDO.

Vài rule anti-theater để biết trước:

- Pitch xuất sắc nhưng ≥50% HV defense fail: cap nhóm ở Tier 3.
- AI engine hoặc CDO infra chạy được nhưng pitch tệ: cap ở Tier 2.
- CDO differentiation thuyết phục yếu (không ra được story "tôi vượt trội gì"): cap ở Tier 2.

---

## Pre-W11 Prep (làm xong trước T2 22/06)

- Đọc `CAPSTONE_EVIDENCE_PACK_FORMAT.md` trong `reference/` để hiểu bộ doc phải nộp + checkpoint cadence.
- Mở qua folder `templates/` xem skeleton. Đọc `templates/README.md` để nắm cách copy + fill in.
- Verify Jira account đã vào đúng project task force.
- AWS account capstone confirmed (nếu cần fresh account thay vì reuse W6-W10).

(File 4 đề tài `TFx_*_LEARNER.md` chưa cần đọc trước T2. Sáng T2 sau random pick, team truy cập link GitHub `xbrain-learners` để mở file đề của mình.)

---

## Reference + Templates

- `reference/`: 4 file đề tài cho HV (`TF1_TRIAGE_LEARNER.md`, `TF2_FINOPS_LEARNER.md`, `TF3_SELFHEAL_LEARNER.md`, `TF4_FORESIGHT_LEARNER.md`) + `CAPSTONE_EVIDENCE_PACK_FORMAT.md`. 4 file đề tài chỉ đọc sau khi bốc T2; Evidence Pack Format đọc trước T2.
- `templates/`: bộ skeleton template cho mọi doc phải nộp (5 docs + 3 contracts cho AI · 7 docs cho CDO). Copy vào repo task force rồi fill in theo `<!-- comment -->` guidance. Đừng tự tạo file từ đầu. Xem `templates/README.md` trước khi dùng.

---

## Risk hay gặp, tự đề phòng

| Risk | Tự check |
|---|---|
| AI over-architect, không kịp build | Hard freeze T5 W11. Sau ký contract không thay đổi ngoài curveball. Cuối T6 W11 engine skeleton chưa deploy: escalate mentor ngay. |
| CDO build infra không match contract | Daily standup probe "AI API endpoint test hit chưa?" Không integrate được T2 W12 là đỏ. |
| Một bạn carry cả nhóm | Mid-W12 (T3 chiều) anh sample Jira random. Phát hiện một bạn ôm hết task: đổi assignment + escalate mentor. Individual defense cap T3 free-rider, không gánh được. |
| AI và CDO disagree contract | Daily Q&A 15-16h W11 chính là chỗ resolve. Đừng ngại push-back EOD T4. |
| Slide đẹp nhưng Q&A fail | Practice individual defense sớm. T4 W12 dry-run nội bộ task force là practice run, lấy feedback từ team trước khi vào panel thật. |

---

## Tinh thần

Capstone không phải "tuần học cuối". Đây là mô phỏng job thật sau khi join team. 2 tuần này là cơ hội cuối để practice 4 skill quan trọng:

1. **Scope discipline**. Bốc đề, clarify 2 ngày, ký contract, build 6 ngày, present. Không scope creep, không "tôi muốn làm thêm".
2. **Trade-off justification**. Mọi quyết định (algorithm, infra angle, cost trade) phải defend bằng data + reasoning. Không "vì tôi thích".
3. **Cross-team contract negotiation**. AI và CDO trong task force giống team backend và team platform thật ngoài kia. Push-back productive là skill, không phải conflict.
4. **Individual ownership trong group**. Random pick 2-3 HV defense, không có chỗ trốn. Mỗi task em claim Done phải walk through được commit + decision + trade-off.

Treat capstone như engagement thật, đừng treat như lab.

Đọc xong tin này thì mở `CAPSTONE_EVIDENCE_PACK_FORMAT.md` + folder `templates/` trong `reference/` để biết bộ doc phải nộp. File 4 đề tài (`TFx_*_LEARNER.md`) chờ tới sáng T2 random pick xong mới đọc.

Chúc cả nhà 2 tuần solid.

Nghĩa
