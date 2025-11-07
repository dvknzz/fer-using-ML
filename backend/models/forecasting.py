from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional, Tuple

import joblib
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

from backend.config import get_settings
from backend.utils.logging import configure_logging

settings = get_settings()
logger = configure_logging("forecasting_models", level=settings.log_level)


def build_supervised_dataset(
    frame: pd.DataFrame,
    lookback: int,
    horizon: int,
    feature_columns: list[str],
    target_column: str = "pm2_5",
) -> Tuple[np.ndarray, np.ndarray]:
    values = frame[feature_columns].values
    target_values = frame[target_column].values
    X, y = [], []
    for idx in range(lookback, len(values) - horizon + 1):
        X.append(values[idx - lookback : idx])
        y.append(target_values[idx + horizon - 1])
    if not X:
        return np.empty((0, lookback, len(feature_columns))), np.empty((0,))
    X_arr = np.stack(X)
    y_arr = np.array(y)
    return X_arr, y_arr


class SequenceRegressor(nn.Module):
    def __init__(
        self,
        input_size: int,
        hidden_size: int,
        num_layers: int,
        cell_type: Literal["lstm", "gru"] = "lstm",
        dropout: float = 0.2,
    ) -> None:
        super().__init__()
        rnn_cls = {"lstm": nn.LSTM, "gru": nn.GRU}[cell_type]
        self.rnn = rnn_cls(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            dropout=dropout if num_layers > 1 else 0.0,
            batch_first=True,
        )
        self.fc = nn.Linear(hidden_size, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        output, _ = self.rnn(x)
        out = output[:, -1, :]
        return self.fc(out)


@dataclass
class SequenceArtifact:
    model_path: Path
    scaler_path: Path
    metadata_path: Path


class SequenceForecaster:
    def __init__(
        self,
        cell_type: Literal["lstm", "gru"],
        lookback: int,
        horizon: int,
        input_size: int,
        hidden_size: int = 64,
        num_layers: int = 2,
        dropout: float = 0.2,
        learning_rate: float = 1e-3,
        epochs: int = 30,
        batch_size: int = 64,
        device: Optional[str] = None,
    ) -> None:
        self.cell_type = cell_type
        self.lookback = lookback
        self.horizon = horizon
        self.input_size = input_size
        self.hidden_size = hidden_size
        self.num_layers = num_layers
        self.dropout = dropout
        self.learning_rate = learning_rate
        self.epochs = epochs
        self.batch_size = batch_size
        self.device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))

        self.scaler = StandardScaler()
        self.model = SequenceRegressor(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            cell_type=cell_type,
            dropout=dropout,
        ).to(self.device)

    def fit(self, X: np.ndarray, y: np.ndarray) -> dict[str, float]:
        if X.size == 0:
            raise ValueError("Training data is empty. Provide more samples.")

        orig_shape = X.shape
        X_flat = X.reshape(-1, self.input_size)
        X_scaled = self.scaler.fit_transform(X_flat).reshape(orig_shape)

        dataset = torch.utils.data.TensorDataset(
            torch.tensor(X_scaled, dtype=torch.float32),
            torch.tensor(y, dtype=torch.float32).unsqueeze(1),
        )
        train_loader = torch.utils.data.DataLoader(
            dataset, batch_size=self.batch_size, shuffle=True
        )

        criterion = nn.MSELoss()
        optimizer = torch.optim.Adam(self.model.parameters(), lr=self.learning_rate)

        self.model.train()
        for epoch in range(self.epochs):
            running_loss = 0.0
            for batch_X, batch_y in train_loader:
                batch_X = batch_X.to(self.device)
                batch_y = batch_y.to(self.device)

                optimizer.zero_grad()
                outputs = self.model(batch_X)
                loss = criterion(outputs, batch_y)
                loss.backward()
                optimizer.step()
                running_loss += loss.item() * batch_X.size(0)
            epoch_loss = running_loss / len(dataset)
            logger.debug(
                "%s Epoch [%d/%d] Loss: %.4f",
                self.cell_type.upper(),
                epoch + 1,
                self.epochs,
                epoch_loss,
            )

        self.model.eval()
        with torch.no_grad():
            predictions = (
                self.model(torch.tensor(X_scaled, dtype=torch.float32).to(self.device))
                .cpu()
                .numpy()
                .flatten()
            )
        metrics = {
            "mae": float(mean_absolute_error(y, predictions)),
            "rmse": float(np.sqrt(mean_squared_error(y, predictions))),
        }
        logger.info(
            "Trained %s forecaster | horizon=%s | MAE=%.3f RMSE=%.3f",
            self.cell_type.upper(),
            self.horizon,
            metrics["mae"],
            metrics["rmse"],
        )
        return metrics

    def predict(self, X: np.ndarray) -> np.ndarray:
        if X.ndim == 2:
            X = np.expand_dims(X, axis=0)
        X_scaled = self.scaler.transform(X.reshape(-1, self.input_size)).reshape(
            X.shape
        )
        tensor_X = torch.tensor(X_scaled, dtype=torch.float32).to(self.device)
        self.model.eval()
        with torch.no_grad():
            preds = self.model(tensor_X).cpu().numpy().flatten()
        return preds

    def save(self, artifact_dir: Path) -> SequenceArtifact:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        suffix = f"{self.cell_type}_h{self.horizon}"
        model_path = artifact_dir / f"sequence_{suffix}.pt"
        scaler_path = artifact_dir / f"sequence_{suffix}_scaler.pkl"
        metadata_path = artifact_dir / f"sequence_{suffix}_meta.json"

        torch.save(self.model.state_dict(), model_path)
        joblib.dump(self.scaler, scaler_path)
        metadata = {
            "cell_type": self.cell_type,
            "lookback": self.lookback,
            "horizon": self.horizon,
            "input_size": self.input_size,
            "hidden_size": self.hidden_size,
            "num_layers": self.num_layers,
            "dropout": self.dropout,
        }
        metadata_path.write_text(json.dumps(metadata, indent=2))
        logger.info("Saved %s forecaster artifacts at %s", self.cell_type, artifact_dir)
        return SequenceArtifact(model_path, scaler_path, metadata_path)

    @classmethod
    def load(cls, artifact_dir: Path, cell_type: str, horizon: int) -> "SequenceForecaster":
        suffix = f"{cell_type}_h{horizon}"
        metadata_path = artifact_dir / f"sequence_{suffix}_meta.json"
        if not metadata_path.exists():
            raise FileNotFoundError(f"Metadata not found for {suffix}")
        metadata = json.loads(metadata_path.read_text())
        instance = cls(
            cell_type=metadata["cell_type"],
            lookback=metadata["lookback"],
            horizon=metadata["horizon"],
            input_size=metadata["input_size"],
            hidden_size=metadata["hidden_size"],
            num_layers=metadata["num_layers"],
            dropout=metadata["dropout"],
        )
        model_path = artifact_dir / f"sequence_{suffix}.pt"
        scaler_path = artifact_dir / f"sequence_{suffix}_scaler.pkl"
        state_dict = torch.load(model_path, map_location=instance.device)
        instance.model.load_state_dict(state_dict)
        instance.scaler = joblib.load(scaler_path)
        instance.model.to(instance.device)
        instance.model.eval()
        logger.info("Loaded %s forecaster for horizon %s", cell_type, horizon)
        return instance


