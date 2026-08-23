"""
Configuration module — loads and validates all settings from environment variables.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class MoodleConfig:
    host: str
    token: str
    user: str
    password: str


@dataclass(frozen=True)
class BotConfig:
    max_file_size_mb: float
    part_size_mb: float
    upload_delay_seconds: float
    log_level: str
    bot_email: str          # Dirección de correo del bot (para los enlaces mailto:)


@dataclass(frozen=True)
class AppConfig:
    moodle: MoodleConfig
    bot: BotConfig


def _require(key: str) -> str:
    value = os.getenv(key, "").strip()
    if not value:
        raise EnvironmentError(
            f"Required environment variable '{key}' is missing or empty. "
            "Check your .env file."
        )
    return value


def load_config() -> AppConfig:
    """Load and validate all configuration from environment. Raises on missing required values."""
    return AppConfig(
        moodle=MoodleConfig(
            host=_require("MOODLE_HOST").rstrip("/"),
            token=_require("MOODLE_TOKEN"),
            user=_require("MOODLE_USER"),
            password=_require("MOODLE_PASS"),
        ),
        bot=BotConfig(
            max_file_size_mb=float(os.getenv("MAX_FILE_SIZE_MB", "999")),
            part_size_mb=float(os.getenv("PART_SIZE_MB", "99")),
            upload_delay_seconds=float(os.getenv("UPLOAD_DELAY_SECONDS", "2")),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            bot_email=_require("BOT_EMAIL"),
        ),
    )


# Singleton — import this everywhere
config = load_config()
