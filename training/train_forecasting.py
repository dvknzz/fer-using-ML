from __future__ import annotations

import argparse

from backend.services.forecasting_service import ForecastingService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Train forecasting model (LSTM/GRU/RandomForest) using data from InfluxDB"
    )
    parser.add_argument(
        "--model-type",
        choices=["lstm", "gru", "random_forest"],
        default="lstm",
        help="Loại mô hình dùng để dự đoán PM2.5",
    )
    parser.add_argument(
        "--horizon",
        type=int,
        default=30,
        help="Khoảng thời gian dự đoán (phút)",
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
    service = ForecastingService()
    result = service.train_model(
        model_type=args.model_type,
        horizon_minutes=args.horizon,
        history_minutes=args.history,
    )
    print("Training completed:", result)


if __name__ == "__main__":
    main()
