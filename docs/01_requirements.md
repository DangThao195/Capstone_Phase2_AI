# Requirements - FinOps Watch System

## 1. Khách hàng nói (Client Voice)

> "Tháng trước AWS bill spike 2.3×, từ baseline ~$180k lên ~$420k trong một tháng. Finance team mất gần một tuần mới truy ra nguyên nhân: một dev quên tắt training cluster, đốt $400/day suốt 18 ngày. Đến khi phát hiện thì đã rớt mất ~$7k tiền oan. Tôi muốn một hệ thống FinOps Watch chạy continuous, tự động detect anomaly và alert đúng người (Finance vs Engineering tách routing). Với pattern obvious (idle resource, mis-tagged spend, runaway training) thì phải tự động ngăn chặn an toàn (auto-containment SAFE) kèm dashboard trực quan cho Finance và có bằng chứng backtest dữ liệu 3 tháng. Hãy nhớ: Tuyệt đối không bao giờ được sờ vào Prod resource để tắt hay xóa, không xóa dữ liệu và không sửa đổi IAM quyền hạn."

---

## 2. Outcomes mong muốn (Restated Outcomes)

Hệ thống FinOps Watch được xây dựng nhằm thiết lập một cơ chế tự động hóa giám sát chi phí liên tục theo mô hình toán học lai kết hợp Trí tuệ nhân tạo tạo sinh, thay thế hoàn toàn cho quy trình rà soát thủ công hàng tuần hiện tại, hướng tới 4 kết quả cốt lõi:

* **Outcome 1 - Single-Shot Ingestion & Detection:** Tự động hóa hoàn toàn luồng thu thập dữ liệu chi phí thông qua một Payload JSON gom cụm một lần duy nhất (Single-Shot Bulk Ingestion) gửi từ CDO Platform sang AI Engine theo chu kỳ 24h cố định. Chuyển đổi từ mô hình rà soát hậu kiểm thụ động sang chủ động phát hiện đột biến chi phí toàn diện trong ngày.
* **Outcome 2 - Intelligent Multi-Channel Alerting:** Thiết lập bộ phân luồng thông minh dựa trên điểm tin cậy toán học từ mô hình AI. Xóa bỏ tình trạng trôi tin nhắn kỹ thuật bằng cách định tuyến cảnh báo chi phí vĩ mô hoặc lỗi thẻ tag (`mis-tagged spend`) về kênh Finance Dashboard, và phân phối trực tiếp các lỗi tài nguyên dư thừa (`idle resource`, `runaway cluster`) về kênh Slack của từng Engineering Squad cụ thể chịu trách nhiệm.
* **Outcome 3 - Safe Multi-Environment Containment:** Triển khai các kịch bản ngăn chặn chi phí tự động thông qua Webhook nhưng đảm bảo an toàn tuyệt đối cho hạ tầng. Phân tách nghiêm ngặt kịch bản ứng phó theo ma trận 5 vùng môi trường thực tế doanh nghiệp (`prod`, `staging`, `dev/sandbox`, `ml-research`, `data-analytics`) nhằm triệt tiêu lãng phí tức thì ở vùng thấp mà không gây gián đoạn dịch vụ cốt lõi ở vùng cao.
* **Outcome 4 - Finance-Friendly Observability:** Cung cấp giao diện Dashboard trực quan phân tách giao diện người dùng. Hiển thị tường minh các xu hướng chi phí (`spend trend`), các điểm bất thường được khoanh vùng (`anomaly overlay`) kèm văn bản giải trình nguyên nhân bằng ngôn ngữ tài chính tự nhiên (Finance-friendly) sinh ra bởi mô hình GenAI, loại bỏ rào cản kỹ thuật thuật toán khó hiểu.

---

## 3. Success criteria (Chỉ số Đo lường Thành công)

Hệ thống phải đạt được các chỉ số đo lường hiệu năng kỹ thuật và giá trị kinh doanh cụ thể sau trên tập dữ liệu kiểm thử quá khứ 3 tháng (3-month historical data backtest) do ban tổ chức cung cấp:

