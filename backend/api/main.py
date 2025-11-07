from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from backend.config import get_settings
from backend.services.anomaly_service import AnomalyDetectionService
from backend.services.forecasting_service import ForecastingService
from backend.services.influx_client import InfluxService
from backend.utils.logging import configure_logging

settings = get_settings()
logger = configure_logging("api", level=settings.log_level)

app = FastAPI(
    title=settings.project_name,
    version="1.0.0",
    description=(
        "REST API chạy trên Raspberry Pi để thu thập dữ liệu không khí, "
        "dự đoán xu hướng PM2.5/PM10 trong 30–60 phút và phát hiện bất thường."
    ),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

influx_service = InfluxService()
forecast_service = ForecastingService()
anomaly_service = AnomalyDetectionService()


class TrainForecastRequest(BaseModel):
    model_type: Literal["lstm", "gru", "random_forest"] = "lstm"
    horizon_minutes: int = Field(30, ge=5, le=360)
    history_minutes: int = Field(24 * 12, ge=60)


class TrainAnomalyRequest(BaseModel):
    detector_type: Literal["isolation_forest", "autoencoder"] = "isolation_forest"
    history_minutes: int = Field(24 * 12, ge=60)


class ForecastResponse(BaseModel):
    horizon_minutes: int
    model_type: str
    prediction: float
    mae: Optional[float] = None
    rmse: Optional[float] = None


class MetricPoint(BaseModel):
    timestamp: datetime
    pm1_0: float
    pm2_5: float
    pm10: float
    gas: float
    device_id: str


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "message": "Air quality platform operational"}


@app.get("/metrics/latest", response_model=MetricPoint)
def latest_metrics() -> MetricPoint:
    df = influx_service.fetch_recent_data(minutes=6, limit=1)
    if df.empty:
        raise HTTPException(status_code=404, detail="No data available")
    series = df.iloc[-1]
    return MetricPoint(
        timestamp=series.name.to_pydatetime(),
        pm1_0=float(series["pm1_0"]),
        pm2_5=float(series["pm2_5"]),
        pm10=float(series["pm10"]),
        gas=float(series["gas"]),
        device_id=str(series["device_id"]),
    )


@app.get("/metrics/history")
def historical_metrics(
    minutes: int = Query(60, ge=10, le=60 * 24),
) -> list[MetricPoint]:
    df = influx_service.fetch_recent_data(minutes=minutes)
    if df.empty:
        return []
    records: list[MetricPoint] = []
    for timestamp, row in df.iterrows():
        records.append(
            MetricPoint(
                timestamp=timestamp.to_pydatetime(),
                pm1_0=float(row["pm1_0"]),
                pm2_5=float(row["pm2_5"]),
                pm10=float(row["pm10"]),
                gas=float(row["gas"]),
                device_id=str(row["device_id"]),
            )
        )
    return records


@app.get("/forecast", response_model=list[ForecastResponse])
def forecast(
    horizons: Optional[list[int]] = Query(None),
    model_type: Literal["lstm", "gru", "random_forest"] = "lstm",
) -> list[ForecastResponse]:
    results = forecast_service.forecast_bundle(
        horizons=horizons, model_type=model_type
    )
    response: list[ForecastResponse] = []
    for result in results:
        if "error" in result:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to generate forecast for {result['horizon_minutes']} minutes: {result['error']}",
            )
        response.append(ForecastResponse(**result))
    return response


@app.post("/forecast/train", response_model=ForecastResponse)
def train_forecast(request: TrainForecastRequest = Body(...)) -> ForecastResponse:
    result = forecast_service.train_model(
        model_type=request.model_type,
        horizon_minutes=request.horizon_minutes,
        history_minutes=request.history_minutes,
    )
    return ForecastResponse(**result.__dict__)


@app.get("/anomalies")
def detect_anomalies(
    detector_type: Literal["isolation_forest", "autoencoder"] = "isolation_forest",
    history_minutes: int = Query(settings.anomaly_window, ge=30, le=60 * 24),
) -> list[dict]:
    results = anomaly_service.detect_anomalies(
        detector_type=detector_type,
        history_minutes=history_minutes,
    )
    return results


@app.post("/anomalies/train")
def train_anomaly_detector(request: TrainAnomalyRequest = Body(...)) -> dict:
    anomaly_service.train_detector(
        detector_type=request.detector_type,
        history_minutes=request.history_minutes,
    )
    return {
        "status": "ok",
        "detector_type": request.detector_type,
        "history_minutes": request.history_minutes,
    }


@app.on_event("shutdown")
def shutdown_event() -> None:
    influx_service.close()
    logger.info("API shutdown completed")
