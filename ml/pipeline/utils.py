"""Shared utilities for data loading and preprocessing."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd
from influxdb_client import InfluxDBClient


@dataclass
class InfluxSettings:
    url: str
    token: str
    org: str
    bucket: str

    @classmethod
    def from_env(cls) -> "InfluxSettings":
        return cls(
            url=os.getenv("INFLUXDB_URL", "http://localhost:8086"),
            token=os.getenv("INFLUXDB_TOKEN", ""),
            org=os.getenv("INFLUXDB_ORG", "your_org"),
            bucket=os.getenv("INFLUXDB_BUCKET", "air_quality"),
        )


FEATURE_COLUMNS = ["pm2_5", "pm10", "mq135_ppm"]


def fetch_influx_data(settings: Optional[InfluxSettings] = None, hours: int = 48) -> pd.DataFrame:
    settings = settings or InfluxSettings.from_env()
    if not settings.token:
        raise RuntimeError("INFLUXDB_TOKEN env var not set")

    client = InfluxDBClient(url=settings.url, token=settings.token, org=settings.org)
    query = f"""
    from(bucket: "{settings.bucket}")
    |> range(start: -{hours}h)
    |> filter(fn: (r) => r._measurement == "air_quality")
    |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
    |> keep(columns: ["_time", "pm1_0", "pm2_5", "pm10", "mq135_ppm", "mq135_raw"])
    |> rename(columns: {{"_time": "timestamp"}})
    |> sort(columns: ["timestamp"])
    """
    tables = client.query_api().query_data_frame(query)
    if tables.empty:
        raise RuntimeError("No data returned from InfluxDB")
    df = tables.drop(columns=[col for col in tables.columns if col.startswith("result") or col.startswith("table")])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.set_index("timestamp").sort_index()
    df = df.asfreq("1min").interpolate(limit_direction="both")
    return df


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["timestamp"], index_col="timestamp")
    df = df.sort_index().asfreq("1min").interpolate(limit_direction="both")
    return df


def create_supervised(
    series: pd.DataFrame,
    lookback: int = 60,
    horizon: int = 30,
    target_col: str = "pm2_5",
) -> tuple[np.ndarray, np.ndarray]:
    values = series.values
    X, y = [], []
    for i in range(len(values) - lookback - horizon + 1):
        X.append(values[i : i + lookback])
        y.append(values[i + lookback + horizon - 1, series.columns.get_loc(target_col)])
    return np.array(X), np.array(y)