| Metric | Target | How to measure |
|---|---|---|
| **AI Detection Precision** | $\ge 80\%$ | $\frac{\text{True Anomalies Caught}}{\text{Total Anomalies Flagged}}$ trên tập backtest dữ liệu 3 tháng. |
| **False Positive (FP) Rate** | $\le 10\%$| $\frac{\text{False Anomalies Flagged}}{\text{Total Normal Data Points}}$ trên tập dữ liệu backtest. |
| **Anomaly Coverage** | $\ge 3$ Anomaly Types | Phải bắt và giải trình thành công ít nhất 3 nhóm bất thường thực tế: Đột biến chi phí đơn lẻ (`runaway training`), Lãng phí máy chạy không tải (`idle resource`), và Sai lệch/Thiếu hụt thẻ tag quản trị (`mis-tagged spend`). |
| **Time Frame Detection Cadence** | 24 Hours | Chu kỳ quét, nạp dữ liệu gom cụm và đưa ra kết quả phân loại bất thường cuối cùng phải hoàn thành trong chu kỳ 24 giờ cố định. |
| **Auto-Containment Implementation** | $\ge 3$ Implemented / $\ge 2$ Dry-run | Lập trình triển khai thực tế 3 kịch bản can thiệp thông qua AWS CLI/API Webhook (`tag-for-review`, `time-gated-countdown`, `auto-shutdown`) và cấu hình sẵn chế độ chạy thử nghiệm an toàn (`dry-run mode mandatory`) cho các kịch bản bóp băng thông hạn ngạch Quota. |

---

## 4. Constraints (Các Ràng buộc Hệ thống)

* **Budget**: Giới hạn nghiêm ngặt chi phí hoạt động của chính hệ thống FinOps Watch trong phạm vi tài khoản AWS Capstone được cấp phát. Tích hợp sẵn cơ chế tự ngắt (Circuit Breaker) trong mã nguồn AI Engine để khống chế chi phí Token gọi sang nền tảng Amazon Bedrock dưới ngưỡng **$50 USD / tháng**.
* **Timeline**: Giai đoạn thiết kế hoàn thiện hệ thống tài liệu kiến trúc kỹ thuật hoàn thành trong Tuần 11. Giai đoạn phát triển code thực chiến, đóng gói Docker và ráp nối API với CDO Platform diễn ra trong Tuần 12. Mốc khóa toàn bộ mã nguồn (**CODE FREEZE**) cố định vào lúc **8h00 sáng Thứ 5 Tuần 12 (02/07/2026)**.
* **Tooling**: Triển khai hoàn toàn trong hệ sinh thái đám mây AWS (AWS-only), sử dụng **FastAPI (Python)** để build AI Engine, đóng gói dạng Docker chạy trên **AWS ECS Fargate** kết hợp gọi mô hình **Amazon Nova (Pro/Lite)** thông qua **Amazon Bedrock API**.
* **Compliance & Boundaries (Ranh giới đỏ cứng)**:
    * **NEVER terminate prod**: Tuyệt đối không bao giờ được tắt, hủy, hạ cấp hay xóa bỏ bất kỳ tài nguyên nào thuộc môi trường Production (`prod-core`, `prod-payments`).
    * **NEVER delete data**: Không bao giờ được thực hiện các hành vi xóa bỏ dữ liệu file chi phí hay log hạch toán hệ thống (Cấm sử dụng lệnh `delete` trên S3/DynamoDB).
    * **NEVER modify IAM**: Hệ thống cấm tuyệt đối hành vi tự động chỉnh sửa quyền hạn IAM, sửa đổi Policy hoặc thay đổi chính sách bảo mật baseline của doanh nghiệp.
    * **Retention Requirement**: Mọi lịch sử can thiệp hạ tầng, snapshot trạng thái trước/sau xử lý, và payload khôi phục (`Audit trail`) phải được lưu giữ lưu trữ tập trung tối thiểu $\ge 90\text{ days}$ phục vụ kiểm toán tuân thủ.

---

## 5. Out of scope (Hạng mục Ngoài Phạm vi)

Để tập trung nguồn lực cốt lõi và tránh hiện tượng phình to phạm vi dự án (scope creep), các hạng mục sau được xác nhận nằm ngoài phạm vi phát triển:
* Dự báo chi phí đám mây tương lai (Forecasting future cost) và lập kế hoạch ngân sách tự động (Budget planning).
* Công cụ khuyến nghị mua gói RI/SP (RI/SP recommendation engine) hoặc tự động thực hiện giao dịch RI/SP (Auto-trade RI/SP).
* Tích hợp hoặc đồng bộ dữ liệu API với các nền tảng bên thứ ba thương mại (CloudHealth, Apptio, Vantage).
* Hỗ trợ đa tiền tệ (Multi-currency) – Hệ thống chỉ tính toán và hạch toán số liệu trên đơn vị tiền tệ USD (USD only).
* Cơ chế phát hiện thời gian thực luồng streaming dưới một giây (Real-time streaming detection sub-second).
* Xây dựng giao diện đăng ký đa người thuê tự phục vụ (Self-service tenant onboarding UI).
* Hạ tầng Multi-region Active-Active (Hệ thống chạy Batch chỉ triển khai Single-region tại Singapore, phần khắc phục sự cố DR chỉ dừng lại ở mặt thiết kế GitOps).

