# Giải thích Bối cảnh FinOps Watch (TF2)

Tài liệu này giải đáp các câu hỏi làm rõ bối cảnh dự án được nêu trong [TF2_FINOPS_LEARNER.md](file:///C:/Users/ASUS/OneDrive/Obsidian Vault/XBrain-Phase2/Capstone_Project/reference/TF2_FINOPS_LEARNER.md).

---

### 1. AWS Organizations đã set up nhưng chưa tight có nghĩa là sao?
Trong môi trường cloud (cụ thể là AWS), **AWS Organizations** là dịch vụ dùng để quản lý tập trung và phân quyền cho nhiều tài khoản AWS con (multi-account) của doanh nghiệp.
* **"Đã set up"**: Công ty đã khởi tạo AWS Organizations và chia hệ thống thành nhiều tài khoản con (ví dụ: tài khoản cho môi trường Dev, Staging, Production, hoặc tài khoản riêng cho từng Squad).
* **"Nhưng chưa tight" (chưa chặt chẽ/chưa tối ưu)**: Nghĩa là họ thiếu các chính sách quản trị, giám sát và kiểm soát chặt chẽ (Guardrails/Governance). Cụ thể:
  * **Thiếu Service Control Policies (SCPs)** để giới hạn các hành vi nguy hiểm hoặc hạn chế việc tạo các tài nguyên siêu đắt đỏ ở môi trường thử nghiệm (Sandbox/Dev).
  * **Thiếu quy định bắt buộc về gắn thẻ tài nguyên (Tagging Strategy)**: Dẫn đến việc khi có chi phí phát sinh bất thường, không biết tài nguyên đó do ai tạo hay thuộc dự án nào (như việc dev quên tắt training cluster đốt tiền suốt 18 ngày).
  * **Thiếu cảnh báo hạn mức (Budget alerts/Cost caps)** tự động ở cấp độ tổ chức để phát hiện và ngăn chặn ngay lập tức.

---

### 2. 80 người chia thành 12 squad thì squad là gì và chia như thế nào?
* **Squad là gì?**
  * **Squad** (mô hình phổ biến từ Spotify) là một nhóm làm việc nhỏ, liên chức năng (cross-functional) và tự chủ (autonomous). 
  * Mỗi Squad thường chịu trách nhiệm trọn vẹn (end-to-end) cho một tính năng, dịch vụ hoặc cấu phần cụ thể của sản phẩm (ví dụ: Squad phụ trách Payment, Squad phụ trách Recommendation Engine...).
  * Thành phần của một Squad thường bao gồm đầy đủ các vai trò cần thiết để hoàn thành công việc độc lập: Product Owner, Developers (Frontend, Backend), QA/Testers và DevOps/SRE.
* **Chia như thế nào trong bối cảnh này?**
  * Với tổng quy mô kỹ sư khoảng ~80 người chia thành 12 squad, trung bình mỗi squad sẽ có khoảng **6 đến 7 thành viên** (80 / 12 ≈ 6.6 người) – đây là quy mô lý tưởng cho một nhóm agile hoạt động hiệu quả (quy tắc "two-pizza team").
  * **Về mặt hạ tầng cloud**: Lý thuyết là mỗi squad nên sở hữu tài nguyên/tài khoản AWS riêng biệt để tự quản lý và chịu trách nhiệm về mặt chi phí. Tuy nhiên, do cấu trúc chưa "tight" nên ranh giới sở hữu tài nguyên giữa các squad bị mờ nhạt, khiến Finance team phải mất cả tuần mới tìm ra "thủ phạm" làm tăng hóa đơn.

---

### 3. Dashboard riêng là như thế nào, console riêng là như thế nào, chúng khác nhau như thế nào?
Đây là hiện trạng phân mảnh thông tin giữa hai bộ phận trong công ty:
* **Dashboard riêng (Finance team):**
  * Là giao diện trực quan hóa dữ liệu tài chính (thường xây dựng qua QuickSight, Tableau, PowerBI...). Giao diện này chỉ hiển thị dữ liệu tổng hợp dạng biểu đồ, số tiền (USD), xu hướng tăng giảm chi phí theo tháng/tuần.
  * Đây là hệ thống **Read-only (chỉ đọc)**, Finance team không thể can thiệp hay cấu hình hạ tầng AWS từ đây.
* **Console riêng (Engineering team):**
  * Là giao diện quản trị trực tiếp của AWS (**AWS Management Console**). Kỹ sư sử dụng console này để tạo, chỉnh sửa, giám sát hoạt động và xóa các tài nguyên hệ thống (EC2 instances, databases, clusters...).
  * Đây là hệ thống **Read-Write (quản trị trực tiếp)**, tập trung vào khía cạnh vận hành kỹ thuật (hiệu năng, uptime, cấu hình) hơn là chi phí.
* **Sự khác biệt cốt lõi:**
  | Tiêu chí | Dashboard riêng (Finance) | Console riêng (Engineering) |
  | :--- | :--- | :--- |
  | **Đối tượng sử dụng** | Nhân viên Tài chính (Finance / CFO) | Kỹ sư phần mềm (Engineers / DevOps) |
  | **Góc nhìn chính** | Tiền tệ ($), Ngân sách (Budget), Xu hướng chi phí | Hạ tầng kỹ thuật, CPU/RAM, Uptime, Deployments |
  | **Mục đích** | Báo cáo chi phí định kỳ, kiểm soát ngân sách | Vận hành hệ thống và phát triển tính năng |
  | **Khả năng can thiệp** | Không thể tác động đến tài nguyên cloud | Có toàn quyền tạo/xóa tài nguyên phát sinh chi phí |

Sự phân tách này tạo ra một "silo" (hố ngăn cách): **Finance nhìn thấy tiền tăng nhưng không biết tắt cái gì, còn Engineer có quyền tắt nhưng lại không nhìn thấy chi phí đang tăng hàng ngày.**

---

### 4. Giải thích "Không nhìn vào cost continuous, chỉ weekly snapshot".
Cụm từ này mô tả việc **thiếu giám sát chi phí theo thời gian thực** (real-time/daily monitoring) mà chỉ xem báo cáo tĩnh định kỳ vào cuối tuần.

* **Cost Continuous (Theo dõi chi phí liên tục):**
  * Là hoạt động thu thập, phân tích dữ liệu chi phí và phát hiện bất thường diễn ra liên tục hàng ngày hoặc theo chu kỳ ngắn (ví dụ: mỗi 12h/24h/48h). 
  * Nếu phát sinh một cluster tiêu tốn $400/ngày, hệ thống giám sát continuous sẽ phát hiện ra ngay lập tức trong vòng 24 giờ và gửi cảnh báo để xử lý sớm.
* **Weekly Snapshot (Ảnh chụp nhanh hàng tuần):**
  * Là một báo cáo tĩnh (file PDF, email tóm tắt hoặc biểu đồ chốt số) xuất ra một lần duy nhất vào cuối tuần để tổng kết chi phí của tuần đó.
  * Nó không phản ánh sự thay đổi động theo từng ngày và không có cơ chế cảnh báo tức thì.
* **Ý nghĩa của câu "Không nhìn vào cost continuous, chỉ weekly snapshot":**
  * Cả Finance và Engineering đều không chủ động theo dõi biến động chi phí hàng ngày. Họ chỉ mở báo cáo tổng kết ra xem một tuần một lần.
  * **Hệ quả thực tế:** Khi một developer quên tắt training cluster vào thứ Ba (đốt $400/ngày), không ai hay biết vào thứ Tư, thứ Năm hay thứ Sáu. Phải đợi đến kỳ xuất báo cáo tuần (Weekly Snapshot) tiếp theo, hoặc thậm chí là hóa đơn cuối tháng, Finance mới phát hiện ra một cột chi phí tăng đột biến. Kể từ lúc phát hiện trên báo cáo tuần đến lúc tìm ra nguyên nhân và dập tắt nó lại mất thêm một khoảng thời gian nữa (như trong bài là mất 18 ngày, tốn oan $7k).
