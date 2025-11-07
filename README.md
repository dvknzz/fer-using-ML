# Hệ thống giám sát – phân tích – dự đoán ô nhiễm không khí thời gian thực

Dự án cung cấp toàn bộ phần mềm cho đề tài **"Xây dựng hệ thống giám sát ô nhiễm tại một khu vực trong đô thị"** sử dụng phần cứng ESP32 + cảm biến Plantower PMS7003 và MQ135, Raspberry Pi 4 làm gateway. Hệ thống đã được mở rộng thành nền tảng phân tích khoa học nhờ các mô hình dự đoán chất lượng không khí (PM2.5/PM10) cho 30–60 phút tiếp theo và chức năng phát hiện bất thường (anomaly detection) để nhận diện cảm biến lỗi, giá trị đột biến hoặc sự cố ô nhiễm tăng đột ngột.

## Kiến trúc tổng thể

```
ESP32 (PMS7003 + MQ135) → MQTT → Raspberry Pi 4 → InfluxDB + Flask API → Ứng dụng Web/Mobile (Grafana, Android, ...)
                                             ↘ ML Pipeline (Forecasting + Anomaly Detection)
```

1. **ESP32** đọc dữ liệu PM1.0/PM2.5/PM10 từ PMS7003, khí gas từ MQ135, gửi JSON qua MQTT.
2. **Raspberry Pi 4** chạy collector Python, lắng nghe MQTT và ghi dữ liệu vào InfluxDB.
3. **ML pipeline** (TensorFlow + Scikit-learn) huấn luyện mô hình LSTM/GRU/Random Forest dự báo PM2.5 trong 30–60 phút, đồng thời huấn luyện Isolation Forest hoặc Autoencoder cho phát hiện bất thường.
4. **Flask REST API** cung cấp endpoint đọc dữ liệu lịch sử, dự đoán tương lai, và danh sách bất thường để tích hợp với Grafana, ứng dụng Android, hoặc hệ thống cảnh báo.

## Cấu trúc thư mục

```
.
├── firmware/esp32/                # Mã nguồn PlatformIO cho ESP32
│   ├── platformio.ini
│   └── src/main.cpp
├── raspberry_pi/collector/        # Collector MQTT → InfluxDB
│   ├── collector.py
│   └── requirements.txt
├── backend/api/                   # REST API dự báo + anomaly detection
│   ├── app.py
│   └── requirements.txt
├── ml/pipeline/                   # Script huấn luyện mô hình dự báo & bất thường
│   ├── train_forecast.py
│   ├── train_anomaly.py
│   └── utils.py
├── ml/models/                     # Thư mục lưu mô hình sau khi huấn luyện (joblib/keras)
├── configs/collector.example.yaml # Cấu hình mẫu cho collector
├── demo.ipynb, model.ipynb        # Notebook gốc (tham khảo)
└── README.md                      # Tài liệu này
```

## 1. Firmware ESP32

- Khởi tạo Wi-Fi, MQTT (PubSubClient) và đọc cảm biến PMS7003 (thư viện `PMS`) cùng MQ135 (đo ADC và quy đổi PPM).
- Định dạng payload JSON:

```json
{
  "device_id": "esp32-air-quality",
  "timestamp": 123456789,
  "pm1_0": 8,
  "pm2_5": 15,
  "pm10": 23,
  "pm2_5_cf": 16,
  "pm10_cf": 25,
  "mq135_ppm": 75.3,
  "mq135_raw": 1800
}
```

- Chu kỳ gửi mặc định: 30 giây (có thể chỉnh `PUBLISH_INTERVAL_MS`).
- Cần hiệu chuẩn MQ135 để xác định thông số `RO` chính xác.

