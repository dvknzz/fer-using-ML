from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal
import pandas as pd

from backend.config import get_settings
from backend.models.forecasting import (
    RandomForestForecaster,
    SequenceForecaster,
    build_supervised_dataset,
)
from backend.services.influx_client import InfluxService
from backend.utils.logging import configure_logging

settings = get_settings()
logger = configure_logging("forecasting_service", level=settings.log_level)


@dataclass
class ForecastResult:
    horizon_minutes: int
    model_type: str
    prediction: float
    mae: float | None = None
    rmse: float | None = None


class ForecastingService:
    def __init__(self, artifact_dir: Path | None = None) -> None:
        self.artifact_dir = (artifact_dir or settings.artifact_dir / "forecasting").resolve()
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.influx = InfluxService()
        self.frequency_minutes = 5  # assume readings aggregated at 5-minute resolution
        self.feature_columns = ["pm1_0", "pm2_5", "pm10", "gas"]
        self.target_column = "pm2_5"

    def _prepare_dataset(self, history_minutes: int = 24 * 12) -> pd.DataFrame:
        df = self.influx.fetch_recent_data(minutes=history_minutes)
        if df.empty:
            logger.warning("No data returned from InfluxDB for forecasting")
            return pd.DataFrame()
        df = df.resample(f"{self.frequency_minutes}T").mean().interpolate()
        df = df.dropna(axis=0, how="any")
        return df

    def train_model(
        self,
        model_type: Literal["lstm", "gru", "random_forest"],
        horizon_minutes: int,
        history_minutes: int = 24 * 12,
    ) -> ForecastResult:
        horizon_steps = max(1, horizon_minutes // self.frequency_minutes)
        df = self._prepare_dataset(history_minutes=history_minutes)
        if df.empty:
            raise RuntimeError("Dataset is empty; ensure InfluxDB has data")

        X, y = build_supervised_dataset(
            df,
            lookback=settings.lookback_window,
            horizon=horizon_steps,
            feature_columns=self.feature_columns,
            target_column=self.target_column,
        )
        if X.size == 0:
            raise RuntimeError(
                "Insufficient samples for training. Collect more sensor data."
            )

        if model_type in {"lstm", "gru"}:
            model = SequenceForecaster(
                cell_type=model_type,
                lookback=settings.lookback_window,
                horizon=horizon_steps,
                input_size=len(self.feature_columns),
            )
            metrics = model.fit(X, y)
            model.save(self.artifact_dir)
            result = ForecastResult(
                horizon_minutes=horizon_minutes,
                model_type=model_type,
                prediction=float(model.predict(X[-1])[-1]),
                mae=metrics["mae"],
                rmse=metrics["rmse"],
            )
        else:
            model = RandomForestForecaster(
                lookback=settings.lookback_window, horizon=horizon_steps
            )
            metrics = model.fit(X, y)
            model.save(self.artifact_dir)
            result = ForecastResult(
                horizon_minutes=horizon_minutes,
                model_type=model_type,
                prediction=float(model.predict(X[-1])[-1]),
                mae=metrics["mae"],
                rmse=metrics["rmse"],
            )

        logger.info("Training finished: %s", result)
        return result

    def load_model(
        self, preferred_type: Literal["lstm", "gru", "random_forest"], horizon_minutes: int
    ):
        horizon_steps = max(1, horizon_minutes // self.frequency_minutes)
        if preferred_type in {"lstm", "gru"}:
            try:
                return SequenceForecaster.load(
                    artifact_dir=self.artifact_dir,
                    cell_type=preferred_type,
                    horizon=horizon_steps,
                )
            except FileNotFoundError:
                logger.warning(
                    "%s model for %s minutes not found, fallback to RandomForest",
                    preferred_type,
                    horizon_minutes,
                )
        return RandomForestForecaster.load(
            artifact_dir=self.artifact_dir, horizon=horizon_steps
        )

    def predict_future(
        self,
        horizon_minutes: int,
        model_type: Literal["lstm", "gru", "random_forest"] = "lstm",
        history_minutes: int = 12 * 12,
    ) -> ForecastResult:
        model = self.load_model(model_type, horizon_minutes)
        df = self._prepare_dataset(history_minutes=history_minutes)
        if df.empty:
            raise RuntimeError("No data available for prediction")
        horizon_steps = max(1, horizon_minutes // self.frequency_minutes)
        X, _ = build_supervised_dataset(
            df,
            lookback=settings.lookback_window,
            horizon=horizon_steps,
            feature_columns=self.feature_columns,
            target_column=self.target_column,
        )
        if X.size == 0:
            raise RuntimeError("Not enough samples to build prediction window")
        latest_window = X[-1]
        prediction = float(model.predict(latest_window)[-1])
        result = ForecastResult(
            horizon_minutes=horizon_minutes,
            model_type=model_type,
            prediction=prediction,
        )
        logger.debug("Prediction result: %s", result)
        return result

    def forecast_bundle(
        self,
        horizons: list[int] | None = None,
        model_type: Literal["lstm", "gru", "random_forest"] = "lstm",
    ) -> list[dict]:
        horizons = horizons or settings.forecast_horizons
        results = []
        for horizon in horizons:
            try:
                result = self.predict_future(horizon_minutes=horizon, model_type=model_type)
                results.append(asdict(result))
            except Exception as exc:  # noqa: BLE001
                logger.exception("Failed to generate forecast for %s minutes", horizon)
                results.append(
                    {
                        "horizon_minutes": horizon,
                        "model_type": model_type,
                        "error": str(exc),
                    }
                )
        return results
