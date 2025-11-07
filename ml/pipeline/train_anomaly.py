"""Train IsolationForest or Autoencoder for anomaly detection."""
from __future__ import annotations

import argparse
import pathlib

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from tensorflow import keras

from .utils import FEATURE_COLUMNS, fetch_influx_data, load_csv

MODEL_DIR = pathlib.Path(__file__).resolve().parent.parent / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)


def build_autoencoder(input_dim: int):
    encoder = keras.Sequential(
        [
            keras.layers.Input(shape=(input_dim,)),
            keras.layers.Dense(32, activation="relu"),
            keras.layers.Dense(16, activation="relu"),
        ],
        name="encoder",
    )
    decoder = keras.Sequential(
        [
            keras.layers.Input(shape=(16,)),
            keras.layers.Dense(32, activation="relu"),
            keras.layers.Dense(input_dim, activation="linear"),
        ],
        name="decoder",
    )
    inputs = keras.layers.Input(shape=(input_dim,))
    latent = encoder(inputs)
    outputs = decoder(latent)
    autoencoder = keras.Model(inputs, outputs)
    autoencoder.compile(optimizer="adam", loss="mse")
    return autoencoder, encoder


def train_isolation_forest(data: pd.DataFrame):
    model = IsolationForest(contamination=0.05, random_state=42)
    model.fit(data[FEATURE_COLUMNS])
    return {"model": model, "type": "isolation_forest", "feature_columns": FEATURE_COLUMNS}


def train_autoencoder(data: pd.DataFrame, epochs: int = 100):
    X = data[FEATURE_COLUMNS].values.astype("float32")
    model, encoder = build_autoencoder(X.shape[1])
    early_stop = keras.callbacks.EarlyStopping(monitor="loss", patience=5, restore_best_weights=True)
    model.fit(X, X, epochs=epochs, batch_size=32, shuffle=True, callbacks=[early_stop], verbose=1)
    return {
        "model": model,
        "encoder": encoder,
        "type": "autoencoder",
        "feature_columns": FEATURE_COLUMNS,
    }


def save_artifact(artifact, out_path: pathlib.Path):
    if artifact["type"] == "isolation_forest":
        joblib.dump(artifact, out_path)
    else:
        ae_path = out_path.with_suffix(".keras")
        artifact["model"].save(ae_path)
        artifact_copy = artifact.copy()
        artifact_copy["model_path"] = ae_path.name
        joblib.dump(artifact_copy, out_path)


def main():
    parser = argparse.ArgumentParser(description="Train anomaly detection model")
    parser.add_argument("--model", choices=["isolation_forest", "autoencoder"], default="isolation_forest")
    parser.add_argument("--csv", type=str, help="Optional CSV file instead of InfluxDB")
    parser.add_argument("--output", type=str, default=str(MODEL_DIR / "anomaly_detector.joblib"))
    args = parser.parse_args()

    if args.csv:
        data = load_csv(args.csv)
    else:
        data = fetch_influx_data(hours=72)

    if args.model == "isolation_forest":
        artifact = train_isolation_forest(data)
    else:
        artifact = train_autoencoder(data)

    out_path = pathlib.Path(args.output)
    save_artifact(artifact, out_path)
    print("Saved anomaly detector to", out_path)


if __name__ == "__main__":
    main()
