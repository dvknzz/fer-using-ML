from __future__ import annotations

import argparse

from backend.services.anomaly_service import AnomalyDetectionService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Huấn luyện mô hình phát hiện bất thường (IsolationForest / Autoencoder)"
    )
    parser.add_argument(
        "--detector-type",
        choices=["isolation_forest", "autoencoder"],
        default="isolation_forest",
        help="Thuật toán phát hiện bất thường sử dụng",
    )
    parser.add_argument(
        "--history",
        type=int,
        default=24 * 12,
        help="Số phút dữ liệu quá khứ dùng để huấn luyện",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    service = AnomalyDetectionService()
    service.train_detector(
        detector_type=args.detector_type,
        history_minutes=args.history,
    )
    print(f"Training {args.detector_type} completed.")


if __name__ == "__main__":
    main()
