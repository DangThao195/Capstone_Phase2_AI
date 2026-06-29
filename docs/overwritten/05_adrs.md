# Architecture Decision Records - Nhóm AI - TF2 FinOps Watch System

## ADR-001 - Choose 24h Detection Cadence over 12h and 48h

- **Status**: Accepted
- **Date**: 2026-06-24

### Context

Task Force 2 cần phát hiện AWS cost anomaly đủ sớm để tránh trường hợp tài nguyên bị bỏ quên tiếp tục phát sinh chi phí trong nhiều ngày. Tuy nhiên, dữ liệu AWS cost/billing như Cost Explorer và CUR không phải dữ liệu real-time hoàn toàn, thường có độ trễ cập nhật 12-24h và có thể có trạng thái `is_estimated = true` trong các ngày gần nhất. Vì vậy nhóm AI cần chọn cadence phù hợp để cân bằng giữa tốc độ phát hiện anomaly và rủi ro tăng false positive.

### Decision

Nhóm AI chọn `24h Scheduled Batch` làm chu kỳ mặc định cho pipeline detection. CDO Platform trigger batch job lúc `02:00 AM` hằng ngày, đóng gói dữ liệu chi phí và gửi sang AI Engine. Nếu dữ liệu CUR bị trễ, CDO phát `telemetry_delay_event` để AI Engine hoãn xử lý và kiểm tra lại sau mỗi 1 giờ. Cadence `12h` chỉ được xem là lựa chọn mở rộng hoặc ad-hoc nếu client yêu cầu phát hiện nhanh hơn và dữ liệu đủ ổn định.

### Consequence

- Phù hợp với bản chất dữ liệu AWS cost/billing vì dữ liệu không cập nhật real-time theo giây.  
- Giảm rủi ro false positive so với cadence 12h do dữ liệu có thêm thời gian ổn định.  
- Vẫn đủ nhanh để phát hiện runaway resource trong vòng khoảng 1 ngày, thay vì để kéo dài nhiều ngày như quy trình thủ công.  
- Giảm số lần gọi Cost Explorer API và giảm rủi ro rate limit so với chạy quá thường xuyên.  

- Không phát hiện anomaly theo phút hoặc theo giờ.  
- Nếu một tài nguyên dev/sandbox tiêu tốn chi phí rất nhanh, hệ thống có thể mất tối đa gần 24h mới phát hiện.  
- Nếu client yêu cầu detection nhanh hơn, nhóm phải cập nhật ADR/contract và bổ sung cơ chế xử lý dữ liệu estimated để tránh false positive.

### Alternatives considered

- **Option A: 12h cadence** - rejected as default because phát hiện nhanh hơn nhưng dữ liệu cost có thể chưa ổn định, dễ tăng false positive và tăng số lần gọi API.
- **Option B: 48h cadence** - rejected because phát hiện quá chậm, có thể để runaway training cluster hoặc idle resource tiếp tục đốt chi phí thêm nhiều ngày.
- **Option C: Real-time streaming detection** - rejected because AWS billing data không phù hợp với real-time sub-second detection và vượt quá scope POC W11-W12.

---

## ADR-002 - Use Single-Shot Bulk Ingestion from CDO to AI Engine

- **Status**: Accepted
- **Date**: 2026-06-24

### Context

AI Engine cần nhận dữ liệu từ cả hai nguồn: dữ liệu tổng hợp hằng ngày từ `cost_explorer_daily.csv` và dữ liệu chi tiết từ `cur_line_items.csv`. Nếu AI Engine phải tự gọi nhiều API hoặc kéo nhiều nguồn dữ liệu rời rạc, hệ thống sẽ tăng độ trễ, khó kiểm soát schema và khó đảm bảo idempotency cho từng batch run.

### Decision

Nhóm AI chọn cơ chế `Single-Shot Bulk Ingestion`. CDO Platform chịu trách nhiệm kéo dữ liệu từ AWS Cost Explorer/CUR, chuẩn hóa và đóng gói thành một payload JSON duy nhất gửi qua Internal ALB tới AI Engine. Payload phải kèm `X-Idempotency-Key` để AI Engine kiểm tra chạy trùng trước khi xử lý.

### Consequence

- Giảm số lần gọi API giữa CDO và AI Engine vì mỗi batch chỉ cần một request chính.  
- Giúp contract giữa AI và CDO rõ ràng hơn: CDO chuẩn bị dữ liệu, AI xử lý detection/RCA.  
- AI Engine có thể tự chọn trường cần thiết cho Isolation Forest và LLM RCA từ payload đã chuẩn hóa.  
- Idempotency key giúp tránh xử lý trùng cùng một batch gây sai lệch dashboard hoặc lặp action.  

