# Xây dựng hệ thống khoa học phân tích & dự đoán ô nhiễm đô thị

> **Điểm cộng khủng trước hội đồng:** ngoài đo lường, hệ thống bổ sung mô-đun **AI dự đoán PM2.5/PM10 trong 30–60 phút** và **phát hiện bất thường** (sensor hư, đột biến chất lượng không khí). Đề tài thăng hạng từ *“hệ thống đo”* thành *“nền tảng khoa học phân tích & cảnh báo chủ động”*.

## 1. Tổng quan đề tài

- **Sinh viên thực hiện:** Dương Vũ Khôi Nguyên – MSSV 21093401 – Email: khoi.nguyenust@gmail.com – SĐT: 032 686 4753  
- **Phần cứng:** ESP32 30 pin + cảm biến bụi Plantower PMS7003 + cảm biến khí MQ135 + Raspberry Pi 4 + mạch PCB tuỳ chỉnh (kích thước 58x68 mm).
- **Mục tiêu:** 
  - Giám sát nồng độ PM1.0, PM2.5, PM10 và khí độc (gas) theo thời gian thực.
  - Lưu trữ dữ liệu lịch sử trên InfluxDB, hiển thị dashboard trực quan (Grafana).
  - **Dự đoán xu hướng ô nhiễm 30–60 phút** với mô hình LSTM/GRU/Random Forest.
  - **Phát hiện bất thường** (Isolation Forest / Autoencoder) để nhận diện sensor lỗi, giá trị đột biến, hoặc sự kiện ô nhiễm bất thường.
  - Cảnh báo realtime qua Firebase Cloud Messaging khi chỉ số vượt ngưỡng.

## 2. Các thành phần chính

| Thành phần | Mô tả |
|------------|-------|
| `firmware/` | Mã nguồn ESP32 đọc PMS7003 + MQ135, publish MQTT định dạng JSON. |
| `backend/` | Backend chạy trên Raspberry Pi: ingest MQTT → InfluxDB, API FastAPI, mô hình dự đoán (LSTM/GRU/RF), mô hình bất thường (Isolation Forest/Autoencoder), scheduler tự động huấn luyện. |
| `training/` | Script CLI huấn luyện/retrain mô hình từ dữ liệu trong InfluxDB. |
| `edge/raspberry_pi/` | Docker-compose cho Pi (Mosquitto + InfluxDB + Grafana + Backend AI). |
| `demo.ipynb`, `model.ipynb` | Notebook trống để bạn log dữ liệu/mô phỏng (tuỳ chỉnh thêm). |

```text
.
├── firmware/
│   └── esp32_air_monitor/esp32_air_monitor.ino
├── backend/
│   ├── api/main.py                 # FastAPI REST
│   ├── config.py                   # Đọc .env
│   ├── ingestion/mqtt_listener.py  # Bridge MQTT → Influx
│   ├── models/                     # LSTM, GRU, RandomForest, IsolationForest, Autoencoder
│   ├── services/                   # Forecasting & anomaly services
│   ├── tasks/scheduler.py          # APScheduler tự động huấn luyện & kiểm tra bất thường
│   └── requirements.txt
├── training/
│   ├── train_forecasting.py
│   └── train_anomaly.py
├── edge/raspberry_pi/
│   ├── docker-compose.yml
│   ├── backend.Dockerfile
│   ├── backend.env
│   └── influxdb.env
└── README.md
```

## 3. Luồng hoạt động tổng quát

1. **ESP32** đọc PMS7003 (UART) + MQ135 (ADC) mỗi 10 giây, publish JSON qua MQTT (`sensors/airquality`).
2. **MQTT bridge trên Raspberry Pi** (`backend/ingestion/mqtt_listener.py`) subscribe → ghi dữ liệu vào InfluxDB.
3. **Grafana** kết nối InfluxDB để hiển thị dashboard lịch sử và realtime.
4. **API FastAPI** cung cấp:
   - `/metrics/latest`, `/metrics/history` – tra cứu dữ liệu hiện tại/lịch sử.
   - `/forecast` – trả về dự đoán PM2.5 cho 30–60 phút tới.
   - `/anomalies` – trả về danh sách bất thường (sensor lỗi, đột biến…).
5. **Scheduler** tự động huấn luyện mô hình (mặc định 6 giờ/lần) và kiểm tra bất thường mỗi 15 phút. Bạn có thể trigger thủ công qua endpoint hoặc script trong `training/`.
6. **Firebase Cloud Messaging (tuỳ chọn)**: tích hợp vào app Flutter để gửi push notification khi API phát hiện vượt ngưỡng hoặc anomaly.

## 4. Tính năng nổi bật (thuyết phục hội đồng)

- **Dự đoán xu hướng ô nhiễm (PM2.5, PM10) 30–60 phút:**  
  - Mô hình chuỗi thời gian LSTM/GRU tùy chọn Random Forest Regressor.  
  - Input: dữ liệu quá khứ từ InfluxDB (PM2.5, PM10, Gas, PM1.0, timestamp).  
  - Output: dự đoán PM2.5 tương lai → hỗ trợ ra quyết định sớm (cảnh báo khi chuẩn bị vượt chuẩn).

- **Phát hiện bất thường (Anomaly Detection):**  
  - **Isolation Forest:** phát hiện điểm dị biệt do sensor lỗi hoặc môi trường.  
  - **Autoencoder:** đo sai số tái tạo để bắt tín hiệu bất thường tinh vi.  
  - Ứng dụng thực tế: cảnh báo sensor hỏng, cháy, kẹt xe, bụi thi công đột biến.

