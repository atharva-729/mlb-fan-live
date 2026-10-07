"""Parquet tables under ``data/processed/<table>/<game_pk>.parquet``.

One file per game keeps reruns idempotent.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from fanpulse_live import config


def table_path(table: str, game_pk: int) -> Path:
    return config.processed_dir() / table / f"{game_pk}.parquet"


def write_table(table: str, game_pk: int, frame: pd.DataFrame) -> Path:
    path = table_path(table, game_pk)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return path


def read_table(table: str, game_pk: int) -> pd.DataFrame:
    return pd.read_parquet(table_path(table, game_pk))
