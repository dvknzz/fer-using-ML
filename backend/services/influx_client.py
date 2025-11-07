from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

import pandas as pd
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

from backend.config import get_settings
from backend.utils.logging import configure_logging

settings = get_settings()
logger = configure_logging("influx_service", level=settings.log_level)


class InfluxService:
    def __init__(self) -> None:
        self.client = InfluxDBClient(
            url=settings.influx_url,
            token=settings.influx_token,
            org=settings.influx_org,
        )
        self.write_api = self.client.write_api(write_options=SYNCHRONOUS)
        self.query_api = self.client.query_api()

    def write_measurement(self, payload: dict[str, Any]) -> None:
        point = (
            Point(settings.influx_measurement)
            .field("pm1_0", float(payload.get("pm1_0", 0)))
            .field("pm2_5", float(payload.get("pm2_5", 0)))
            .field("pm10", float(payload.get("pm10", 0)))
            .field("gas", float(payload.get("gas", 0)))
            .tag("device_id", payload.get("device_id", "esp32"))
            .time(
                payload.get("timestamp", datetime.utcnow()),
                write_precision=WritePrecision.NS,
            )
        )
        self.write_api.write(
            bucket=settings.influx_bucket, record=point, org=settings.influx_org
        )
        logger.debug("Wrote measurement to InfluxDB: %s", payload)

    def write_batch(self, points: Iterable[dict[str, Any]]) -> None:
        influx_points = []
        for payload in points:
            influx_points.append(
                Point(settings.influx_measurement)
                .field("pm1_0", float(payload.get("pm1_0", 0)))
                .field("pm2_5", float(payload.get("pm2_5", 0)))
                .field("pm10", float(payload.get("pm10", 0)))
                .field("gas", float(payload.get("gas", 0)))
                .tag("device_id", payload.get("device_id", "esp32"))
                .time(
                    payload.get("timestamp", datetime.utcnow()),
                    write_precision=WritePrecision.NS,
                )
            )
        if influx_points:
            self.write_api.write(
                bucket=settings.influx_bucket,
                record=influx_points,
                org=settings.influx_org,
            )
            logger.info("Wrote %d measurements to InfluxDB", len(influx_points))

    def fetch_recent_data(
        self, minutes: int = 120, limit: Optional[int] = None
    ) -> pd.DataFrame:
        start = datetime.utcnow() - timedelta(minutes=minutes)
        query = f"""
        from(bucket: "{settings.influx_bucket}")
            |> range(start: {start.isoformat()}Z)
            |> filter(fn: (r) => r["_measurement"] == "{settings.influx_measurement}")
            |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
            |> sort(columns: ["_time"])
        """
        if limit:
            query += f"\n            |> limit(n: {limit})"
        logger.debug("Executing Influx query: %s", query)
        df = self.query_api.query_data_frame(query=query, org=settings.influx_org)
        if not isinstance(df, pd.DataFrame) or df.empty:
            return pd.DataFrame()
        if isinstance(df, list):
            df = pd.concat(df)
        if "_start" in df.columns:
            df = df.drop(columns=["_start", "_stop"], errors="ignore")
        df = df.rename(columns={"_time": "timestamp"})
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.set_index("timestamp").sort_index()
        return df[["pm1_0", "pm2_5", "pm10", "gas", "device_id"]]

    def fetch_window(
        self, start_time: datetime, end_time: datetime
    ) -> pd.DataFrame:
        query = f"""
        from(bucket: "{settings.influx_bucket}")
            |> range(start: {start_time.isoformat()}Z, stop: {end_time.isoformat()}Z)
            |> filter(fn: (r) => r["_measurement"] == "{settings.influx_measurement}")
            |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
            |> sort(columns: ["_time"])
        """
        df = self.query_api.query_data_frame(query=query, org=settings.influx_org)
        if not isinstance(df, pd.DataFrame) or df.empty:
            return pd.DataFrame()
        if isinstance(df, list):
            df = pd.concat(df)
        df = df.rename(columns={"_time": "timestamp"})
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.set_index("timestamp").sort_index()
        return df[["pm1_0", "pm2_5", "pm10", "gas", "device_id"]]

    def close(self) -> None:
        self.client.close()
        logger.info("Closed InfluxDB client")