class RandomForestForecaster:
    def __init__(self, lookback: int, horizon: int) -> None:
        self.lookback = lookback
        self.horizon = horizon
        self.scaler = StandardScaler()
        self.model = RandomForestRegressor(
            n_estimators=200,
            max_depth=None,
            random_state=42,
            n_jobs=-1,
        )

    def fit(self, X: np.ndarray, y: np.ndarray) -> dict[str, float]:
        if X.size == 0:
            raise ValueError("Training data is empty. Provide more samples.")
        nsamples, lookback, nfeatures = X.shape
        X_flat = X.reshape(nsamples, lookback * nfeatures)
        X_scaled = self.scaler.fit_transform(X_flat)

        X_train, X_val, y_train, y_val = train_test_split(
            X_scaled, y, test_size=0.2, random_state=42
        )
        self.model.fit(X_train, y_train)
        preds = self.model.predict(X_val)
        metrics = {
            "mae": float(mean_absolute_error(y_val, preds)),
            "rmse": float(np.sqrt(mean_squared_error(y_val, preds))),
        }
        logger.info(
            "Trained RandomForest forecaster | horizon=%s | MAE=%.3f RMSE=%.3f",
            self.horizon,
            metrics["mae"],
            metrics["rmse"],
        )
        return metrics

    def predict(self, X: np.ndarray) -> np.ndarray:
        if X.ndim == 3:
            X_flat = X.reshape(X.shape[0], -1)
        else:
            X_flat = X.reshape(1, -1)
        X_scaled = self.scaler.transform(X_flat)
        return self.model.predict(X_scaled)

    def save(self, artifact_dir: Path) -> dict[str, Path]:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        suffix = f"rf_h{self.horizon}"
        model_path = artifact_dir / f"{suffix}.joblib"
        scaler_path = artifact_dir / f"{suffix}_scaler.joblib"
        joblib.dump(self.model, model_path)
        joblib.dump(self.scaler, scaler_path)
        logger.info("Saved RandomForest forecaster artifacts at %s", artifact_dir)
        return {"model": model_path, "scaler": scaler_path}

    @classmethod
    def load(cls, artifact_dir: Path, horizon: int) -> "RandomForestForecaster":
        suffix = f"rf_h{horizon}"
        model_path = artifact_dir / f"{suffix}.joblib"
        scaler_path = artifact_dir / f"{suffix}_scaler.joblib"
        if not model_path.exists():
            raise FileNotFoundError(f"RandomForest model not found at {model_path}")
        instance = cls(lookback=settings.lookback_window, horizon=horizon)
        instance.model = joblib.load(model_path)
        instance.scaler = joblib.load(scaler_path)
        logger.info("Loaded RandomForest forecaster for horizon=%s", horizon)
        return instance