- **Nâng cấp kế hoạch thực hiện:**  
  - Bổ sung milestone huấn luyện mô hình AI (10/07–20/08) và báo cáo kết quả dự đoán/anomaly vào giữa kỳ.  
  - Kết quả demo gồm dashboard Grafana + ứng dụng di động hiển thị dự đoán & cảnh báo.

## 5. Hướng dẫn triển khai

### 5.1. Firmware ESP32
1. Cài Arduino IDE hoặc PlatformIO, thêm board ESP32.
2. Sửa thông tin WiFi & MQTT trong `firmware/esp32_air_monitor/esp32_air_monitor.ino`.
3. Cài thư viện: `PubSubClient`, `ArduinoJson`, `ESP32 board defs`, `PMS7003` (nếu cần).
4. Nạp code và kiểm tra Serial Monitor (115200 baud) để xem log.

### 5.2. Raspberry Pi 4 (backend + dữ liệu)
1. Cài Docker & Docker Compose.
2. Chỉnh sửa token, mật khẩu trong `edge/raspberry_pi/*.env` (đặc biệt `INFLUX_TOKEN`).
3. Deploy stack:
   ```bash
   cd edge/raspberry_pi
   docker compose up -d
   ```
4. Tạo file `.env` tại gốc (copy nội dung `backend.env` nếu chạy ngoài Docker).
5. Chạy bridge MQTT (nếu muốn chạy ngoài container):
   ```bash
   python -m backend.ingestion.mqtt_listener
   ```
6. Khởi chạy API:
   ```bash
   uvicorn backend.api.main:app --host 0.0.0.0 --port 8000
   ```

### 5.3. Huấn luyện mô hình
```bash
# Huấn luyện LSTM 30 phút
python training/train_forecasting.py --model-type lstm --horizon 30 --history 1440

# Huấn luyện Random Forest 60 phút
python training/train_forecasting.py --model-type random_forest --horizon 60

# Huấn luyện Isolation Forest
python training/train_anomaly.py --detector-type isolation_forest --history 1440

# Huấn luyện Autoencoder
python training/train_anomaly.py --detector-type autoencoder --history 2880
```
Các mô hình được lưu trong `backend/artifacts/` để API sử dụng.

### 5.4. Sử dụng API
- Swagger UI: `http://<raspberry-ip>:8000/docs`
- Endpoints quan trọng:
  - `GET /forecast?horizons=30&horizons=60`
  - `POST /forecast/train`
  - `GET /anomalies`
  - `POST /anomalies/train`

## 6. Dashboard & ứng dụng di động

- **Grafana:** tạo dashboard hiển thị realtime (PM1.0/2.5/10, Gas, predicted PM2.5, flags anomaly).
- **Flutter app (định hướng):**
  - Gọi REST API để lấy dữ liệu & dự đoán.
  - Đăng ký Firebase Cloud Messaging để nhận thông báo khi `prediction > ngưỡng` hoặc `anomaly == true`.
  - Hiển thị lịch sử 24h, biểu đồ line, danh sách cảnh báo.

## 7. Kế hoạch thực hiện (cập nhật)

| STT | Công việc | Sản phẩm | Thời gian |
|-----|-----------|----------|-----------|
| 1 | Viết & bảo vệ đề cương | Tài liệu PL2, slide | 30/05 – 05/06/2025 |
| 2 | Nghiên cứu lý thuyết | MQTT, IoT, PMS7003, MQ135, InfluxDB, Grafana, Flask/REST, Firebase, ML time-series | 06/06 – 20/06/2025 |
| 3 | Chế tạo phần cứng | PCB, ESP32, sensor, mô hình | 20/06 – 10/07/2025 |
| 4 | Xây dựng REST & pipeline dữ liệu | Flask/FastAPI, MQTT↔Influx | 10/07 – 20/07/2025 |
| **5** | **Huấn luyện mô hình dự đoán 30–60 phút (LSTM/GRU/RF)** | Script `training/train_forecasting.py`, artifact lưu Influx | **20/07 – 10/08/2025** |
| **6** | **Phát hiện bất thường (Isolation Forest / Autoencoder)** | Dịch vụ anomaly, dashboard hiển thị | **20/07 – 10/08/2025** |
| 7 | Ứng dụng di động + FCM | App Flutter, push notification | 20/07 – 10/08/2025 |
| 8 | Triển khai thực tế (3 vị trí) | Dữ liệu thực tế, report so sánh | 10/08 – 15/08/2025 |
| 9 | Báo cáo giữa kỳ | File báo cáo, slide, demo AI dự đoán + anomaly | 25/08 – 08/09/2025 |
| 10 | Báo cáo kiểm tra trùng lặp | Bản thảo hoàn chỉnh | 10/12 – 20/12/2025 |
| 11 | Bảo vệ khóa luận | Slide, demo, app | 25/12 – 31/12/2025 |

## 8. Công nghệ và thư viện

- **Firmware:** ESP-IDF / Arduino, PubSubClient, ArduinoJson.
- **Edge & Backend:** Python 3.11, FastAPI, Uvicorn, InfluxDB Client, Paho MQTT, APScheduler.
- **AI:** PyTorch (LSTM/GRU Autoencoder), Scikit-learn (RandomForest, IsolationForest), pandas, numpy.
- **DevOps:** Docker Compose (Mosquitto, InfluxDB, Grafana, Backend).

## 9. Hướng mở rộng

- Tự động sinh cảnh báo bằng SMS/Zalo OA.
- Tích hợp thêm cảm biến môi trường (nhiệt độ, độ ẩm, tiếng ồn).
- Deploy mô hình lên cloud để tổng hợp nhiều điểm đo.
- Huấn luyện lại mô hình hằng ngày với pipeline Airflow.

---
**Liên hệ:** khoi.nguyenust@gmail.com – Sẵn sàng chia sẻ thêm schematic PCB và thiết kế enclosure 3D khi cần.