- Payload có thể lớn nếu CDO gửi quá nhiều CUR line items, cần kiểm soát kích thước và paging/chunking nếu cần.  
- AI Engine phụ thuộc vào chất lượng schema và freshness của CDO Platform.  
- Nếu một phần dữ liệu bị thiếu, batch cần có trạng thái `SUSPENDED` hoặc `PARTIAL` thay vì suy luận thiếu căn cứ.

### Alternatives considered

- **Option A: AI Engine tự pull dữ liệu từ S3/Athena/Cost Explorer** - rejected because làm AI Engine phụ thuộc sâu vào data infrastructure của CDO, tăng quyền truy cập và tăng coupling.
- **Option B: Multiple API calls cho từng account/service/resource** - rejected because tăng độ trễ, tăng API round-trips và khó kiểm soát idempotency.
- **Option C: Chỉ gửi daily aggregate, không gửi CUR line items** - rejected because đủ cho detection thô nhưng không đủ context để LLM RCA và tạo engineering action.

---

## ADR-003 - Use Hybrid IF/Heuristic Pre-Filter with Amazon Nova LLM RCA

- **Status**: Accepted
- **Date**: 2026-06-24

### Context

FinOps Watch cần vừa phát hiện anomaly chính xác, vừa giải thích nguyên nhân theo ngôn ngữ dễ hiểu cho Finance và đủ chi tiết cho Engineering. Pure statistical model chạy nhanh nhưng khó hiểu ngữ cảnh nghiệp vụ, còn pure LLM agent đọc toàn bộ CUR sẽ tốn token, chậm và có rủi ro hallucination. Hệ thống cũng bị ràng buộc cost ceiling cho Bedrock dưới khoảng `$50/tháng`.

### Decision

Nhóm AI chọn kiến trúc lai: `Isolation Forest / Heuristic Pre-Filter` dùng để loại bỏ phần lớn dữ liệu bình thường, sau đó chỉ gửi các điểm nghi ngờ sang `Amazon Nova LLM` qua Amazon Bedrock để phân tích RCA và đề xuất mitigation. LLM không trực tiếp quyết định anomaly trên toàn bộ dữ liệu, mà chỉ xử lý các candidate đã được IF/heuristic lọc.

### Consequence

- Giảm mạnh chi phí token vì LLM chỉ đọc tập nghi ngờ, không đọc toàn bộ CUR.  
- Tăng khả năng đạt Precision ≥80% và FP Rate ≤10% nhờ kết hợp tín hiệu toán học với ngữ cảnh RCA.  
- IF/heuristic đảm nhiệm phần lọc nhanh, LLM đảm nhiệm phần giải thích tự nhiên và tạo JSON cho dashboard.  
- Phù hợp với yêu cầu Finance-friendly observability vì LLM có thể tạo executive summary dễ hiểu.  

- Pipeline phức tạp hơn pure statistical vì phải vận hành cả model ML và LLM stage.  
- Cần guardrail/prompt rule để hạn chế hallucination và yêu cầu LLM chỉ dựa trên dữ liệu đầu vào.  
- Nếu pre-filter quá chặt, hệ thống có thể bỏ sót anomaly trước khi tới LLM.

### Alternatives considered

- **Option A: Pure Statistical/ML** - rejected because chạy nhanh và rẻ nhưng không giải thích tốt nguyên nhân cho Finance/Engineering, dễ tăng FP khi gặp load test hoặc migration hợp lệ.
- **Option B: Pure LLM Agent** - rejected because tốn token lớn, chậm, khó audit và có rủi ro hallucination khi nạp toàn bộ CUR.
- **Option C: Manual analyst review only** - rejected because không giải quyết được yêu cầu tự động hóa phát hiện và phản ứng trong ngày.

---

## ADR-004 - Use Amazon Bedrock Nova as the GenAI Provider

- **Status**: Accepted
- **Date**: 2026-06-24

### Context

AI Engine cần một LLM để phân tích RCA, tạo finance-friendly explanation và sinh structured JSON output. Vì hệ thống bị ràng buộc AWS-only, cần kết nối nội bộ an toàn, kiểm soát IAM/secret và có thể áp dụng guardrail để giảm rủi ro prompt injection hoặc rò rỉ dữ liệu.

### Decision

