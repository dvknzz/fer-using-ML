"""Train PM2.5 forecasting models (LSTM/GRU/RandomForest)."""
from __future__ import annotations

import argparse
import pathlib

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.preprocessing import StandardScaler
from tensorflow import keras

from .utils import FEATURE_COLUMNS, create_supervised, fetch_influx_data, load_csv

MODEL_DIR = pathlib.Path(__file__).resolve().parent.parent / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)


def build_lstm(input_shape):
    model = keras.Sequential(
        [
            keras.layers.Input(shape=input_shape),
            keras.layers.LSTM(64, return_sequences=True),
            keras.layers.Dropout(0.2),
            keras.layers.LSTM(32),
            keras.layers.Dense(16, activation="relu"),
            keras.layers.Dense(1),
        ]
    )
    model.compile(optimizer="adam", loss="mse")
    return model


def build_gru(input_shape):
    model = keras.Sequential(
        [
            keras.layers.Input(shape=input_shape),
            keras.layers.GRU(64, return_sequences=True),
            keras.layers.Dropout(0.2),
            keras.layers.GRU(32),
            keras.layers.Dense(16, activation="relu"),
            keras.layers.Dense(1),
        ]
    )
    model.compile(optimizer="adam", loss="mse")
    return model


def train_model(model_type: str, data: pd.DataFrame, lookback: int, horizon: int):
    X, y = create_supervised(data[FEATURE_COLUMNS], lookback=lookback, horizon=horizon)
    split = int(len(X) * 0.8)
    X_train, X_val = X[:split], X[split:]
    y_train, y_val = y[:split], y[split:]

    if model_type == "random_forest":
        scaler = StandardScaler()
        X_train_flat = scaler.fit_transform(X_train.reshape(len(X_train), -1))
        X_val_flat = scaler.transform(X_val.reshape(len(X_val), -1))
        model = RandomForestRegressor(n_estimators=300, max_depth=None, random_state=42, n_jobs=-1)
        model.fit(X_train_flat, y_train)
        preds = model.predict(X_val_flat)
        metrics = {
            "mae": float(mean_absolute_error(y_val, preds)),
            "rmse": float(np.sqrt(mean_squared_error(y_val, preds))),
        }
        artifact = {
            "model": model,
            "scaler": scaler,
            "lookback": lookback,
            "horizon": horizon,
            "type": model_type,
            "feature_columns": FEATURE_COLUMNS,
        }
        return artifact, metrics

    keras.backend.clear_session()
    if model_type == "lstm":
        model = build_lstm((lookback, len(FEATURE_COLUMNS)))
    elif model_type == "gru":
        model = build_gru((lookback, len(FEATURE_COLUMNS)))
    else:
        raise ValueError(f"Unsupported model type: {model_type}")

    early_stop = keras.callbacks.EarlyStopping(monitor="val_loss", patience=5, restore_best_weights=True)
    history = model.fit(
        X_train,
        y_train,
        validation_data=(X_val, y_val),
        epochs=100,
        batch_size=32,
        callbacks=[early_stop],
        verbose=1,
    )
    preds = model.predict(X_val)
    metrics = {
        "mae": float(mean_absolute_error(y_val, preds)),
        "rmse": float(np.sqrt(mean_squared_error(y_val, preds))),
        "history": history.history,
    }
    artifact = {
        "model": model,
        "lookback": lookback,
        "horizon": horizon,
        "type": model_type,
        "feature_columns": FEATURE_COLUMNS,
    }
    return artifact, metrics


def save_artifact(artifact, metrics, out_path: pathlib.Path):
    if artifact["type"] == "random_forest":
        joblib.dump({"artifact": artifact, "metrics": metrics}, out_path)
    else:
        keras_path = out_path.with_suffix(".keras")
        artifact["model"].save(keras_path)
        artifact_copy = artifact.copy()
        artifact_copy["model_path"] = keras_path.name
        joblib.dump({"artifact": artifact_copy, "metrics": metrics}, out_path)



def main():
    parser = argparse.ArgumentParser(description="Train PM2.5 forecasting model")
    parser.add_argument("--model", choices=["lstm", "gru", "random_forest"], default="lstm")
    parser.add_argument("--lookback", type=int, default=60, help="Minutes of history for input")
    parser.add_argument("--horizon", type=int, default=30, help="Forecast horizon in minutes")
    parser.add_argument("--csv", type=str, help="Optional CSV file instead of InfluxDB")
    parser.add_argument("--output", type=str, default=str(MODEL_DIR / "pm25_forecaster.joblib"))
    args = parser.parse_args()

    if args.csv:
        data = load_csv(args.csv)
    else:
        data = fetch_influx_data(hours=72)

    artifact, metrics = train_model(args.model, data, lookback=args.lookback, horizon=args.horizon)
    out_path = pathlib.Path(args.output)
    save_artifact(artifact, metrics, out_path)
    print("Saved model to", out_path)
    print("Metrics:", metrics)


if __name__ == "__main__":
    main()