**Triển khai**
1. Cài [PlatformIO IDE](https://platformio.org/).
2. Mở thư mục `firmware/esp32`, chỉnh thông tin Wi-Fi, MQTT trong `src/main.cpp`.
3. `pio run -t upload` để nạp firmware.

## 2. Raspberry Pi Collector

Collector nhận MQTT rồi ghi vào InfluxDB bằng client chính thức.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r raspberry_pi/collector/requirements.txt
cp configs/collector.example.yaml configs/collector.yaml
nano configs/collector.yaml  # chỉnh IP MQTT, token InfluxDB
python raspberry_pi/collector/collector.py configs/collector.yaml
```

Collector sử dụng buffer ghi theo lô, chống mất dữ liệu khi mạng chập chờn.

## 3. CSDL InfluxDB & trực quan hóa

- Tạo bucket `air_quality`, lưu token vào biến môi trường `INFLUXDB_TOKEN` (dùng chung cho collector, ML pipeline và API).
- Sử dụng Grafana để trực quan hóa: kết nối datasource InfluxDB, tạo dashboard hiển thị PM2.5/PM10, trạng thái cảnh báo.

## 4. Huấn luyện mô hình dự báo PM2.5 (30–60 phút)

Script `ml/pipeline/train_forecast.py` hỗ trợ 3 kiến trúc:
- `--model lstm` (mặc định): mạng LSTM 2 tầng.
- `--model gru`: mạng GRU 2 tầng.
- `--model random_forest`: RandomForestRegressor (scikit-learn) sử dụng đặc trưng chuỗi thời gian đã flatten.

Các bước:

```bash
cd ml/pipeline
python train_forecast.py --model lstm --lookback 60 --horizon 30
# hoặc dùng dữ liệu đã thu thập lưu CSV
python train_forecast.py --model random_forest --csv data/air_quality.csv --horizon 45
```

- `lookback`: số phút lịch sử cho mỗi mẫu huấn luyện (mặc định 60 phút).
- `horizon`: mốc dự đoán (30, 45, 60 phút).
- Mô hình được lưu tại `ml/models/pm25_forecaster.joblib`. Với LSTM/GRU, kiến trúc keras lưu ở file `.keras` kèm metadata joblib.

## 5. Huấn luyện mô hình phát hiện bất thường

```bash
cd ml/pipeline
python train_anomaly.py --model isolation_forest
# hoặc Autoencoder
python train_anomaly.py --model autoencoder --csv data/air_quality.csv
```

- Isolation Forest: tự động phân loại điểm bất thường (`predict = -1`).
- Autoencoder: tái tạo vector cảm biến, dùng lỗi tái tạo để xác định bất thường (sự cố cảm biến, ô nhiễm đột biến).
- Mô hình lưu tại `ml/models/anomaly_detector.joblib`.

## 6. REST API cho ứng dụng web/mobile

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r backend/api/requirements.txt
export INFLUXDB_URL=http://localhost:8086
export INFLUXDB_TOKEN=YOUR_TOKEN
export INFLUXDB_ORG=your_org
export INFLUXDB_BUCKET=air_quality
python backend/api/app.py
```

Endpoints chính:

| Endpoint | Mô tả |
| --- | --- |
| `GET /health` | Kiểm tra trạng thái API |
| `GET /api/v1/measurements?minutes=120` | Lấy dữ liệu PM2.5/PM10/MQ135 trong 120 phút gần nhất |
| `POST /api/v1/predict {"horizon": 30}` | Dự đoán PM2.5 cho 30/45/60 phút tới |
| `GET /api/v1/anomalies?minutes=180` | Trả về danh sách thời điểm bất thường |

API tự động nạp mô hình trong `ml/models`. Nếu yêu cầu horizon khác với mô hình đã huấn luyện, API sẽ dùng horizon gốc và trả về thông tin tương ứng.

## 7. Ứng dụng di động & thông báo

- App Android có thể gọi API `/predict` mỗi 5 phút để cập nhật dự báo, gửi push notification khi PM2.5 dự kiến vượt ngưỡng 50/100 µg/m³.
- Sử dụng Firebase Cloud Messaging (FCM) tích hợp, endpoint `/anomalies` để hiển thị lịch sử sự cố.

## 8. Gợi ý báo cáo khoa học

- Phân tích độ chính xác mô hình (MAE, RMSE) với từng horizon 30/45/60 phút.
- Trình bày case study anomaly detection: cảm biến hỏng (giá trị không đổi), sự kiện giao thông/thi công, cháy.
- So sánh mô hình: RandomForest vs LSTM/GRU → nhấn mạnh lý do chọn mô hình cuối (độ chính xác, tài nguyên, thời gian suy luận).
- Đề xuất mở rộng: dự báo theo mùa, tích hợp dữ liệu khí tượng, dashboard Grafana realtime.

## 9. Lộ trình triển khai (tham khảo kế hoạch đã cung cấp)

1. **Tuần 1–2**: hoàn thiện firmware ESP32, kết nối MQTT.
2. **Tuần 3**: thu thập dữ liệu lên InfluxDB, dựng Grafana.
3. **Tuần 4–5**: huấn luyện mô hình dự báo, đánh giá kết quả.
4. **Tuần 6**: phát hiện bất thường, thử nghiệm với dữ liệu thực.
5. **Tuần 7**: xây dựng API + ứng dụng di động.
6. **Tuần 8**: chuẩn bị báo cáo, demo, slide bảo vệ.

## 10. Giấy tờ & demo cuối kỳ

- Chuẩn bị dashboard Grafana hiển thị real-time, overlay đường dự báo 30/60 phút.
- Video demo: cảm biến đặt ngoài trời, app nhận thông báo khi PM2.5 tăng bất thường.
- Báo cáo kỹ thuật: mô tả kiến trúc, quy trình ML, đánh giá thực nghiệm.

## 11. Bản quyền & đóng góp

Toàn bộ mã nguồn được cấp phép MIT (có thể chỉnh theo yêu cầu đề tài). Bạn có thể mở issue/PR để đóng góp cải tiến.

Chúc bạn triển khai thành công và gây ấn tượng với hội đồng nhờ chức năng dự báo & phát hiện bất thường! 🚀
