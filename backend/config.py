from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import BaseSettings, Field


class Settings(BaseSettings):
    # General
    project_name: str = Field(
        default="Urban Air Quality Intelligence Platform",
        description="Public name for logging and API metadata",
    )
    env: str = Field(default="development")
    log_level: str = Field(default="INFO")

    # MQTT
    mqtt_host: str = Field(default="localhost")
    mqtt_port: int = Field(default=1883)
    mqtt_topic: str = Field(default="sensors/airquality")
    mqtt_username: Optional[str] = None
    mqtt_password: Optional[str] = None
    mqtt_client_id: str = Field(default="raspi-air-ingestor")

    # InfluxDB
    influx_url: str = Field(default="http://localhost:8086")
    influx_token: str = Field(default="influxdb-token")
    influx_org: str = Field(default="airlab")
    influx_bucket: str = Field(default="air_quality")
    influx_measurement: str = Field(default="air_metrics")

    # Model settings
    artifact_dir: Path = Field(default=Path(__file__).resolve().parent / "artifacts")
    lookback_window: int = Field(
        default=24,
        description="Number of historical data points to use as input features",
    )
    forecast_horizons: list[int] = Field(
        default_factory=lambda: [30, 60],
        description="Forecast horizons in minutes",
    )
    anomaly_window: int = Field(
        default=60,
        description="Window of minutes used for anomaly detection context",
    )

    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.artifact_dir.mkdir(parents=True, exist_ok=True)
    return settings
