# app/security.py

import base64
import hashlib

from cryptography.fernet import (
    Fernet,
    InvalidToken,
)


def _fernet(secret: str) -> Fernet:
    if not secret:
        raise RuntimeError(
            "SETTINGS_ENCRYPTION_KEY is not configured."
        )

    key = base64.urlsafe_b64encode(
        hashlib.sha256(
            secret.encode("utf-8")
        ).digest()
    )

    return Fernet(key)


def encrypt_secret(
    value: str,
    secret: str,
) -> str:
    if not value:
        raise ValueError(
            "Secret value cannot be empty."
        )

    return (
        _fernet(secret)
        .encrypt(value.encode("utf-8"))
        .decode("ascii")
    )


def decrypt_secret(
    value: str,
    secret: str,
) -> str:
    try:
        return (
            _fernet(secret)
            .decrypt(value.encode("ascii"))
            .decode("utf-8")
        )

    except (
        InvalidToken,
        ValueError,
        TypeError,
    ) as exc:
        raise RuntimeError(
            "Stored API credential cannot be decrypted."
        ) from exc


def mask_secret(value: str) -> str:
    if len(value) <= 8:
        return "••••••••"

    return (
        value[:4]
        + "••••••••"
        + value[-4:]
    )
