"""Paths, environment-driven settings and the game config."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from fanpulse_live import __version__

# src/fanpulse_live/config.py -> repo root (the project is installed editable).
PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(PROJECT_ROOT / ".env")

DEFAULT_USER_AGENT = f"fanpulse-live/{__version__} (internal prototype)"
GAME_CONFIG_PATH = PROJECT_ROOT / "config" / "game.yaml"


def data_dir() -> Path:
    override = os.getenv("FANPULSE_LIVE_DATA_DIR")
    return Path(override) if override else PROJECT_ROOT / "data"


def raw_dir() -> Path:
    return data_dir() / "raw"


def processed_dir() -> Path:
    return data_dir() / "processed"


def ticks_dir() -> Path:
    return data_dir() / "ticks"


def user_agent() -> str:
    return os.getenv("FANPULSE_LIVE_USER_AGENT") or DEFAULT_USER_AGENT


def load_nicknames() -> dict[str, int]:
    """Nickname to MLB person id, from ``config/nicknames.yaml``."""
    path = PROJECT_ROOT / "config" / "nicknames.yaml"
    if not path.exists():
        return {}
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("nicknames") or {}


def load_game(path: Path | None = None) -> dict[str, Any]:
    """The game, its threads, its streams and the tick settings, from ``config/game.yaml``."""
    return yaml.safe_load((path or GAME_CONFIG_PATH).read_text(encoding="utf-8"))
