# app/job_manager.py

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from io import BytesIO

from telegram import InputFile
from telegram.error import TelegramError

from app.config import settings
from app.database import (
    claim_job,
    delete_media,
    get_bot_settings,
    get_job,
    get_media,
    get_user_settings,
    recoverable_jobs,
    save_media,
    set_job_status,
)
from app.image_utils import convert_output, make_thumbnail, normalize_filename
from app.services.expand import build_expansion, expand
from app.services.remove_bg import remove_background
from app.services.upscale import upscale

logger = logging.getLogger(__name__)

_queue: asyncio.Queue[str] | None = None
_worker_tasks: list[asyncio.Task] = []
_stop_event: asyncio.Event | None = None
_enqueued: set[str] = set()
_bot = None


def _now():
    return datetime.now(timezone.utc)


def is_running() -> bool:
    return bool(_worker_tasks) and any(not task.done() for task in _worker_tasks)


def worker_count() -> int:
    return len(_worker_tasks)


def queue_size() -> int:
    return _queue.qsize() if _queue else 0


async def enqueue_job(job_id: str) -> bool:
    global _queue
    if _queue is None:
        return False
    if job_id in _enqueued:
        return True
    job = await get_job(job_id)
    if not job:
        return False
    if job.get("status") not in {"queued", "ready_to_send"}:
        return False
    _enqueued.add(job_id)
    await _queue.put(job_id)
    return True


async def _recover_jobs():
    jobs = await recoverable_jobs(200)
    recovered = 0
    for job in jobs:
        job_id = str(job["_id"])
        if job.get("status") == "processing":
            await set_job_status(
                job_id,
                "queued",
                last_error="Recovered automatically after restart.",
            )
        if await enqueue_job(job_id):
            recovered += 1
    logger.info("Recovered %s durable image job(s).", recovered)


async def start_workers(bot, worker_count: int = 2):
    global _queue, _worker_tasks, _stop_event, _bot
    if is_running():
        return
    _bot = bot
    _queue = asyncio.Queue()
    _stop_event = asyncio.Event()
    _enqueued.clear()
    worker_count = max(1, min(int(worker_count), 4))
    _worker_tasks = [asyncio.create_task(_worker_loop(i + 1)) for i in range(worker_count)]
    await _recover_jobs()
    logger.info("Image job workers started: %s", worker_count)


async def stop_workers():
    global _queue, _worker_tasks, _stop_event, _bot
    if not _worker_tasks:
        return
    if _stop_event:
        _stop_event.set()
    for task in _worker_tasks:
        task.cancel()
    for task in _worker_tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Image worker shutdown failed")
    _worker_tasks = []
    _queue = None
    _stop_event = None
    _bot = None
    _enqueued.clear()


async def _worker_loop(number: int):
    logger.info("Image worker %s started.", number)
    while True:
        try:
            job_id = await _queue.get()
            try:
                _enqueued.discard(job_id)
                await process_job(job_id)
            finally:
                _queue.task_done()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Worker %s crashed while handling a job.", number)
            await asyncio.sleep(1)


