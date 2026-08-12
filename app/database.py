# app/database.py

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from pymongo import ReturnDocument
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorGridFSBucket

from app.config import settings

client = None
db = None
media_bucket = None

DEFAULT_USER = {
    "format": "PNG",
    "jpeg_quality": 95,
    "prefix": "",
    "suffix": "",
    "scale_in_filename": True,
    "thumbnail": True,
}

DEFAULT_BOT = {
    "_id": "main",
    "privacy": {
        "encryption": True,
        "show_api_keys": False,
    },
    "processing": {
        "timeout": 120,
        "max_upload_mb": 20,
    },
    "output": {
        "jpeg_quality": 95,
        "thumbnail": True,
    },
    "rotation": {
        "enabled": True,
    },
}


def _copy(value):
    return deepcopy(value)


def _now():
    return datetime.now(timezone.utc)


def _oid(value):
    try:
        return ObjectId(value)
    except Exception:
        return None


async def connect():
    global client, db, media_bucket

    if not settings.mongodb_uri:
        raise RuntimeError("MONGODB_URI is not configured.")

    client = AsyncIOMotorClient(
        settings.mongodb_uri,
        serverSelectionTimeoutMS=10000,
        connectTimeoutMS=10000,
        socketTimeoutMS=30000,
        retryWrites=True,
    )

    try:
        await client.admin.command("ping")
        db = client[settings.mongodb_database]
        media_bucket = AsyncIOMotorGridFSBucket(db, bucket_name="media")

        await db.users.create_index("telegram_id", unique=True)
        await db.apis.create_index(
            [("enabled", 1), ("last_used", 1), ("created_at", 1)]
        )
        await db.jobs.create_index([("user_id", 1), ("status", 1), ("updated_at", -1)])
        await db.jobs.create_index([("status", 1), ("updated_at", 1)])
        await db.jobs.create_index([("expires_at", 1)])
        await db.media.files.create_index("metadata.expires_at", expireAfterSeconds=0)
        await get_bot_settings()

    except Exception:
        client.close()
        client = None
        db = None
        media_bucket = None
        raise


async def close():
    global client, db, media_bucket
    if client:
        client.close()
    client = None
    db = None
    media_bucket = None


def get_db():
    if db is None:
        raise RuntimeError("MongoDB is not connected.")
    return db


def get_media_bucket():
    if media_bucket is None:
        raise RuntimeError("MongoDB media storage is not connected.")
    return media_bucket


async def ensure_user(uid: int):
    await get_db().users.update_one(
        {"telegram_id": uid},
        {"$setOnInsert": {"telegram_id": uid, "settings": _copy(DEFAULT_USER), "created_at": _now()}},
        upsert=True,
    )


async def get_user_settings(uid: int):
    await ensure_user(uid)
    doc = await get_db().users.find_one({"telegram_id": uid}, {"settings": 1})
    data = _copy(DEFAULT_USER)
    if doc:
        stored = doc.get("settings", {})
        if isinstance(stored, dict):
            data.update(stored)
    return data


async def update_user_settings(uid: int, values: dict):
    await ensure_user(uid)
    updates = {f"settings.{key}": value for key, value in values.items()}
    if updates:
        await get_db().users.update_one({"telegram_id": uid}, {"$set": updates})


async def reset_user_settings(uid: int):
    await get_db().users.update_one(
        {"telegram_id": uid},
        {"$set": {"settings": _copy(DEFAULT_USER)}},
        upsert=True,
    )


async def get_bot_settings():
    database = get_db()
    doc = await database.bot_settings.find_one({"_id": "main"})
    if not doc:
        await database.bot_settings.update_one(
            {"_id": "main"},
            {"$setOnInsert": _copy(DEFAULT_BOT)},
            upsert=True,
        )
        doc = await database.bot_settings.find_one({"_id": "main"})

    merged = _copy(DEFAULT_BOT)
    for section in ("privacy", "processing", "output", "rotation"):
        stored = doc.get(section, {}) if doc else {}
        if isinstance(stored, dict):
            merged[section].update(stored)
    return merged


async def set_bot(path: str, value: Any):
    if not path or path.startswith("$") or "$" in path:
        raise ValueError("Invalid bot setting path.")
    await get_db().bot_settings.update_one(
        {"_id": "main"},
        {"$set": {path: value}},
        upsert=True,
    )


async def add_api(label: str, encrypted: str):
    label = label.strip()
    if not label:
        raise ValueError("API label cannot be empty.")
    if not encrypted:
        raise ValueError("Encrypted API key cannot be empty.")
    await get_db().apis.insert_one({
        "label": label,
        "key": encrypted,
        "enabled": True,
        "created_at": _now(),
        "last_used": None,
    })


async def list_apis():
    return await get_db().apis.find().sort([("created_at", 1)]).to_list(length=None)


async def toggle_api(oid: str):
    obj = _oid(oid)
    if not obj:
        return False
    doc = await get_db().apis.find_one({"_id": obj})
    if not doc:
        return False
    await get_db().apis.update_one({"_id": obj}, {"$set": {"enabled": not bool(doc.get("enabled", False))}})
    return True


async def delete_api(oid: str):
    obj = _oid(oid)
    if not obj:
        return False
    result = await get_db().apis.delete_one({"_id": obj})
    return result.deleted_count == 1


