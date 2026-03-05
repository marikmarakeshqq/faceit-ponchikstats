from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _parse_admin_ids(raw_value: str) -> set[int]:
    result: set[int] = set()
    for chunk in raw_value.split(","):
        cleaned = chunk.strip()
        if not cleaned:
            continue
        if cleaned.isdigit():
            result.add(int(cleaned))
    return result


@dataclass(slots=True)
class BotConfig:
    telegram_token: str
    faceit_api_key: str
    admin_ids: set[int]
    database_path: Path
    default_poll_interval_sec: int
    request_timeout_sec: float
    card_output_dir: Path

    @classmethod
    def from_env(cls) -> "BotConfig":
        load_dotenv()

        telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        faceit_api_key = os.getenv("FACEIT_API_KEY", "").strip()
        admin_ids = _parse_admin_ids(os.getenv("ADMIN_IDS", ""))

        if not telegram_token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required.")
        if not faceit_api_key:
            raise ValueError("FACEIT_API_KEY is required.")
        if not admin_ids:
            raise ValueError("ADMIN_IDS must contain at least one Telegram user id.")

        return cls(
            telegram_token=telegram_token,
            faceit_api_key=faceit_api_key,
            admin_ids=admin_ids,
            database_path=Path(os.getenv("DATABASE_PATH", "data/bot.db")),
            default_poll_interval_sec=max(10, int(os.getenv("DEFAULT_POLL_INTERVAL_SEC", "180"))),
            request_timeout_sec=max(5.0, float(os.getenv("REQUEST_TIMEOUT_SEC", "20"))),
            card_output_dir=Path(os.getenv("CARD_OUTPUT_DIR", "data/cards")),
        )

    def ensure_directories(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.card_output_dir.mkdir(parents=True, exist_ok=True)
