# Giải Pháp Phân Biệt Benign và Anomaly Không Dùng Tên (CPCI Method)
## Q&A Slide: Đối phó với Dữ liệu Thiếu Tag/Metadata

---

## 🖥️ Slide: Chỉ số Đồng bộ Chi phí - Hiệu năng (CPCI)

### 📊 Bảng so sánh Telemetry (Bản chất vật lý):

| Loại sự kiện | Chi phí ($\Delta \text{Cost}$) | Hiệu năng ($\Delta \text{CPU / Connections}$) | Chỉ số CPCI | Quyết định của AI |
|---|---|---|---|---|
| **Benign Spike** (Autoscale / Flashsale) | Tăng vọt 📈 | Tăng vọt tương xứng 📈 | **$\approx 1$ (Đồng bộ)** | **Mute (Nuốt)** 🔕 |
| **Benign Migration** (Di chuyển dữ liệu) | Tăng vọt 📈 | Network Out vọt lên 📈 | **$\approx 1$ (Đồng bộ)** | **Mute (Nuốt)** 🔕 |
| **Anomaly Spike** (NAT misconfig / CW Leak) | Tăng vọt 📈 | CPU / Connections $\approx 0$ 📉 | **$\approx 0$ (Lệch pha)** | **Báo động** 🚨 |
| **Anomaly Idle** (CSDL staging bị bỏ quên) | Duy trì cao ➡️ | Connections = 0 📉 | **$\approx 0$ (Lệch pha)** | **Báo động** 🚨 |

---

### 🎙️ Kịch bản nói (Talking Points):
*“Thưa hội đồng, để hệ thống FinOps hoạt động thực chất trên các hạ tầng lớn - nơi tag và tên tài nguyên thường xuyên bị đặt sai hoặc bị thiếu - nhóm chúng em đề xuất giải pháp **Chỉ số Đồng bộ Chi phí - Hiệu năng (CPCI)** dựa trên dữ liệu từ file `metrics.csv`:*

* *Thay vì dùng rule cứng Whitelist theo tên, AI Engine sẽ tính toán **độ tương quan động (Sliding correlation)** giữa chuỗi thời gian chi phí và chuỗi thời gian hiệu năng của chính tài nguyên đó.*
* *Nếu chi phí tăng mà CPU hoặc Connections cũng tăng tương ứng (CPCI gần bằng 1), AI tự động nhận diện đây là hành vi co giãn tự nhiên (Autoscale) và **im lặng**.*
* *Ngược lại, nếu chi phí nhảy vọt nhưng CPUUtilization hay Connections vẫn đi ngang ở đáy (CPCI gần bằng 0), AI sẽ phát báo động lãng phí ngay lập tức.*
* *Phương pháp toán học này giúp hệ thống tự đứng vững độc lập, không phụ thuộc vào con người gán tag đúng hay sai.”*
