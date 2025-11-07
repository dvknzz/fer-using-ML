"""MQTT -> InfluxDB bridge running on Raspberry Pi."""
from __future__ import annotations

import json
import logging
import os
import queue
import signal
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict

import yaml
from dotenv import load_dotenv
from influxdb_client import InfluxDBClient, Point, WriteOptions
from paho.mqtt import client as mqtt

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


@dataclass
class MQTTConfig:
    host: str
    port: int
    topic: str
    client_id: str
    keepalive: int = 60


@dataclass
class InfluxConfig:
    url: str
    token: str
    org: str
    bucket: str


@dataclass
class BufferConfig:
    max_records: int = 100
    flush_interval_seconds: int = 10


@dataclass
class AppConfig:
    mqtt: MQTTConfig
    influxdb: InfluxConfig
    buffer: BufferConfig

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "AppConfig":
        return AppConfig(
            mqtt=MQTTConfig(**data["mqtt"]),
            influxdb=InfluxConfig(**data["influxdb"]),
            buffer=BufferConfig(**data.get("buffer", {})),
        )


class MeasurementBuffer:
    def __init__(self, max_records: int, flush_interval: int, flush_callback):
        self._max_records = max_records
        self._flush_interval = flush_interval
        self._flush_callback = flush_callback
        self._queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._flusher, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=5)
        self.flush()

    def add(self, measurement: Dict[str, Any]):
        self._queue.put(measurement)
        if self._queue.qsize() >= self._max_records:
            self.flush()

    def flush(self):
        items = []
        while not self._queue.empty():
            items.append(self._queue.get())
        if items:
            logger.info("Flushing %d records to InfluxDB", len(items))
            self._flush_callback(items)

    def _flusher(self):
        while not self._stop.wait(self._flush_interval):
            self.flush()


def load_config(path: str) -> AppConfig:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    token = data["influxdb"].get("token") or os.getenv("INFLUXDB_TOKEN")
    if not token:
        raise RuntimeError("InfluxDB token missing. Set in config or INFLUXDB_TOKEN env var")
    data["influxdb"]["token"] = token
    return AppConfig.from_dict(data)


def create_influx_writer(cfg: InfluxConfig):
    client = InfluxDBClient(url=cfg.url, token=cfg.token, org=cfg.org)
    write_api = client.write_api(write_options=WriteOptions(batch_size=500, flush_interval=5_000))

    def _write(measurements: list[Dict[str, Any]]):
        points = []
        for m in measurements:
            ts = m.get("timestamp")
            if isinstance(ts, (int, float)):
                ts = int(ts)
            point = (
                Point("air_quality")
                .tag("device_id", m.get("device_id", "unknown"))
                .field("pm1_0", float(m.get("pm1_0", 0.0)))
                .field("pm2_5", float(m.get("pm2_5", 0.0)))
                .field("pm10", float(m.get("pm10", 0.0)))
                .field("pm2_5_cf", float(m.get("pm2_5_cf", 0.0)))
                .field("pm10_cf", float(m.get("pm10_cf", 0.0)))
                .field("mq135_ppm", float(m.get("mq135_ppm", 0.0)))
                .field("mq135_raw", float(m.get("mq135_raw", 0.0)))
            )
            if ts:
                # timestamp from ESP32 is milliseconds since boot; convert to ns relative to now
                point = point.time(time.time_ns())
            points.append(point)
        write_api.write(bucket=cfg.bucket, org=cfg.org, record=points)

    return _write


def on_message(buffer: MeasurementBuffer):
    def _callback(_client, _userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
            logger.debug("Received payload: %s", payload)
            buffer.add(payload)
        except json.JSONDecodeError as exc:
            logger.error("Invalid JSON payload: %s", exc)
    return _callback


def run(config_path: str):
    load_dotenv()
    cfg = load_config(config_path)
    influx_writer = create_influx_writer(cfg.influxdb)
    buffer = MeasurementBuffer(
        max_records=cfg.buffer.max_records,
        flush_interval=cfg.buffer.flush_interval_seconds,
        flush_callback=influx_writer,
    )

    client = mqtt.Client(client_id=cfg.mqtt.client_id)
    client.on_message = on_message(buffer)

    logger.info("Connecting to MQTT broker %s:%d", cfg.mqtt.host, cfg.mqtt.port)
    client.connect(cfg.mqtt.host, cfg.mqtt.port, cfg.mqtt.keepalive)
    client.subscribe(cfg.mqtt.topic)

    buffer.start()

    def _signal_handler(_signum, _frame):
        logger.info("Stopping collector...")
        client.loop_stop()
        client.disconnect()
        buffer.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    client.loop_forever()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python collector.py <config.yaml>")
        sys.exit(1)
    run(sys.argv[1])