async def next_api(excluded_ids=None, rotate=True):
    excluded_ids = excluded_ids or []
    query = {"enabled": True}
    if excluded_ids:
        query["_id"] = {"$nin": excluded_ids}
    sort_order = [("last_used", 1), ("created_at", 1)] if rotate else [("created_at", 1)]
    return await get_db().apis.find_one(query, sort=sort_order)


async def mark_api_used(oid):
    obj = _oid(oid)
    if not obj:
        return False
    result = await get_db().apis.update_one({"_id": obj}, {"$set": {"last_used": _now()}})
    return result.modified_count == 1


# ---------------------------------------------------------------------------
# Durable GridFS media storage
# ---------------------------------------------------------------------------

async def save_media(data: bytes, content_type: str, filename: str, expires_at: datetime, purpose: str = "image") -> str:
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ValueError("Media data is empty.")
    if content_type not in {"image/jpeg", "image/png"}:
        raise ValueError("Unsupported media content type.")
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    file_id = await get_media_bucket().upload_from_stream(
        filename,
        bytes(data),
        metadata={
            "content_type": content_type,
            "expires_at": expires_at,
            "purpose": purpose,
        },
    )
    return str(file_id)


async def get_media(file_id: str):
    obj = _oid(file_id)
    if not obj:
        return None
    try:
        grid_out = await get_media_bucket().open_download_stream(obj)
        data = await grid_out.read()
        meta = grid_out.metadata or {}
        content_type = meta.get("content_type", "image/jpeg")
        filename = grid_out.filename or "image.jpg"
        expires_at = meta.get("expires_at")
        if expires_at and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at and _now() >= expires_at:
            await delete_media(file_id)
            return None
        return {
            "file_id": str(file_id),
            "content_type": content_type,
            "filename": filename,
            "expires_at": expires_at,
        }, data
    except Exception:
        return None


async def delete_media(file_id: str):
    obj = _oid(file_id)
    if not obj:
        return
    try:
        await get_media_bucket().delete(obj)
    except Exception:
        pass


async def cleanup_expired_media():
    database = get_db()
    now = _now()
    cursor = database.media.files.find({"metadata.expires_at": {"$lte": now}}, {"_id": 1})
    ids = [doc["_id"] async for doc in cursor]
    for obj in ids:
        try:
            await get_media_bucket().delete(obj)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Durable image jobs
# ---------------------------------------------------------------------------

JOB_ACTIVE = {"awaiting_action", "queued", "processing", "ready_to_send"}
JOB_RECOVERABLE = {"queued", "processing", "ready_to_send"}


async def create_job(user_id: int, chat_id: int, message_id: int, file_id: str, filename: str, width: int, height: int, expires_at: datetime):
    now = _now()
    result = await get_db().jobs.insert_one({
        "user_id": user_id,
        "chat_id": chat_id,
        "message_id": message_id,
        "file_id": file_id,
        "filename": filename,
        "width": width,
        "height": height,
        "status": "awaiting_action",
        "state": "operation",
        "operation": None,
        "scale": None,
        "ratio": None,
        "custom_size": None,
        "side": None,
        "expand_amount": None,
        "target_size": None,
        "output_file_id": None,
        "output_filename": None,
        "output_content_type": None,
        "attempts": 0,
        "last_error": None,
        "created_at": now,
        "updated_at": now,
        "expires_at": expires_at,
    })
    return str(result.inserted_id)


async def get_job(job_id: str, user_id: int | None = None):
    obj = _oid(job_id)
    if not obj:
        return None
    query = {"_id": obj}
    if user_id is not None:
        query["user_id"] = user_id
    return await get_db().jobs.find_one(query)


async def get_active_job(user_id: int):
    return await get_db().jobs.find_one(
        {"user_id": user_id, "status": {"$in": list(JOB_ACTIVE)}},
        sort=[("created_at", -1)],
    )


async def update_job(job_id: str, values: dict):
    obj = _oid(job_id)
    if not obj:
        return False
    values = dict(values)
    values["updated_at"] = _now()
    result = await get_db().jobs.update_one({"_id": obj}, {"$set": values})
    return result.modified_count == 1


async def claim_job(job_id: str):
    obj = _oid(job_id)
    if not obj:
        return None
    return await get_db().jobs.find_one_and_update(
        {"_id": obj, "status": "queued"},
        {"$set": {"status": "processing", "started_at": _now(), "updated_at": _now()}, "$inc": {"attempts": 1}},
        return_document=ReturnDocument.AFTER,
    )


async def set_job_status(job_id: str, status: str, **values):
    values["status"] = status
    values["updated_at"] = _now()
    return await update_job(job_id, values)


async def recoverable_jobs(limit: int = 100):
    return await get_db().jobs.find(
        {"status": {"$in": list(JOB_RECOVERABLE)}, "expires_at": {"$gt": _now()}},
        sort=[("updated_at", 1)],
    ).to_list(length=limit)


async def cleanup_expired_jobs():
    database = get_db()
    now = _now()
    jobs = await database.jobs.find(
        {"expires_at": {"$lte": now}, "status": {"$ne": "completed"}},
        {"file_id": 1, "output_file_id": 1},
    ).to_list(length=500)
    for job in jobs:
        for key in ("file_id", "output_file_id"):
            if job.get(key):
                await delete_media(job[key])
    await database.jobs.delete_many({"expires_at": {"$lte": now}})
