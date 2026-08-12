# app/temp_storage.py
# Backwards-compatible durable media API backed by MongoDB GridFS.

from datetime import datetime

from app.database import (
    save_media,
    get_media,
    delete_media,
    cleanup_expired_media,
)


def new_file_id(extension: str = ".jpg") -> str:
    # File IDs are created by GridFS at save time. This function remains only
    # for compatibility with older imports; callers should use save_temp_file.
    return ""


async def save_temp_file(file_id, data, content_type, filename, expires_at: datetime):
    return await save_media(data, content_type, filename, expires_at, purpose="image")


async def get_temp_file(file_id: str):
    return await get_media(file_id)


async def read_temp_file(file_id: str):
    return await get_media(file_id)


async def delete_temp_file(file_id: str) -> None:
    await delete_media(file_id)


def cleanup_expired() -> None:
    # Kept for compatibility. Cleanup is async because GridFS is async.
    return None


async def cleanup_expired_async():
    await cleanup_expired_media()
