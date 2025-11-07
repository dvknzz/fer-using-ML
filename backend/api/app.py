"""Flask REST API exposing air quality data and ML predictions."""
from __future__ import annotations

import os
import pathlib
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from flask import Flask, jsonify, request
from flask_cors import CORS
from influxdb_client import InfluxDBClient
import joblib
import numpy as np
import pandas as pd

MODEL_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "ml" / "models"
DEFAULT_MODEL_PATH = MODEL_DIR / "pm25_forecaster.joblib"
DEFAULT_ANOMALY_PATH = MODEL_DIR / "anomaly_detector.joblib"

app = Flask(__name__)
CORS(app)


class InfluxService:
    def __init__(self):
        self._client = InfluxDBClient(
            url=os.getenv("INFLUXDB_URL", "http://localhost:8086"),
            token=os.getenv("INFLUXDB_TOKEN"),
            org=os.getenv("INFLUXDB_ORG", "your_org"),
        )
        self._bucket = os.getenv("INFLUXDB_BUCKET", "air_quality")

    def fetch_recent(self, minutes: int = 180) -> pd.DataFrame:
        query = f"""
        from(bucket: "{self._bucket}")
        |> range(start: -{minutes}m)
        |> filter(fn: (r) => r._measurement == "air_quality")
        |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
        |> keep(columns: ["_time", "pm1_0", "pm2_5", "pm10", "mq135_ppm", "mq135_raw"])
        |> rename(columns: {{"_time": "timestamp"}})
        |> sort(columns: ["timestamp"])
        """
        tables = self._client.query_api().query_data_frame(query)
        if not tables.empty:
            df = tables.drop(columns=[col for col in tables.columns if col.startswith("result") or col.startswith("table")])
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            df = df.set_index("timestamp").sort_index()
            return df
        return pd.DataFrame()


influx_service = InfluxService()


class ModelRegistry:
    def __init__(self):
        self._forecast_artifact: Dict[str, Any] | None = None
        self._anomaly_artifact: Dict[str, Any] | None = None

    def load_models(self):
        if DEFAULT_MODEL_PATH.exists():
            bundle = joblib.load(DEFAULT_MODEL_PATH)
            if isinstance(bundle, dict) and "artifact" in bundle:
                self._forecast_artifact = bundle["artifact"]
                self._forecast_artifact["metrics"] = bundle.get("metrics")
            else:
                self._forecast_artifact = bundle
        if DEFAULT_ANOMALY_PATH.exists():
            bundle = joblib.load(DEFAULT_ANOMALY_PATH)
            if isinstance(bundle, dict) and "artifact" in bundle:
                self._anomaly_artifact = bundle["artifact"]
            else:
                self._anomaly_artifact = bundle

    @property
    def forecast_artifact(self):
        if self._forecast_artifact is None:
            self.load_models()
        return self._forecast_artifact

    @property
    def anomaly_artifact(self):
        if self._anomaly_artifact is None:
            self.load_models()
        return self._anomaly_artifact


models = ModelRegistry()


def build_features(df: pd.DataFrame, lookback: int) -> np.ndarray:
    if df.empty:
        raise ValueError("No data available to build features")
    df = df.copy()
    df = df.asfreq("1min").interpolate(limit_direction="both")
    if len(df) < lookback:
        raise ValueError("Not enough data for forecasting")
    features = df.iloc[-lookback:][["pm2_5", "pm10", "mq135_ppm"]]
    return features.values.reshape(1, lookback, features.shape[1])


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/v1/measurements")
def measurements():
    minutes = int(request.args.get("minutes", 120))
    df = influx_service.fetch_recent(minutes)
    if df.empty:
        return jsonify([])
    df = df.reset_index()
    df["timestamp"] = df["timestamp"].dt.tz_convert(timezone.utc).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return jsonify(df.to_dict(orient="records"))


