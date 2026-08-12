# app/config.py
# REQUIRED FOR DEPLOY

import os
from dataclasses import dataclass


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        return default

    return max(minimum, min(maximum, value))


def _admin_ids() -> tuple[int, ...]:
    values = []

    for value in os.getenv("ADMIN_IDS", "").split(","):
        value = value.strip()

        if value.isdigit():
            values.append(int(value))

    return tuple(dict.fromkeys(values))


def _str_env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class Settings:
    # Telegram
    telegram_bot_token: str = _str_env("TELEGRAM_BOT_TOKEN")
    admin_ids: tuple[int, ...] = _admin_ids()

    # MongoDB
    mongodb_uri: str = _str_env("MONGODB_URI")
    mongodb_database: str = _str_env(
        "MONGODB_DATABASE",
        "image_ai_bot",
    )

    # Encryption
    settings_encryption_key: str = _str_env(
        "SETTINGS_ENCRYPTION_KEY"
    )

    # Public URL / webhook
    public_base_url: str = _str_env(
        "PUBLIC_BASE_URL"
    ).rstrip("/")

    webhook_secret: str = _str_env(
        "WEBHOOK_SECRET"
    )

    # Pixelcut
    #
    # Optional fallback API key.
    # The main system still supports encrypted API keys
    # stored in MongoDB.
    pixelcut_api_key: str = _str_env(
        "PIXELCUT_API_KEY"
    )

    # Processing
    port: int = _int_env(
        "PORT",
        8000,
        1,
        65535,
    )

    default_max_upload_mb: int = _int_env(
        "MAX_UPLOAD_MB",
        20,
        1,
        20,
    )

    default_timeout: int = _int_env(
        "PIXELCUT_TIMEOUT",
        120,
        10,
        600,
    )

    # Uptime
    uptime_url: str = _str_env(
        "UPTIME_URL"
    )

    uptime_interval: int = _int_env(
        "UPTIME_INTERVAL",
        300,
        60,
        1800,
    )


settings = Settings()
