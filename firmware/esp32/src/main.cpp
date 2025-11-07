#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <PMS.h>

// ==== User configuration =====================================================
const char *WIFI_SSID = "YOUR_WIFI_SSID";
const char *WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

const char *MQTT_BROKER = "192.168.1.10";     // Raspberry Pi IP
const int   MQTT_PORT   = 1883;
const char *MQTT_TOPIC  = "environment/air_quality";
const char *MQTT_CLIENT_ID = "esp32-air-quality";

// How often to publish measurements (ms)
const uint32_t PUBLISH_INTERVAL_MS = 30'000;    // 30 seconds

// Plantower PMS7003 pins
const int PMS_RX_PIN = 16; // ESP32 RX2
const int PMS_TX_PIN = 17; // ESP32 TX2

// MQ135 analog pin
const int MQ135_PIN   = 34;
// Calibrated clean-air ratio (depends on sensor, adjust after calibration)
const float MQ135_RL  = 10.0f;  // load resistance in kOhms
const float MQ135_RO  = 76.63f; // sensor resistance in clean air (calibrate!)

// ==== Globals ================================================================
HardwareSerial SerialPMS(2);
PMS pms(SerialPMS);
PMS::DATA pmsData;

WiFiClient wifiClient;
PubSubClient mqttClient(wifiClient);

unsigned long lastPublish = 0;

// ==== Utility functions ======================================================
float readMQ135PPM(uint16_t raw)
{
    // Convert ADC value to voltage (assuming 3.3V reference)
    const float voltage = (static_cast<float>(raw) / 4095.0f) * 3.3f;
    // Calculate sensor resistance (RS) from voltage divider
    const float rs = ((3.3f * MQ135_RL) / voltage) - MQ135_RL;

    // MQ135 characteristic curve for NH3/CO2 approximation (adjust for your target gas)
    // ppm = A * (RS/RO)^B
    const float ratio = rs / MQ135_RO;
    const float A = 116.6020682f;
    const float B = -2.769034857f;

    return A * powf(ratio, B);
}

void connectWiFi()
{
    Serial.print("Connecting to WiFi");
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    int attempts = 0;
    while (WiFi.status() != WL_CONNECTED) {
        delay(500);
        Serial.print(".");
        attempts++;
        if (attempts > 40) {
            Serial.println("\nFailed to connect to WiFi, restarting...");
            ESP.restart();
        }
    }
    Serial.println("\nWiFi connected");
    Serial.print("IP address: ");
    Serial.println(WiFi.localIP());
}

void connectMQTT()
{
    while (!mqttClient.connected()) {
        Serial.print("Connecting to MQTT broker...");
        if (mqttClient.connect(MQTT_CLIENT_ID)) {
            Serial.println("connected");
        } else {
            Serial.print("failed, rc=");
            Serial.print(mqttClient.state());
            Serial.println(". Trying again in 5 seconds");
            delay(5000);
        }
    }
}

void publishMeasurement()
{
    if (!pms.readUntil(pmsData, 2000)) {
        Serial.println("Failed to read PMS7003 data");
        return;
    }

    const uint16_t mq135Raw = analogRead(MQ135_PIN);
    const float mq135PPM = readMQ135PPM(mq135Raw);

    StaticJsonDocument<256> payload;
    payload["device_id"] = MQTT_CLIENT_ID;
    payload["timestamp"] = (uint64_t) (millis());
    payload["pm1_0"] = pmsData.PM_AE_UG_1_0;
    payload["pm2_5"] = pmsData.PM_AE_UG_2_5;
    payload["pm10"] = pmsData.PM_AE_UG_10_0;
    payload["pm2_5_cf"] = pmsData.PM_SP_UG_2_5;
    payload["pm10_cf"] = pmsData.PM_SP_UG_10_0;
    payload["mq135_ppm"] = mq135PPM;
    payload["mq135_raw"] = mq135Raw;

    char buffer[256];
    const size_t n = serializeJson(payload, buffer);

    if (mqttClient.publish(MQTT_TOPIC, buffer, n)) {
        Serial.println("Published measurement:");
        serializeJsonPretty(payload, Serial);
        Serial.println();
    } else {
        Serial.println("Failed to publish measurement");
    }
}

void setup()
{
    Serial.begin(115200);
    delay(2000);

    // PMS7003 serial
    SerialPMS.begin(9600, SERIAL_8N1, PMS_RX_PIN, PMS_TX_PIN);

    // Setup MQ135 input
    analogReadResolution(12);
    analogSetAttenuation(ADC_11db);

    connectWiFi();

    mqttClient.setServer(MQTT_BROKER, MQTT_PORT);
    connectMQTT();
}

void loop()
{
    if (WiFi.status() != WL_CONNECTED) {
        connectWiFi();
    }
    if (!mqttClient.connected()) {
        connectMQTT();
    }
    mqttClient.loop();

    const unsigned long now = millis();
    if (now - lastPublish >= PUBLISH_INTERVAL_MS) {
        publishMeasurement();
        lastPublish = now;
    }
}