---

## 6. Non-functional requirements (Yêu cầu Phi chức năng)

* **SLO Platform & AI API Contract**:
    * P99 Latency của API Engine khi nhận yêu cầu xử lý ngầm từ CDO: $< 50\text{ms}$ (Đối với việc tiếp nhận Async Ingestion), và phản hồi kết quả polling từ DynamoDB Store: $< 10\text{ms}$.
    * SLA thời gian xử lý suy luận (gọi Bedrock API và ghi nhận kết quả): $< 30$ giây.
    * Availability của API Endpoint phục vụ CDon kết nối nội bộ: $\ge 99.5\%$.
    * Error Rate của hệ thống phản hồi API: $< 0.5\%$.
* **Multi-tenant Scale & Network Isolation**: Đảm bảo thiết kế logic định tuyến đa người thuê (`multi-tenant routing`) cô lập an toàn dữ liệu giữa các tenant thông qua thuộc tính định danh mã tài khoản (`X-Tenant-Id`). Đóng gói container chạy hoàn toàn trong Private Subnet, kết nối thông qua mạng Load Balancer nội bộ (Internal ALB), tuyệt đối cấm mở cổng public ra Internet.
* **Security Baseline & Idempotency Rules**:
    * Tuân thủ nguyên tắc đặc quyền tối thiểu (IAM Least Privilege), sử dụng IAM Execution Role gắn kèm task, cấm sử dụng Long-lived IAM Access Keys.
    * Toàn bộ thông tin nhạy cảm, API keys, AWS credentials phải được cấu hình và bóc tách tập trung qua AWS Secrets Manager.
    * Ép đính kèm Header `X-Idempotency-Key` định dạng Composite Key (`Time-bounded Composite Key`) có cấu hình TTL tự động hủy sau 24 giờ trên DynamoDB để triệt tiêu hoàn toàn thảm họa chạy lặp lại đè dữ liệu.
* **Cost Target**: Hệ thống tối ưu hóa dung lượng lưu trữ DynamoDB bằng cơ chế tự động dọn dẹp dữ liệu hết hạn (TTL 90 ngày cho Audit Log) và đẩy luồng stream nén dài hạn về bộ lưu trữ giá rẻ S3 Archive.

---

## 7. Open questions (Giải quyết Câu hỏi Mở)

* [x] **Q1:** Dữ liệu chi phí dùng để chạy hệ thống và backtest trong quá trình làm dự án lấy từ đâu?
    * *Resolved:* Client cung cấp tập dữ liệu hạch toán giả lập thực tế bao gồm file Daily vĩ mô (`cost_explorer_daily.csv`) và file CUR vi mô chi tiết (`cur_line_items.csv`) chứa dữ liệu lịch sử liên tục trong 3 tháng để chạy demo và chấm điểm thuật toán.
* [x] **Q2:** Giao diện Dashboard Finance mong muốn cụ thể hiển thị ra sao?
    * *Resolved:* Dashboard được phân rã giao diện người dùng dựa trên cấu trúc JSON đầu ra từ mô hình GenAI. Tách biệt khối Finance (Hiển thị biểu đồ tiền, câu báo cáo tóm tắt executive bằng ngôn ngữ tự nhiên) và khối Engineering Console (Hiển thị ARN thiết bị kỹ thuật, mã lệnh can thiệp Webhook và lý do kỹ thuật sâu).
* [x] **Q3:** Chu kỳ cập nhật file CUR trên S3 của môi trường giả lập có độ trễ (data lag) cố định là bao nhiêu tiếng?
    * *Resolved:* Dữ liệu CUR hạch toán có độ trễ tự nhiên từ AWS dao động từ 12-24 giờ. Vì vậy, hệ thống chốt hạ quy trình **Batch Job cố định chạy chu kỳ 24 giờ một lần vào khung giờ đêm (02:00 AM)**. Khi phát hiện AWS xuất dữ liệu CUR trễ muộn, CDO Platform sẽ tự động bắn tín hiệu `telemetry_delay_event` để AI Engine tạm hoãn tiến trình (Hold chu kỳ batch) sang trạng thái `SUSPENDED` và tự động kiểm tra lại sau mỗi 1 giờ, bảo đảm tính toàn vẹn và chính xác tuyệt đối cho baseline toán học của mô hình AI.