async def process_job(job_id: str):
    job = await get_job(job_id)
    if not job:
        return

    status = job.get("status")
    if status == "ready_to_send":
        await deliver_job(job_id)
        return
    if status != "queued":
        return

    claimed = await claim_job(job_id)
    if not claimed:
        return
    job = claimed

    try:
        if not settings.public_base_url:
            raise RuntimeError("PUBLIC_BASE_URL is not configured.")

        bot_settings = await get_bot_settings()
        user_settings = await get_user_settings(job["user_id"])
        file_record = await get_media(job["file_id"])
        if not file_record:
            raise RuntimeError("Source image is missing or expired.")

        image_url = f"{settings.public_base_url}/media/{job['file_id']}"
        operation = job.get("operation")
        scale = int(job.get("scale") or 2)
        result = None
        content_type = None

        await _send_message(job["chat_id"], f"⏳ Processing {operation.replace('_', ' ')} {scale}×...")

        if operation == "upscale":
            result, content_type = await upscale(image_url, scale)
        elif operation == "remove":
            result, content_type = await remove_background(image_url)
            if scale in (2, 4):
                intermediate_id = await save_media(
                    result,
                    content_type,
                    "remove-bg.png" if content_type == "image/png" else "remove-bg.jpg",
                    _now() + timedelta(minutes=30),
                    purpose="intermediate",
                )
                try:
                    result, content_type = await upscale(
                        f"{settings.public_base_url}/media/{intermediate_id}",
                        scale,
                    )
                finally:
                    await delete_media(intermediate_id)
        elif operation == "expand":
            result, content_type = await _run_expand(job, image_url)
            if scale in (2, 4):
                intermediate_id = await save_media(
                    result,
                    content_type,
                    "expanded.png" if content_type == "image/png" else "expanded.jpg",
                    _now() + timedelta(minutes=30),
                    purpose="intermediate",
                )
                try:
                    result, content_type = await upscale(
                        f"{settings.public_base_url}/media/{intermediate_id}",
                        scale,
                    )
                finally:
                    await delete_media(intermediate_id)
        else:
            raise ValueError("Unknown image operation.")

        if not result:
            raise RuntimeError("Image processing returned an empty result.")

        quality = min(
            max(1, int(user_settings.get("jpeg_quality", 95))),
            max(1, min(100, int(bot_settings.get("output", {}).get("jpeg_quality", 95)))),
        )
        result, output_type, ext = convert_output(
            bytes(result),
            user_settings.get("format", "PNG"),
            quality,
        )
        filename = normalize_filename(
            job.get("filename", "image"),
            user_settings.get("prefix", ""),
            user_settings.get("suffix", ""),
            scale,
            ext,
            bool(user_settings.get("scale_in_filename", True)),
        )

        # Persist the final result before touching Telegram. If the process dies
        # now, the startup recovery path can send it after restart.
        expires_at = _now() + timedelta(hours=24)
        output_id = await save_media(result, output_type, filename, expires_at, purpose="output")

        await set_job_status(
            job_id,
            "ready_to_send",
            output_file_id=output_id,
            output_filename=filename,
            output_content_type=output_type,
            last_error=None,
        )

        await deliver_job(job_id)

    except Exception as exc:
        logger.exception("Durable image job failed: %s", job_id)
        attempts = int(job.get("attempts", 1))
        # Retry transient failures a few times. The job remains in MongoDB, so
        # a process restart can also recover it.
        retryable = attempts < 3 and not isinstance(exc, ValueError)
        if retryable:
            await set_job_status(job_id, "queued", last_error=str(exc))
            await asyncio.sleep(min(2 ** attempts, 10))
            await enqueue_job(job_id)
        else:
            await set_job_status(job_id, "failed", last_error=str(exc), failed_at=_now())
            await _send_message(job["chat_id"], f"❌ {exc}")


async def _run_expand(job, image_url: str):
    original = await get_media(job["file_id"])
    if not original:
        raise RuntimeError("Source image is missing or expired.")
    _, data = original
    from app.image_utils import image_info
    width, height, _ = image_info(data)

    if job.get("target_size"):
        target_w, target_h = job["target_size"]
    elif job.get("expand_amount"):
        amount = int(job["expand_amount"])
        side = job.get("side")
        target_w, target_h = width, height
        if side in ("left", "right"):
            target_w += amount
        elif side in ("top", "bottom"):
            target_h += amount
        elif side == "all":
            target_w += amount * 2
            target_h += amount * 2
        else:
            raise ValueError("Invalid expand side.")
    else:
        raise ValueError("Expand settings are incomplete.")

    side = job.get("side")
    build_expansion(width, height, int(target_w), int(target_h), side)
    return await expand(image_url, width, height, int(target_w), int(target_h), side)


async def deliver_job(job_id: str):
    if _bot is None:
        return False
    job = await get_job(job_id)
    if not job or job.get("status") != "ready_to_send":
        return False
    output_id = job.get("output_file_id")
    if not output_id:
        return False
    raw = await get_media(output_id)
    if not raw:
        await set_job_status(job_id, "failed", last_error="Saved output is missing.")
        return False
    meta, data = raw
    try:
        thumbnail = None
        bot_settings = await get_bot_settings()
        user_settings = await get_user_settings(job["user_id"])
        if user_settings.get("thumbnail") and bot_settings.get("output", {}).get("thumbnail", True):
            try:
                thumb = make_thumbnail(data)
                thumbnail = InputFile(BytesIO(thumb), filename="thumb.jpg")
            except Exception:
                logger.exception("Thumbnail creation failed for job %s", job_id)

        await _bot.send_document(
            chat_id=job["chat_id"],
            document=BytesIO(data),
            filename=job.get("output_filename") or meta.get("filename") or "result.jpg",
            thumbnail=thumbnail,
            caption=f"✅ {str(job.get('operation', 'Image')).replace('_', ' ').title()} {int(job.get('scale') or 1)}× complete.",
        )
        await set_job_status(job_id, "completed", completed_at=_now())
        await delete_media(job.get("file_id"))
        await delete_media(output_id)
        logger.info("Durable image job completed: %s", job_id)
        return True
    except TelegramError as exc:
        logger.exception("Telegram delivery failed for job %s", job_id)
        await set_job_status(job_id, "ready_to_send", last_error=str(exc))
        return False


async def _send_message(chat_id: int, text: str):
    if _bot is None:
        return
    try:
        await _bot.send_message(chat_id=chat_id, text=text)
    except TelegramError:
        logger.exception("Failed to send job status message to %s", chat_id)