Nhóm AI chọn `Amazon Nova Pro/Lite` thông qua `Amazon Bedrock API` làm GenAI provider cho tầng RCA và explanation. AI Engine gọi Bedrock bằng IAM Role/SigV4, không hard-code long-lived access key. Nova Lite có thể dùng cho tác vụ đơn giản/chi phí thấp, Nova Pro dùng cho RCA phức tạp hơn nếu cần chất lượng suy luận cao hơn.

### Consequence

- Phù hợp ràng buộc AWS-only của capstone.  
- Tích hợp tốt với IAM Role, Secrets Manager, private networking và Bedrock Guardrails.  
- Giảm rủi ro bảo mật so với việc gọi provider ngoài AWS bằng API key public.  
- Có thể kiểm soát cost bằng circuit breaker và giới hạn token hằng tháng.  

- Phụ thuộc vào quota, latency và availability của Bedrock trong region triển khai.  
- Cần thiết kế fallback hoặc degraded mode nếu Bedrock lỗi hoặc vượt cost ceiling.  
- Chất lượng RCA phụ thuộc vào prompt, input JSON và grounding rule.

### Alternatives considered

- **Option A: OpenAI API** - rejected because không phù hợp ràng buộc AWS-only và làm tăng yêu cầu quản lý API key/egress ra ngoài AWS.
- **Option B: Self-host open-source LLM** - rejected because vận hành GPU/serving phức tạp, chi phí cao và vượt scope POC 2 tuần.
- **Option C: Không dùng LLM, chỉ template text cố định** - rejected because khó giải thích linh hoạt các case RCA khác nhau và không đủ Finance-friendly.

---

## ADR-005 - Use Environment-Based Safe Containment Matrix

- **Status**: Accepted
- **Date**: 2026-06-24

### Context

Client yêu cầu hệ thống không chỉ cảnh báo mà còn auto-containment an toàn cho các pattern rõ ràng như idle resource, runaway training hoặc mis-tagged spend. Tuy nhiên, requirement có ranh giới đỏ cứng: không terminate prod, không xóa dữ liệu và không modify IAM. Vì vậy cần một chiến lược containment phân vùng theo mức độ rủi ro môi trường.

### Decision

Nhóm AI chọn `Safe Multi-Environment Containment Matrix`. Hành động được quyết định theo `resource_tags_user_environment`:

- `prod`: chỉ `tag-for-review` và alert SRE/DevOps, không tự động tắt hoặc giới hạn tài nguyên.
- `staging`: gắn tag countdown và time-gated alert 4 giờ; nếu không có phản hồi thì mới kích hoạt enforcement theo policy.
- `dev/sandbox`: nếu confidence ≥ 0.80 thì cho phép auto-containment an toàn như stop instance hoặc schedule shutdown.
- `ml-research`: nếu confidence ≥ 0.80 thì cho phép stop GPU/notebook idle để giảm runaway training cost.
- `data-analytics`: áp dụng quota/cost cap policy để tránh vòng lặp truy vấn làm tăng chi phí.

Mọi hành động phải ghi audit trail và rollback path.

### Consequence

- Bảo vệ Production khỏi hành động tự động nguy hiểm.  
- Vẫn tạo giá trị tự động hóa rõ ràng ở các môi trường rủi ro thấp như dev/sandbox/ml-research.  
- Dễ giải thích trong defense vì action không do LLM tự ý làm, mà phải đi qua ma trận guardrail.  
- Hỗ trợ giảm chi phí nhanh ở các case rõ ràng như GPU idle hoặc dev instance chạy ngoài giờ.  

- Prod có thể tiếp tục phát sinh chi phí cho đến khi SRE xử lý thủ công.  
- Staging/dev vẫn có rủi ro tác động workload hợp lệ nếu confidence hoặc tag sai.  
- Cần CDO cấu hình IAM/webhook policy đúng để enforcement không vượt quá ranh giới đỏ.

### Alternatives considered

- **Option A: Hard Stop All** - rejected because tiết kiệm nhanh nhưng có thể gây gián đoạn production và vi phạm compliance.
- **Option B: Only Alert, No Containment** - rejected because không đạt mục tiêu tự động ngăn chặn lãng phí ở vùng thấp.
- **Option C: LLM tự quyết định và thực thi action trực tiếp** - rejected because LLM có rủi ro hallucination và không được vượt qua policy/guardrail layer.

---

## ADR-006 - Use DynamoDB with S3 Archive for Idempotency and Audit Trail

- **Status**: Accepted
- **Date**: 2026-06-24

