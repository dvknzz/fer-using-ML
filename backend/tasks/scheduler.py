from __future__ import annotations

from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from backend.config import get_settings
from backend.services.anomaly_service import AnomalyDetectionService
from backend.services.forecasting_service import ForecastingService
from backend.utils.logging import configure_logging

settings = get_settings()
logger = configure_logging("scheduler", level=settings.log_level)


class MonitoringScheduler:
    def __init__(self) -> None:
        self.scheduler = BackgroundScheduler(timezone="UTC")
        self.forecast_service = ForecastingService()
        self.anomaly_service = AnomalyDetectionService()

    def start(self) -> None:
        self.scheduler.add_job(
            self.train_forecast_models,
            trigger="interval",
            hours=6,
            next_run_time=datetime.utcnow(),
            id="train_forecasts",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self.detect_recent_anomalies,
            trigger="interval",
            minutes=15,
            next_run_time=datetime.utcnow(),
            id="detect_anomalies",
            replace_existing=True,
        )
        self.scheduler.start()
        logger.info("Scheduler started with forecast and anomaly jobs")

    def shutdown(self) -> None:
        self.scheduler.shutdown(wait=False)
        logger.info("Scheduler shutdown complete")

    def train_forecast_models(self) -> None:
        logger.info("Training forecast models for horizons %s", settings.forecast_horizons)
        for horizon in settings.forecast_horizons:
            try:
                self.forecast_service.train_model(
                    model_type="lstm", horizon_minutes=horizon
                )
            except Exception:  # noqa: BLE001
                logger.exception("Failed to train LSTM forecaster for %s min", horizon)
                try:
                    self.forecast_service.train_model(
                        model_type="random_forest", horizon_minutes=horizon
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Fallback RandomForest training failed for horizon %s", horizon
                    )

    def detect_recent_anomalies(self) -> None:
        logger.info("Running scheduled anomaly detection")
        try:
            anomalies = self.anomaly_service.detect_anomalies(
                detector_type="isolation_forest",
                history_minutes=settings.anomaly_window,
            )
            flagged = [a for a in anomalies if a["is_anomaly"]]
            logger.info("Detected %d anomalies in last window", len(flagged))
        except Exception:  # noqa: BLE001
            logger.exception("Scheduled anomaly detection failed")


if __name__ == "__main__":
    import time

    scheduler = MonitoringScheduler()
    try:
        scheduler.start()
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        scheduler.shutdown()
