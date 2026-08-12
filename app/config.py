# app/config.py
# REQUIRED FOR DEPLOY

import os
from dataclasses import dataclass


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _admin_ids() -> tuple[int, ...]:
    return tuple(
        dict.fromkeys(
            int(value.strip())
            for value in os.getenv("ADMIN_IDS", "").split(",")
            if value.strip().isdigit()
        )
    )


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    admin_ids: tuple[int, ...] = _admin_ids()

    mongodb_uri: str = os.getenv("MONGODB_URI", "").strip()
    mongodb_database: str = os.getenv(
        "MONGODB_DATABASE", "image_ai_bot"
    ).strip()

    settings_encryption_key: str = os.getenv(
        "SETTINGS_ENCRYPTION_KEY", ""
    ).strip()

    public_base_url: str = os.getenv(
        "PUBLIC_BASE_URL", ""
    ).strip().rstrip("/")

    webhook_secret: str = os.getenv(
        "WEBHOOK_SECRET", ""
    ).strip()

    port: int = _int_env("PORT", 8000, 1, 65535)
    default_max_upload_mb: int = _int_env(
        "MAX_UPLOAD_MB", 20, 1, 20
    )
    default_timeout: int = _int_env(
        "PIXELCUT_TIMEOUT", 120, 10, 600
    )

    uptime_url: str = os.getenv("UPTIME_URL", "").strip()
    uptime_interval: int = _int_env(
        "UPTIME_INTERVAL", 300, 60, 1800
    )


settings = Settings()
