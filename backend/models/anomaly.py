from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import joblib
import numpy as np
import torch
import torch.nn as nn
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

from backend.config import get_settings
from backend.utils.logging import configure_logging

settings = get_settings()
logger = configure_logging("anomaly_models", level=settings.log_level)


class Autoencoder(nn.Module):
    def __init__(self, input_dim: int, latent_dim: int = 8) -> None:
        super().__init__()
        self.encoder = nn.Sequential(
            nn.Linear(input_dim, 32),
            nn.ReLU(),
            nn.Linear(32, 16),
            nn.ReLU(),
            nn.Linear(16, latent_dim),
        )
        self.decoder = nn.Sequential(
            nn.Linear(latent_dim, 16),
            nn.ReLU(),
            nn.Linear(16, 32),
            nn.ReLU(),
            nn.Linear(32, input_dim),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        latent = self.encoder(x)
        reconstructed = self.decoder(latent)
        return reconstructed


@dataclass
class AutoencoderArtifact:
    model_path: Path
    scaler_path: Path
    metadata_path: Path


class AutoencoderAnomalyDetector:
    def __init__(
        self,
        input_dim: int,
        latent_dim: int = 8,
        learning_rate: float = 1e-3,
        epochs: int = 40,
        batch_size: int = 64,
        threshold_factor: float = 2.5,
        device: Optional[str] = None,
    ) -> None:
        self.input_dim = input_dim
        self.latent_dim = latent_dim
        self.learning_rate = learning_rate
        self.epochs = epochs
        self.batch_size = batch_size
        self.threshold_factor = threshold_factor
        self.device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))

        self.model = Autoencoder(input_dim=input_dim, latent_dim=latent_dim).to(
            self.device
        )
        self.scaler = StandardScaler()
        self.threshold_: float | None = None

    def fit(self, X: np.ndarray) -> float:
        X_scaled = self.scaler.fit_transform(X)
        dataset = torch.utils.data.TensorDataset(
            torch.tensor(X_scaled, dtype=torch.float32)
        )
        loader = torch.utils.data.DataLoader(
            dataset, batch_size=self.batch_size, shuffle=True
        )

        criterion = nn.MSELoss()
        optimizer = torch.optim.Adam(self.model.parameters(), lr=self.learning_rate)
        self.model.train()
        for epoch in range(self.epochs):
            epoch_loss = 0.0
            for batch in loader:
                batch = batch[0].to(self.device)
                optimizer.zero_grad()
                reconstructed = self.model(batch)
                loss = criterion(reconstructed, batch)
                loss.backward()
                optimizer.step()
                epoch_loss += loss.item() * batch.size(0)
            epoch_loss /= len(dataset)
            logger.debug("Autoencoder epoch %d/%d loss %.5f", epoch + 1, self.epochs, epoch_loss)

        self.model.eval()
        with torch.no_grad():
            tensors = torch.tensor(X_scaled, dtype=torch.float32).to(self.device)
            recon = self.model(tensors).cpu().numpy()
        reconstruction_errors = np.mean((X_scaled - recon) ** 2, axis=1)
        self.threshold_ = float(np.mean(reconstruction_errors) + self.threshold_factor * np.std(reconstruction_errors))
        logger.info("Autoencoder trained with threshold %.6f", self.threshold_)
        return self.threshold_

    def score_samples(self, X: np.ndarray) -> np.ndarray:
        if self.threshold_ is None:
            raise RuntimeError("Model not fitted yet")
        X_scaled = self.scaler.transform(X)
        tensors = torch.tensor(X_scaled, dtype=torch.float32).to(self.device)
        self.model.eval()
        with torch.no_grad():
            recon = self.model(tensors).cpu().numpy()
        reconstruction_errors = np.mean((X_scaled - recon) ** 2, axis=1)
        return reconstruction_errors

    def predict(self, X: np.ndarray) -> np.ndarray:
        errors = self.score_samples(X)
        if self.threshold_ is None:
            raise RuntimeError("Threshold not computed")
        return (errors > self.threshold_).astype(int)

    def save(self, artifact_dir: Path) -> AutoencoderArtifact:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        model_path = artifact_dir / "autoencoder.pt"
        scaler_path = artifact_dir / "autoencoder_scaler.joblib"
        metadata_path = artifact_dir / "autoencoder_meta.json"
        torch.save(
            {
                "model_state": self.model.state_dict(),
                "threshold": self.threshold_,
            },
            model_path,
        )
        joblib.dump(self.scaler, scaler_path)
        metadata_path.write_text(
            json.dumps(
                {
                    "input_dim": self.input_dim,
                    "latent_dim": self.latent_dim,
                    "threshold_factor": self.threshold_factor,
                },
                indent=2,
            )
        )
        logger.info("Saved autoencoder anomaly detector artifacts to %s", artifact_dir)
        return AutoencoderArtifact(model_path, scaler_path, metadata_path)

    @classmethod
    def load(cls, artifact_dir: Path) -> "AutoencoderAnomalyDetector":
        metadata_path = artifact_dir / "autoencoder_meta.json"
        metadata = json.loads(metadata_path.read_text())
        instance = cls(
            input_dim=metadata["input_dim"],
            latent_dim=metadata["latent_dim"],
            threshold_factor=metadata["threshold_factor"],
        )
        model_path = artifact_dir / "autoencoder.pt"
        payload = torch.load(model_path, map_location=instance.device)
        instance.model.load_state_dict(payload["model_state"])
        instance.threshold_ = payload["threshold"]
        scaler_path = artifact_dir / "autoencoder_scaler.joblib"
        instance.scaler = joblib.load(scaler_path)
        instance.model.to(instance.device)
        instance.model.eval()
        logger.info("Loaded autoencoder anomaly detector")
        return instance


class IsolationForestDetector:
    def __init__(self, contamination: float = 0.01, random_state: int = 42) -> None:
        self.contamination = contamination
        self.scaler = StandardScaler()
        self.model = IsolationForest(
            contamination=contamination,
            n_estimators=300,
            max_samples="auto",
            random_state=random_state,
            n_jobs=-1,
        )

    def fit(self, X: np.ndarray) -> None:
        X_scaled = self.scaler.fit_transform(X)
        self.model.fit(X_scaled)
        logger.info("IsolationForest trained with contamination %.4f", self.contamination)

    def predict(self, X: np.ndarray) -> np.ndarray:
        X_scaled = self.scaler.transform(X)
        preds = self.model.predict(X_scaled)
        return np.where(preds == -1, 1, 0)

    def score_samples(self, X: np.ndarray) -> np.ndarray:
        X_scaled = self.scaler.transform(X)
        return -self.model.score_samples(X_scaled)

    def save(self, artifact_dir: Path) -> dict[str, Path]:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        model_path = artifact_dir / "isolation_forest.joblib"
        scaler_path = artifact_dir / "isolation_forest_scaler.joblib"
        joblib.dump(self.model, model_path)
        joblib.dump(self.scaler, scaler_path)
        logger.info("Saved IsolationForest anomaly detector artifacts to %s", artifact_dir)
        return {"model": model_path, "scaler": scaler_path}

    @classmethod
    def load(cls, artifact_dir: Path) -> "IsolationForestDetector":
        model_path = artifact_dir / "isolation_forest.joblib"
        scaler_path = artifact_dir / "isolation_forest_scaler.joblib"
        instance = cls()
        instance.model = joblib.load(model_path)
        instance.scaler = joblib.load(scaler_path)
        logger.info("Loaded IsolationForest anomaly detector")
        return instance
