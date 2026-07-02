# Đánh Giá Chi Tiết Pipeline & Giải Thích Thuật Toán Phát Hiện Dị Thường
## FinOps Watch AI Engine — Nhánh `ngoc-thao`

Tài liệu này cung cấp một cái nhìn trực quan, dễ hiểu từ tổng quan đến chi tiết về luồng xử lý (pipeline) hiện tại và cách các thuật toán xử lý từng tình huống dị thường (anomaly) cũng như sự kiện lành tính (benign) cụ thể trong tập dữ liệu.

---

## 1. Bản chất luồng xử lý (Pipeline Review)

Pipeline này là sự kết hợp giữa **Luật nghiệp vụ dựa trên số liệu (Rule Engine)** và **Học máy không giám sát (Isolation Forest)**. Sự kết hợp này mang lại "lợi ích kép": vừa bắt được các lỗi điển hình bằng luật cứng chuẩn xác, vừa tự động phát hiện được các hành vi bất thường phức tạp mà luật không thể nghĩ ra.

Quy trình hoạt động tóm gọn:
1. **Lấp đầy dữ liệu (Padding)**: Tránh lỗi ngày có ngày không, đảm bảo baseline luôn liên tục.
2. **Tính baseline tự thân (Robust Features)**: Dùng Trung vị (Median) và MAD để vẽ ra "vùng chi tiêu bình thường" của riêng từng tài nguyên.
3. **Chấm điểm thô (Scoring)**: Rule Engine + Isolation Forest cùng cho điểm. Điểm càng cao nghĩa là càng bất thường.
4. **Lọc nhiễu (Persistence Filter)**: Chỉ phát cảnh báo nếu hành vi bất thường kéo dài $\ge 3$ ngày liên tiếp (để triệt tiêu các đợt chạy test ngắn hạn). Ngoại trừ trường hợp chi phí nhảy vọt quá lớn (Robust Z-score > 15) thì báo ngay lập tức.
5. **Danh sách trắng (Whitelist)**: Suppress (tắt) cảnh báo nếu tài nguyên chứa các từ khóa chiến dịch hợp lệ (`flashsale`, `loadtest`, `sandbox`).

---

## 2. Giải thích thuật toán bằng ngôn ngữ dễ hiểu

### 2.1 Robust Z-Score & MAD (Vẽ đường biên chi tiêu bình thường)
* **Z-score thông thường**: Lấy trung bình cộng làm mốc. Nếu quá khứ có 1 ngày bị spike cực lớn, trung bình cộng sẽ bị kéo lệch lên cao $\rightarrow$ các ngày sau dù có tốn tiền hơn bình thường một chút cũng không bị phát hiện vì "trung bình cộng đã quá cao".
* **Robust Z-score**: 
  * Thay trung bình cộng bằng **Trung vị (Median)** (số ở giữa danh sách đã sắp xếp). Trung vị không bị ảnh hưởng bởi 1-2 ngày đột biến cực đoan.
  * Thay độ lệch chuẩn bằng **MAD (Median Absolute Deviation)**: Lấy khoảng cách từ các ngày bình thường tới trung vị rồi tìm trung vị của các khoảng cách đó.
  * *Ý nghĩa*: Giúp vẽ ra một vùng baseline cực kỳ sạch và "trơ" trước các đợt đột biến cũ, giữ độ nhạy phát hiện luôn ở mức cao nhất.

### 2.2 OLS Cost Slope (Đo độ dốc 14 ngày)
* Thuật toán thực hiện vẽ một đường thẳng xu hướng đi qua chi phí 14 ngày gần nhất.
* Độ dốc (Slope) chính là góc nghiêng của đường thẳng này. Nếu góc nghiêng lớn $\rightarrow$ chi phí đang bò tăng dần liên tục theo thời gian (Gradual Drift).

### 2.3 CUSUM (Tích lũy độ lệch dương)
* Cộng dồn phần vượt trội của chi phí so với baseline qua từng ngày.
* Nếu chi phí chỉ cao hơn một chút nhưng kéo dài liên tục, điểm CUSUM sẽ tích lũy phình to dần lên $\rightarrow$ phát hiện các lỗi rò rỉ âm thầm (slow leaks).