@app.post("/api/v1/predict")
def predict():
    body: Dict[str, Any] = request.get_json(force=True) or {}
    horizon = int(body.get("horizon", 30))
    if horizon not in {30, 45, 60}:
        return jsonify({"error": "Supported horizons are 30, 45, or 60 minutes"}), 400

    df = influx_service.fetch_recent(240)
    if df.empty:
        return jsonify({"error": "No data"}), 400

    artifact = models.forecast_artifact
    if artifact is None:
        return jsonify({"error": "Forecast model not trained"}), 500

    lookback = int(artifact.get("lookback", 60))
    features = build_features(df, lookback)
    artifact = models.forecast_artifact
    model_type = artifact.get("type", "lstm")
    model_horizon = int(artifact.get("horizon", horizon))
    if model_horizon != horizon:
        # If request horizon differs from trained horizon, adjust response but still return prediction
        horizon = model_horizon
    if model_type == "random_forest":
        scaler = artifact.get("scaler")
        model = artifact.get("model")
        if scaler is None or model is None:
            return jsonify({"error": "RandomForest artifact is incomplete"}), 500
        X_flat = features.reshape(features.shape[0], -1)
        X_scaled = scaler.transform(X_flat)
        prediction = model.predict(X_scaled)
    else:
        model_path = artifact.get("model_path")
        model = artifact.get("model")
        if model is None and model_path:
            model_file = MODEL_DIR / model_path
            if model_file.exists():
                from tensorflow import keras

                model = keras.models.load_model(model_file)
                artifact["model"] = model
        if model is None:
            return jsonify({"error": "Neural network model missing"}), 500
        prediction = model.predict(features)

    current_ts = df.index[-1]
    future_ts = current_ts + timedelta(minutes=horizon)
    prediction_value = float(np.array(prediction).reshape(-1)[0])
    response = {
        "predicted_pm2_5": prediction_value,
        "horizon_minutes": horizon,
        "predicted_at": current_ts.isoformat(),
        "valid_for": future_ts.isoformat(),
    }
    return jsonify(response)


@app.get("/api/v1/anomalies")
def anomalies():
    minutes = int(request.args.get("minutes", 180))
    df = influx_service.fetch_recent(minutes)
    if df.empty:
        return jsonify([])
    artifact = models.anomaly_artifact
    if artifact is None:
        return jsonify([])
    model_type = artifact.get("type", "isolation_forest")
    model = artifact.get("model")
    if model is None:
        if model_type == "autoencoder" and artifact.get("model_path"):
            model_file = MODEL_DIR / artifact["model_path"]
            if model_file.exists():
                from tensorflow import keras

                model = keras.models.load_model(model_file)
                artifact["model"] = model
        if model is None:
            return jsonify([])
    feature_cols = ["pm2_5", "pm10", "mq135_ppm"]
    features_df = df[feature_cols]
    if model_type == "isolation_forest":
        scores = model.decision_function(features_df)
        preds = model.predict(features_df)
    else:
        # Autoencoder reconstruction error based anomaly score
        features = features_df.values.astype("float32")
        reconstructed = model.predict(features)
        errors = np.mean((features - reconstructed) ** 2, axis=1)
        threshold = float(artifact.get("threshold", np.mean(errors) + 3 * np.std(errors)))
        scores = threshold - errors
        preds = np.where(errors > threshold, -1, 1)
    df = df.reset_index()
    df["timestamp"] = df["timestamp"].dt.tz_convert(timezone.utc)
    anomalies: List[Dict[str, Any]] = []
    for idx, row in df.iterrows():
        if preds[idx] == -1:
            anomalies.append(
                {
                    "timestamp": row["timestamp"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "pm2_5": row["pm2_5"],
                    "pm10": row["pm10"],
                    "mq135_ppm": row["mq135_ppm"],
                    "score": scores[idx],
                }
            )
    return jsonify(anomalies)


if __name__ == "__main__":
    models.load_models()
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=True)
