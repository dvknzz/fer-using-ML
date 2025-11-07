#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

// ************ WiFi cấu hình ************
const char *WIFI_SSID = "YOUR_WIFI_SSID";
const char *WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

// ************ MQTT cấu hình ************
const char *MQTT_HOST = "192.168.1.10";
const uint16_t MQTT_PORT = 1883;
const char *MQTT_TOPIC = "sensors/airquality";
const char *MQTT_CLIENT_ID = "esp32-pm-sensor";

WiFiClient espClient;
PubSubClient mqttClient(espClient);

// ************ Cảm biến ************
HardwareSerial &pmsSerial = Serial2; // UART cho PMS7003
const uint8_t PMS_RX = 16;          // RX2 của ESP32 -> TX PMS7003
const uint8_t PMS_TX = 17;          // TX2 của ESP32 -> RX PMS7003

const uint8_t MQ135_PIN = 34;       // Chân ADC đọc tín hiệu MQ135
const float ADC_REF = 3.3;          // Điện áp tham chiếu
const float LOAD_RESISTOR = 10.0;   // kΩ, thay thế bằng giá trị thực tế

// Tần suất gửi dữ liệu (ms)
const unsigned long PUBLISH_INTERVAL = 10UL * 1000UL;
unsigned long lastPublish = 0;

struct PmsData {
  uint16_t pm1_0;
  uint16_t pm2_5;
  uint16_t pm10;
  bool valid;
};

void connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Đang kết nối WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(1000);
    Serial.print(".");
  }
  Serial.print("\nKết nối thành công! IP: ");
  Serial.println(WiFi.localIP());
}

void connectMQTT() {
  while (!mqttClient.connected()) {
    Serial.print("Đang kết nối MQTT...");
    if (mqttClient.connect(MQTT_CLIENT_ID)) {
      Serial.println(" đã kết nối!");
    } else {
      Serial.print(" thất bại, rc=");
      Serial.print(mqttClient.state());
      Serial.println(" thử lại sau 5s");
      delay(5000);
    }
  }
}

PmsData readPMS7003() {
  PmsData data = {0, 0, 0, false};
  const int FRAME_LENGTH = 32;
  if (pmsSerial.available() >= FRAME_LENGTH) {
    if (pmsSerial.read() == 0x42 && pmsSerial.read() == 0x4D) {
      uint8_t buffer[FRAME_LENGTH - 2];
      pmsSerial.readBytes(buffer, FRAME_LENGTH - 2);

      uint16_t checksum = 0x42 + 0x4D;
      for (int i = 0; i < FRAME_LENGTH - 4; i++) {
        checksum += buffer[i];
      }

      uint16_t receivedChecksum = (buffer[FRAME_LENGTH - 4] << 8) | buffer[FRAME_LENGTH - 3];
      if (checksum == receivedChecksum) {
        data.pm1_0 = (buffer[4] << 8) | buffer[5];
        data.pm2_5 = (buffer[6] << 8) | buffer[7];
        data.pm10 = (buffer[8] << 8) | buffer[9];
        data.valid = true;
      }
    }
  }
  return data;
}

float readMQ135() {
  int adcValue = analogRead(MQ135_PIN);
  float voltage = (adcValue / 4095.0f) * ADC_REF;
  float rs = (ADC_REF - voltage) * LOAD_RESISTOR / voltage;
  return rs;
}

void publishPayload(const PmsData &pms, float gasValue) {
  StaticJsonDocument<256> doc;
  doc["device_id"] = "esp32_air_node_01";
  doc["pm1_0"] = pms.pm1_0;
  doc["pm2_5"] = pms.pm2_5;
  doc["pm10"] = pms.pm10;
  doc["gas"] = gasValue;
  doc["timestamp"] = millis();

  char buffer[256];
  size_t len = serializeJson(doc, buffer);

  if (mqttClient.publish(MQTT_TOPIC, buffer, len)) {
    Serial.print("Đã gửi dữ liệu: ");
    Serial.println(buffer);
  } else {
    Serial.println("Gửi dữ liệu thất bại!");
  }
}

void setup() {
  Serial.begin(115200);
  delay(100);
  Serial.println("\nKhởi động hệ thống giám sát ô nhiễm...");

  connectWiFi();
  mqttClient.setServer(MQTT_HOST, MQTT_PORT);
  connectMQTT();

  pmsSerial.begin(9600, SERIAL_8N1, PMS_RX, PMS_TX);
  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);
}

void loop() {
  if (!mqttClient.connected()) {
    connectMQTT();
  }
  mqttClient.loop();

  unsigned long now = millis();
  if (now - lastPublish >= PUBLISH_INTERVAL) {
    lastPublish = now;

    PmsData pms = readPMS7003();
    float gasValue = readMQ135();

    if (pms.valid) {
      publishPayload(pms, gasValue);
    } else {
      Serial.println("Không đọc được dữ liệu PMS7003.");
    }
  }
}