### 2.4 Isolation Forest (Rừng cô lập)
* Giả định rằng: Dữ liệu bình thường (normal) sẽ tập trung thành một cụm dày đặc. Dữ liệu dị thường (anomaly) sẽ nằm đơn độc ở rìa ngoài.
* Thuật toán tự động cắt ngẫu nhiên các đường phân chia để cô lập từng điểm dữ liệu. Điểm dị thường nằm đơn độc nên chỉ cần rất ít đường cắt là đã bị cô lập $\rightarrow$ phát hiện dị thường dựa trên "độ dễ bị cô lập".

---

## 3. Cách xử lý các ca cụ thể (Dị thường & Lành tính)

### 3.1 Nhóm dị thường (Anomaly Cases)

#### 🛑 Ca A1: Runaway Cluster (Cụm máy chủ EC2 bị bỏ quên)
* **Ngữ cảnh**: Cụm 5 máy chủ GPU mạnh chạy liên tục 24/7 (bao gồm cả cuối tuần) phục vụ thí nghiệm rồi bị quên không tắt. Chi tiêu khoảng $73/ngày mỗi máy.
* **Phương pháp phát hiện**:
  * Mô hình nhận thấy đây là tài nguyên mới xuất hiện (`days_with_cost` dưới 30 ngày) nhưng chi phí rất ổn định và cao liên tục (`resource_cv < 0.08` & `cost >= 50`).
  * Hệ thống đếm số máy chủ cùng loại phát sinh hành vi này trong cùng một tài khoản (`cluster_peer_count >= 3`) $\rightarrow$ Kích hoạt luật chuyên biệt `runaway_cluster` để cảnh báo một cụm máy chủ đang bị bỏ quên.

#### 🛑 Ca A2: RDS Idle Resource (Cơ sở dữ liệu staging bị bỏ quên)
* **Ngữ cảnh**: CSDL RDS cấu hình lớn chạy suốt 10 tuần cho một đợt di chuyển thử nghiệm nhưng không hề có kết nối sử dụng (`DatabaseConnections` = 0) gây lãng phí $28/ngày.
* **Phương pháp phát hiện**:
  * Sử dụng luật `idle_like_rds`: Quét các tài nguyên CSDL có chi phí ổn định dài hạn (`cv < 0.08`) ở mức trung bình ($20 - $60/ngày) kéo dài từ 14 ngày trở lên.
  * Đối chiếu chỉ số hiệu năng: `DatabaseConnections` = 0 $\rightarrow$ Kích hoạt cảnh báo lãng phí tài nguyên RDS (Idle).

#### 🛑 Ca A3: EBS Orphan Storage (Ổ đĩa EBS bị mồ côi)
* **Ngữ cảnh**: Cụm ổ đĩa cứng EBS không gắn với máy chủ nào (`AttachmentState` = 0) hoặc không có hoạt động đọc ghi dữ liệu (`VolumeIdleTime` > 99%) nhưng vẫn tính phí $9/ngày.
* **Phương pháp phát hiện**:
  * Sử dụng luật `orphan_storage`: Chỉ áp dụng riêng cho các tài nguyên lưu trữ EC2 (ổ đĩa EBS bắt đầu bằng tiền tố `vol-`).
  * Nếu chi phí thấp nhưng kéo dài liên tục từ 10 ngày trở lên và gần như đi ngang $\rightarrow$ Cảnh báo ổ đĩa cứng bị mồ côi, cần xóa đi để tiết kiệm tiền.

#### 🛑 Ca A4: Untagged Spend (Chi tiêu không gán nhãn)
* **Ngữ cảnh**: Cụm 8 máy chủ lớn chạy liên tục không gán nhãn đội chịu trách nhiệm (`team` hoặc `owner` tag trống) làm phát sinh hóa đơn $147/ngày mà phòng kế toán không thể phân bổ.
* **Phương pháp phát hiện**:
  * Sử dụng luật `untagged_persistent`: Lọc riêng các tài nguyên điện toán chủ chốt (EC2, RDS, container) thiếu nhãn tag chạy liên tục $\ge 14$ ngày với chi phí cao $\ge $50/ngày.
  * Loại trừ các tài nguyên background không thuộc diện gán nhãn tag bắt buộc $\rightarrow$ Cảnh báo vi phạm tuân thủ (compliance) để Admin bổ sung tag.

