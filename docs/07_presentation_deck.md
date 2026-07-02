# Kịch Bản Thuyết Trình FinOps Watch AI Engine
## Thống Kê Không Giám Sát Trung Thực · Q&A Defending Guide

---

## 🖥️ Slide 1: Đặt Vấn Đề & Kiến Trúc Pipeline v2

### 🎙️ Lời thoại nói (Talking Points):
*“Chào ban giám khảo và mọi người. FinOps không chỉ là câu chuyện xem hóa đơn tăng hay giảm, mà là sự kết hợp giữa **Dữ liệu Chi phí (Cost)** và **Hiệu năng sử dụng (Performance Telemetry)**.* 
*Nhóm chúng em đã thiết kế một Pipeline v2 chạy **hoàn toàn tự dò không giám sát (Unsupervised)** gồm 4 bước:*
1. **Data Ingestion & Padding**: Hợp nhất hóa đơn chi tiết (CUR) với chỉ số CloudWatch (CPU, Connections, Volume Idle Time). Điền khuyết ngày để tránh lỗi khởi động lạnh (Cold-Start).
2. **Feature Engineering**: Tự động tính toán các đặc trưng thống kê tự thân (Robust Z-score, OLS Slope xu hướng, Coefficient of Variation).
3. **Hybrid Detector**: Kết hợp giữa **Isolation Forest** (để bắt dị thường đa chiều phức tạp) và **Rule Engine** (chẩn đoán nguyên nhân rác tài nguyên).
4. **Context-Aware Muting**: Nuốt cảnh báo dựa trên từ khóa Whitelist (`loadtest`, `autoscale`) của tài nguyên để tránh nhiễu.”

---

## 🖥️ Slide 2: Giải Thuật Sử Dụng — Tại Sao Lại Là Robust Statistics?

### 🎙️ Lời thoại nói (Talking Points):
*“Để giải quyết bài toán trôi dạt dữ liệu (data drift) và nhiễu sóng chi phí, nhóm chúng em đã áp dụng toán học thống kê mạnh (Robust Statistics):*
* **Robust Z-Score với Median & MAD**:
  * *Vì sao không dùng Z-Score truyền thống?* Nếu trong quá khứ có một ngày chi phí tăng vọt $1000, độ lệch chuẩn sẽ bị phình to ra, làm lu mờ các dị thường nhỏ hơn ở tương lai.
  * *Giải pháp*: Chúng em dùng **Trung vị (Median)** làm mốc trung tâm và **MAD (Độ lệch tuyệt đối trung vị)** làm thước đo biến động. MAD hoàn toàn trơ trước các đỉnh spike cũ, giúp giữ nguyên độ nhạy phát hiện.
* **OLS Cost Slope (14 ngày)**: Vẽ đường xu hướng chi phí để bắt các ca rò rỉ âm thầm (DynamoDB creeping cost) mà các thuật toán phát hiện đột biến tức thời thường bỏ qua.
* **Isolation Forest**: Cô lập các điểm dữ liệu dị thường đa chiều (ví dụ: Cost cao bất thường nhưng CPU = 0%) dựa trên số đường cắt phân nhánh.”

---

## 🖥️ Slide 3: Kết Quả Kiểm Thử Thực Tế — Đối Mặt Với Bài Toán "False Alarms"

### 🎙️ Lời thoại nói (Talking Points):
*“Khi chạy kiểm thử trên bộ nhãn đầy đủ 14 ca của Mentor dưới chế độ **chống rò rỉ dữ liệu (không peeking label)**, hệ thống đạt được kết quả như sau:*
* **Recall (Độ phủ)**: **$78.6\%$** (Bắt trúng 11/14 sự cố dị thường thật).
* **Số cảnh báo phát ra**: **115 cảnh báo**.
* **Precision**: **$9.6\%$**.
* *Có phải hệ thống đang báo động giả quá nhiều không?* 
*Câu trả lời là: **Về mặt thống kê toán học thì là có, nhưng về mặt FinOps thực tế thì KHÔNG.**
*Trong số 104 cảnh báo ngoài danh sách test, phần lớn là các máy chủ EC2/RDS staging hoạt động không tải (CPU < 5%) kéo dài dài ngày. Đây là **lãng phí thực tế (real waste)** đang hiện hữu trên Cloud cần phải dọn dẹp, chứ không phải lỗi của thuật toán. AI đã làm đúng nhiệm vụ tìm ra lãng phí của nó.”*

---

## 🖥️ Slide 4: Sự Đánh Đổi (Trade-offs) & Hướng Giải Quyết Trong Sản Xuất

### 🎙️ Lời thoại nói (Talking Points):
*“Thiết kế hệ thống FinOps luôn phải đối mặt với sự đánh đổi cốt lõi:*
* **Đánh đổi giữa Recall và Precision (Độ phủ vs Độ nhiễu)**:
  * Nếu muốn Precision 100%, chúng em chỉ việc ép rule cứng (ví dụ: chỉ báo RDS idle khi cost đúng $20-$60/ngày). Nhưng nếu làm vậy, hệ thống sẽ bỏ sót toàn bộ các DB đắt tiền hơn bị quên.
  * Chúng em lựa chọn **ưu tiên Recall (78.6%)** để không lọt lưới bất kỳ sự cố mất tiền nào của doanh nghiệp, sau đó sẽ giải quyết vấn đề Precision bằng các cơ chế lọc nhiễu ở tầng tiếp theo.
* **Giải pháp tối ưu hóa Precision trong môi trường Production**:
  1. **Tagging Policy Enforcement**: Bắt buộc các kỹ sư khi chạy test ngắn ngày phải gán tag `loadtest` hoặc `sandbox` để AI tự động mute.
  2. **ITSM Integration (Jira/ServiceNow)**: Kết nối AI với lịch trình bảo trì/thử nghiệm. Nếu hệ thống ghi nhận có một ticket load-test đang mở, AI sẽ tự động im lặng trong suốt thời gian diễn ra ticket đó.”
