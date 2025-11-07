from __future__ import annotations

import json
import signal
import sys
from datetime import datetime
from threading import Event

import paho.mqtt.client as mqtt

from backend.config import get_settings
from backend.services.influx_client import InfluxService
from backend.utils.logging import configure_logging

settings = get_settings()
logger = configure_logging("mqtt_ingestor", level=settings.log_level)
shutdown_event = Event()


class MqttInfluxBridge:
    def __init__(self) -> None:
        self.influx = InfluxService()
        self.client = mqtt.Client(client_id=settings.mqtt_client_id)
        if settings.mqtt_username and settings.mqtt_password:
            self.client.username_pw_set(
                settings.mqtt_username, settings.mqtt_password
            )

        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.on_disconnect = self.on_disconnect

    def start(self) -> None:
        logger.info(
            "Connecting to MQTT broker %s:%s topic %s",
            settings.mqtt_host,
            settings.mqtt_port,
            settings.mqtt_topic,
        )
        self.client.connect(settings.mqtt_host, settings.mqtt_port, keepalive=60)
        self.client.loop_start()
        self.client.subscribe(settings.mqtt_topic)
        logger.info("Subscribed to MQTT topic %s", settings.mqtt_topic)

    def stop(self) -> None:
        logger.info("Stopping MQTT bridge")
        self.client.loop_stop()
        self.client.disconnect()
        self.influx.close()

    def on_connect(self, client: mqtt.Client, userdata, flags, rc) -> None:  # type: ignore[override]
        if rc == 0:
            logger.info("Connected to MQTT broker")
        else:
            logger.error("MQTT connection failed with code %s", rc)

    def on_disconnect(self, client: mqtt.Client, userdata, rc) -> None:  # type: ignore[override]
        if rc != 0:
            logger.warning("Unexpected MQTT disconnection (code %s), reconnecting", rc)
            client.reconnect()

    def on_message(self, client: mqtt.Client, userdata, message: mqtt.MQTTMessage) -> None:  # type: ignore[override]
        try:
            payload = json.loads(message.payload.decode("utf-8"))
            payload.setdefault("timestamp", datetime.utcnow().isoformat())
            self.influx.write_measurement(payload)
            logger.debug("Stored payload from MQTT: %s", payload)
        except json.JSONDecodeError:
            logger.exception("Failed to decode MQTT payload: %s", message.payload)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to process MQTT message")


def _handle_signal(signum, frame) -> None:
    logger.info("Received signal %s, shutting down...", signum)
    shutdown_event.set()


def main() -> None:
    bridge = MqttInfluxBridge()
    bridge.start()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    while not shutdown_event.is_set():
        shutdown_event.wait(timeout=1)

    bridge.stop()
    sys.exit(0)


if __name__ == "__main__":
    main()