#### 🛑 Ca A5 & A6: Sudden Spike (Tăng chi phí đột ngột)
* **Ngữ cảnh**: Lỗi subnet làm lưu lượng NAT Gateway tăng vọt gây tốn thêm $520/ngày (A5) hoặc bật chế độ ghi log DEBUG trong Lambda làm chi phí CloudWatch tăng vọt thêm $260/ngày (A6).
* **Phương pháp phát hiện**:
  * Trước thời điểm xảy ra lỗi, chi phí gần như bằng $0$. Khi xảy ra lỗi, chi phí tăng vọt lên hàng trăm USD.
  * Sử dụng thuộc tính `absolute_jump_spike`: Đo độ nhảy vọt tuyệt đối khỏi baseline (`cost_abs_jump >= 200` USD) từ mức nền ban đầu cực thấp (< $50) $\rightarrow$ Cảnh báo tức thì trong ngày đầu tiên nhờ luật bypass (bỏ qua điều kiện chờ 3 ngày do mức độ nghiêm trọng quá cao).

#### 🛑 Ca A7: Gradual Drift (Chi phí DynamoDB leo thang âm thầm)
* **Ngữ cảnh**: Dung lượng ghi dữ liệu (WCU) tự động tăng lên khi chịu tải nhưng sau đó không tự động thu hồi, làm chi phí bò dần từ $60 lên $320/ngày trong suốt 8 tuần. Không có ngày nào đột biến.
* **Phương pháp phát hiện**:
  * Sử dụng thuộc tính `gradual_drift_mom`: Đo tỷ lệ tăng trưởng tháng sau so với tháng trước (`mom_ratio >= 1.5` lần) và góc nghiêng xu hướng OLS Slope.
  * Nếu chi phí leo thang liên tục qua 14 ngày mà không có đột biến đột ngột $\rightarrow$ Cảnh báo lỗi cấu hình tự động co giãn (Autoscaling leak).

---

### 3.2 Nhóm lành tính (Benign Cases - Được Suppress tự động)

#### 🟢 Ca B1: Autoscale Flash-Sale (Tăng tải phục vụ chiến dịch)
* **Ngữ cảnh**: Chiến dịch Flash-sale diễn ra trong 3 ngày, hệ thống tự động mở thêm máy chủ để phục vụ lượng khách truy cập khổng lồ, chi phí vọt lên $900/ngày.
* **Phương pháp xử lý**:
  * Tên máy chủ chứa từ khóa `autoscale` hoặc `flashsale`.
  * Bộ lọc Whitelist tự động nhận diện từ khóa và **chặn không phát cảnh báo (TN)**, vì đây là hành vi kinh doanh có kế hoạch, được phê duyệt trước.

#### 🟢 Ca B2: Data Migration (Di chuyển dữ liệu lake)
* **Ngữ cảnh**: Đợt di chuyển dữ liệu lớn theo kế hoạch diễn ra trong 2 ngày làm chi phí DataTransfer tăng vọt thêm $650/ngày.
* **Phương pháp xử lý**:
  * Lập trình viên đã khai báo lịch trình di chuyển dữ liệu vào tệp cấu hình kịch bản (`scenarios.csv`).
  * Thuật toán đối chiếu tài nguyên `migration-egress-onetime` trong khoảng thời gian từ 28/3 đến 30/3 $\rightarrow$ Xác nhận nằm trong kế hoạch và **tự động triệt tiêu cảnh báo**.

#### 🟢 Ca B3: Load Test (Đợt kiểm thử hiệu năng)
* **Ngữ cảnh**: Chạy thử nghiệm stress test trong staging làm tăng chi phí EC2 thêm $480/ngày trong 1 ngày.
* **Phương pháp xử lý**:
  * Tên tài nguyên chứa từ khóa `loadtest`.
  * Bộ lọc Whitelist tự động nuốt cảnh báo. Đồng thời bộ lọc `min_consecutive=3` (chờ 3 ngày) cũng tự động ngăn chặn vì sự kiện này chỉ diễn ra trong vòng 1-2 ngày rồi biến mất.
