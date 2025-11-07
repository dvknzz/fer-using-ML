# Sơ đồ nguyên lý phần cứng

Sơ đồ tổng quan mô tả cách kết nối ESP32 với cảm biến PMS7003, MQ135 và khả năng giao tiếp không dây với Raspberry Pi 4.

```mermaid
graph TD
    subgraph Power
        VUSB[5V USB]
    end
    subgraph ESP32
        ESP32VCC[Vin (5V)]
        ESP32GND[GND]
        GPIO16[GPIO16 / RX2]
        GPIO17[GPIO17 / TX2]
        GPIO34[GPIO34 / ADC1_CH6]
    end
    subgraph PMS7003
        PMSVCC[VCC 5V]
        PMSGND[GND]
        PMSTX[TX]
        PMSRX[RX]
        PMSSET[SET]
    end
    subgraph MQ135
        MQVCC[VCC 5V]
        MQGND[GND]
        MQAO[AO]
    end
    subgraph RaspberryPi4
        RPi[MQTT Broker / Collector]
    end

    VUSB --> ESP32VCC
    ESP32VCC --> PMSVCC
    ESP32VCC --> MQVCC
    ESP32GND --> PMSGND
    ESP32GND --> MQGND
    PMSTX --> GPIO16
    PMSRX --> GPIO17
    PMSSET -.-> ESP32VCC
    MQAO --> GPIO34
    ESP32 -.Wi-Fi MQTT.-> RPi
```

## Bảng nối dây chi tiết

| Thành phần | Chân | Kết nối | Ghi chú |
| --- | --- | --- | --- |
| ESP32 | Vin (5V) | 5V USB / Adapter | Cấp nguồn chung cho cảm biến |
| ESP32 | GND | GND chung | Tránh vòng lặp đất |
| ESP32 | GPIO16 (RX2) | PMS7003 TX | UART2 nhận dữ liệu bụi |
| ESP32 | GPIO17 (TX2) | PMS7003 RX | Dùng để gửi lệnh sleep/wake |
| ESP32 | GPIO34 (ADC1_CH6) | MQ135 AO | Đọc giá trị analog đã phân áp |
| PMS7003 | SET | Vin (qua 10kΩ) | Giữ mức cao để hoạt động |
| PMS7003 | RESET | Không nối / kéo lên 5V | Chỉ dùng khi cần reset phần cứng |
| MQ135 | DO | Không sử dụng | Tùy chọn làm ngưỡng cảnh báo |
| MQ135 | Heater | Vin 5V | Đảm bảo nguồn đủ 200mA |
| ESP32 | 3V3 | Không cấp cho cảm biến | Không đủ dòng cho PMS7003 |
| ESP32 | EN | Không nối | |

## Ghi chú nguồn và bảo vệ

- Tổng dòng PMS7003 ~100mA, MQ135 ~150mA (khi heater nóng), ESP32 ~240mA đỉnh → nên dùng adapter 5V 1A.
- Nếu dây dài >30cm, bổ sung tụ điện 100µF gần PMS7003 để ổn định.
- Khuyến nghị đặt thêm bộ chuyển mức logic nếu dùng PMS7003 bản 3.3V; đa số module hỗ trợ trực tiếp 5V.

## Kết nối với Raspberry Pi 4

- Raspberry Pi 4 không kết nối dây trực tiếp, chỉ cần cùng mạng Wi-Fi với ESP32.
- Broker MQTT có thể chạy trên chính Raspberry Pi (Mosquitto). ESP32 publish dữ liệu vào topic `air_quality/measurements`.
- Collector Python trong `raspberry_pi/collector/collector.py` subscribe MQTT và ghi dữ liệu xuống InfluxDB.

