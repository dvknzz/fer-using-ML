from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import pandas as pd

from backend.config import get_settings
from backend.models.anomaly import (
    AutoencoderAnomalyDetector,
    IsolationForestDetector,
)
from backend.services.influx_client import InfluxService
from backend.utils.logging import configure_logging

settings = get_settings()
logger = configure_logging("anomaly_service", level=settings.log_level)


@dataclass
class AnomalyResult:
    detector: str
    timestamp: str
    score: float
    is_anomaly: bool
    pm2_5: float
    pm10: float
    gas: float


class AnomalyDetectionService:
    def __init__(self, artifact_dir: Path | None = None) -> None:
        self.artifact_dir = (artifact_dir or settings.artifact_dir / "anomaly").resolve()
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.influx = InfluxService()
        self.feature_columns = ["pm2_5", "pm10", "gas", "pm1_0"]

    def _prepare_dataset(self, history_minutes: int = 24 * 12) -> pd.DataFrame:
        df = self.influx.fetch_recent_data(minutes=history_minutes)
        if df.empty:
            logger.warning("No data returned from InfluxDB for anomaly detection")
            return pd.DataFrame()
        df = df.resample("5T").mean().interpolate()
        df = df.dropna()
        return df

    def train_detector(
        self,
        detector_type: Literal["isolation_forest", "autoencoder"] = "isolation_forest",
        history_minutes: int = 24 * 12,
    ):
        df = self._prepare_dataset(history_minutes=history_minutes)
        if df.empty:
            raise RuntimeError("Dataset empty; cannot train anomaly detector")
        X = df[self.feature_columns].values
        if detector_type == "isolation_forest":
            detector = IsolationForestDetector()
            detector.fit(X)
            detector.save(self.artifact_dir)
            return detector
        detector = AutoencoderAnomalyDetector(input_dim=X.shape[1])
        detector.fit(X)
        detector.save(self.artifact_dir)
        return detector

    def load_detector(
        self, detector_type: Literal["isolation_forest", "autoencoder"]
    ):
        if detector_type == "isolation_forest":
            return IsolationForestDetector.load(self.artifact_dir)
        return AutoencoderAnomalyDetector.load(self.artifact_dir)

    def detect_anomalies(
        self,
        detector_type: Literal["isolation_forest", "autoencoder"] = "isolation_forest",
        history_minutes: int = settings.anomaly_window,
    ) -> list[dict]:
        detector = self.load_detector(detector_type)
        df = self._prepare_dataset(history_minutes=history_minutes)
        if df.empty:
            raise RuntimeError("No data available for anomaly detection")
        X = df[self.feature_columns].values
        if detector_type == "isolation_forest":
            scores = detector.score_samples(X)
            flags = detector.predict(X)
        else:
            scores = detector.score_samples(X)
            flags = detector.predict(X)
        results: list[AnomalyResult] = []
        for (timestamp, row), score, flag in zip(df.iterrows(), scores, flags):
            results.append(
                AnomalyResult(
                    detector=detector_type,
                    timestamp=timestamp.isoformat(),
                    score=float(score),
                    is_anomaly=bool(flag),
                    pm2_5=float(row["pm2_5"]),
                    pm10=float(row["pm10"]),
                    gas=float(row["gas"]),
                )
            )
        return [asdict(result) for result in results]