### Context

AI Engine cần kiểm tra chạy trùng lặp theo `X-Idempotency-Key`, lưu trạng thái batch, lưu audit trail, before/after state và rollback path. Đồng thời, hệ thống cần retention tối thiểu 90 ngày để phục vụ kiểm toán và backtest. Nếu chỉ ghi file log ra S3, truy vấn dashboard và kiểm tra idempotency sẽ chậm.

### Decision

Nhóm AI chọn `Amazon DynamoDB + S3 Archive`. DynamoDB dùng cho idempotency check, batch state, audit trail nóng và rollback payload. TTL 24h áp dụng cho idempotency key chạy batch; audit trail lưu tối thiểu 90 ngày. Dữ liệu audit hết hạn hoặc cần lưu dài hạn sẽ được stream/nén sang S3 Standard/Glacier Archive.

### Consequence

- DynamoDB hỗ trợ truy vấn nhanh cho idempotency và dashboard state.  
- Dễ lưu cấu trúc actor, before/after state, action payload và rollback path.  
- TTL giúp kiểm soát chi phí lưu trữ nóng.  
- S3 Archive phù hợp cho lưu trữ dài hạn và compliance với chi phí thấp.  

- Phải thiết kế key schema tốt để tránh hot partition.  
- Cần đảm bảo stream/archive không làm mất log audit quan trọng.  
- Tăng thêm một thành phần vận hành so với chỉ ghi log file đơn giản.

### Alternatives considered

- **Option A: S3 + Athena only** - rejected because chi phí rẻ nhưng độ trễ truy vấn cao, không phù hợp cho idempotency check và dashboard trạng thái gần real-time.
- **Option B: RDS/PostgreSQL** - rejected because cần quản trị schema/connection nhiều hơn, không cần thiết cho POC audit key-value/state.
- **Option C: Chỉ dùng CloudWatch Logs** - rejected because khó truy vấn theo idempotency key/action state và không đủ tiện cho rollback payload.

---

## ADR-007 - Deploy AI Engine as FastAPI Docker Service on ECS Fargate

- **Status**: Accepted
- **Date**: 2026-06-24

### Context

AI Engine cần chạy Python/FastAPI, pandas, scikit-learn, gọi Bedrock và expose endpoint nội bộ cho CDO Platform. Sau khi ký deployment contract, đổi compute target sẽ làm CDO phải chỉnh lại network, endpoint, health check và rollout flow, nên đây là decision có reversal cost cao.

### Decision

Nhóm AI chọn `FastAPI Docker service trên AWS ECS Fargate` làm compute target chính cho AI Engine. Service chạy trong private subnet, đặt sau Internal ALB, không public Internet. Lambda chỉ được giữ như fallback cho skeleton hoặc batch nhẹ nếu cần triển khai nhanh.

### Consequence

- Fargate phù hợp workload Python/ML vì dễ đóng gói dependencies bằng Docker.  
- Internal ALB giúp CDO gọi endpoint ổn định trong private network.  
- Không public Internet, giảm bề mặt tấn công.  
- Dễ cấu hình health check, canary rollout, rollback và scaling.  

- Fargate phức tạp hơn Lambda ở phần cluster/service/ALB/networking.  
- Có chi phí nền cao hơn nếu service luôn chạy.  
- Cần CDO/AI phối hợp cấu hình Security Group, IAM Role, Secrets Manager và Bedrock endpoint.

### Alternatives considered

- **Option A: AWS Lambda only** - rejected as default because dependency Python/ML và LLM RCA có thể làm package/runtime phức tạp; vẫn giữ Lambda làm fallback cho skeleton hoặc batch nhẹ.
- **Option B: EC2 self-managed service** - rejected because tăng gánh nặng patching, scaling và vận hành.
- **Option C: EKS/Kubernetes** - rejected because quá nặng cho POC 2 tuần nếu không cần orchestration phức tạp.
- **Option D: Public API endpoint** - rejected because CDO và AI Engine chỉ cần giao tiếp nội bộ, public Internet làm tăng rủi ro bảo mật.

---

## Related documents

- `01_requirements.md`
- `02_solution_design.md`
- `03_ai_engine_spec.md`
- `04_eval_report.md`
- `contracts/telemetry-contract.md`
- `contracts/ai-api-contract.md`
- `contracts/deployment-contract.md`

---

<!-- Append ADR mới ở dưới. Khi một ADR bị thay thế, đánh dấu Status: Superseded by ADR-NNN và không xóa nội dung cũ. -->
