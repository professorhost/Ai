# app/handlers/image.py

import logging
import math
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from app.config import settings
from app.database import (
    create_job,
    delete_media,
    get_active_job,
    get_bot_settings,
    get_job,
    get_media,
    set_job_status,
    update_job,
)
from app.image_utils import image_info, validate_image
from app.job_manager import enqueue_job
from app.keyboards import operations, ratios, scales, sides
from app.database import save_media

logger = logging.getLogger(__name__)

RATIOS = {
    "1:1": (1, 1),
    "4:3": (4, 3),
    "4:5": (4, 5),
    "9:16": (9, 16),
    "16:9": (16, 9),
    "2.39:1": (2.39, 1),
    "a4": (210, 297),
    "letter": (8.5, 11),
}
VALID_SIDES = {"left", "right", "top", "bottom", "all"}


def _target_dimensions(width: int, height: int, ratio: str):
    if ratio not in RATIOS:
        raise ValueError("Invalid target ratio.")
    rw, rh = RATIOS[ratio]
    target_ratio = rw / rh
    if width <= 0 or height <= 0:
        raise ValueError("Invalid image dimensions.")
    if width / height > target_ratio:
        return int(width), int(math.ceil(width / target_ratio))
    return int(math.ceil(height * target_ratio)), int(height)


def _parse_custom(text: str):
    raw = (text or "").strip().lower().replace("×", "x").replace(" ", "")
    parts = raw.split("x")
    if len(parts) != 2:
        raise ValueError("Use WIDTH x HEIGHT, for example 1920 x 1080.")
    try:
        width, height = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise ValueError("Width and height must be numbers.") from exc
    if not (128 <= width <= 6000 and 128 <= height <= 6000):
        raise ValueError("Width and height must be between 128 and 6000 pixels.")
    return width, height


async def _safe_answer(q):
    try:
        await q.answer()
    except BadRequest as exc:
        message = str(exc).lower()
        if any(x in message for x in ("query is too old", "query id is invalid", "response timeout expired")):
            logger.warning("Ignoring expired Telegram callback: %s", exc)
            return
        logger.exception("Telegram callback answer failed")
    except TelegramError:
        logger.exception("Telegram callback answer failed")


async def _safe_edit(q, text, reply_markup=None):
    try:
        await q.edit_message_text(text=text, reply_markup=reply_markup)
        return True
    except BadRequest as exc:
        message = str(exc).lower()
        if any(x in message for x in ("message is not modified", "message to edit not found", "query is too old")):
            logger.warning("Ignoring Telegram edit error: %s", exc)
            return False
        raise


async def _job_from_callback(q, job_id: str | None):
    uid = q.from_user.id
    if job_id:
        job = await get_job(job_id, uid)
    else:
        job = await get_active_job(uid)
    if not job:
        await q.message.reply_text("❌ Image session not found. Send the image again.")
        return None
    if job.get("status") in {"completed", "cancelled", "failed"}:
        await q.message.reply_text("❌ This image session is no longer active. Send the image again.")
        return None
    return job


async def receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    try:
        bot = await get_bot_settings()
        configured_limit = int((bot.get("processing") or {}).get("max_upload_mb", settings.default_max_upload_mb))
        limit_mb = max(1, min(configured_limit, settings.default_max_upload_mb, 20))
        limit = limit_mb * 1024 * 1024

        doc = update.message.document
        photo = update.message.photo[-1] if update.message.photo else None
        if doc:
            mime = (doc.mime_type or "").lower()
            if mime not in {"image/jpeg", "image/png"}:
                raise ValueError("Only JPG/JPEG/PNG images are supported.")
            if doc.file_size and doc.file_size > limit:
                raise ValueError(f"Image exceeds the {limit_mb} MB upload limit.")
            tg_file = await doc.get_file()
            data = bytes(await tg_file.download_as_bytearray())
            filename = doc.file_name or "image.jpg"
        elif photo:
            tg_file = await photo.get_file()
            data = bytes(await tg_file.download_as_bytearray())
            filename = "image.jpg"
        else:
            return

        validate_image(data, limit)
        width, height, image_format = image_info(data)
        content_type = "image/png" if image_format == "PNG" else "image/jpeg"
        extension = ".png" if image_format == "PNG" else ".jpg"
        if not filename.lower().endswith(extension):
            filename = f"image{extension}"

        expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
        file_id = await save_media(data, content_type, filename, expires_at, purpose="input")
        job_id = await create_job(
            update.effective_user.id,
            update.effective_chat.id,
            update.message.message_id,
            file_id,
            filename,
            width,
            height,
            expires_at,
        )

        context.user_data.clear()
        await update.message.reply_text(
            "Choose an operation:",
            reply_markup=operations(job_id),
        )
        logger.info("Created durable image job %s for user %s", job_id, update.effective_user.id)
    except Exception as exc:
        logger.exception("Image receive failed")
        try:
            await update.message.reply_text(f"❌ {exc}")
        except Exception:
            logger.exception("Failed to send image receive error")


async def callback(update, context):
    q = update.callback_query
    if not q:
        return
    await _safe_answer(q)
    data = q.data or ""
    parts = data.split(":")
    kind = parts[0] if parts else ""

    # New callbacks contain job_id. Legacy callbacks are still accepted and
    # resolve against the user's latest durable job, so deploys do not break
    # buttons already sitting in Telegram chats.
    job_id = None
    payload = None
    if len(parts) >= 3 and len(parts[1]) == 24 and all(ch in "0123456789abcdefABCDEF" for ch in parts[1]):
        job_id = parts[1]
        payload = ":".join(parts[2:])
    elif kind == "cancel" and len(parts) == 2 and len(parts[1]) == 24:
        job_id = parts[1]
        payload = ""
    elif len(parts) >= 2:
        payload = ":".join(parts[1:])

    job = await _job_from_callback(q, job_id)
    if not job:
        return
    job_id = str(job["_id"])

    try:
        if kind == "op":
            operation = payload
            if operation not in {"remove", "upscale", "expand"}:
                raise ValueError("Invalid image operation.")
            await update_job(job_id, {"operation": operation, "state": "choose_scale" if operation != "expand" else "choose_ratio"})
            if operation == "remove":
                await _safe_edit(q, "Remove BG — choose result scale.", scales("remove", job_id))
            elif operation == "upscale":
                await _safe_edit(q, "Upscale — choose scale.", scales("upscale", job_id))
            else:
                await _safe_edit(q, "Expand — choose target ratio.", ratios(job_id))
            return

        if kind in {"upscale", "remove", "expandscale"}:
            scale = int(payload)
            if scale not in (2, 4):
                raise ValueError("Scale must be 2× or 4×.")
            operation = "expand" if kind == "expandscale" else kind
            await update_job(job_id, {"operation": operation, "scale": scale, "state": "queued"})
            await set_job_status(job_id, "queued", operation=operation, scale=scale)
            if not await enqueue_job(job_id):
                # The durable queued state is intentionally kept. Startup
                # recovery will pick it up even if the worker is restarting.
                await q.message.reply_text("⏳ Job queued. It will start automatically.")
            else:
                await q.message.reply_text("⏳ Job queued. Please wait...")
            return

        if kind == "ratio":
            ratio = payload
            if ratio == "custom":
                await update_job(job_id, {"state": "custom"})
                await q.message.reply_text("Send custom target width × height.\nExample: 1920 x 1080")
                return
            if ratio not in RATIOS:
                raise ValueError("Invalid target ratio.")
            await update_job(job_id, {"ratio": ratio, "state": "choose_side"})
            await _safe_edit(q, "Choose which side to expand.", sides(job_id))
            return

        if kind == "side":
            side = payload
            if side not in VALID_SIDES:
                raise ValueError("Invalid expansion side.")
            await update_job(job_id, {"side": side})
            job = await get_job(job_id, q.from_user.id)
            if job.get("ratio"):
                target_w, target_h = _target_dimensions(job["width"], job["height"], job["ratio"])
                from app.services.expand import build_expansion
                build_expansion(job["width"], job["height"], target_w, target_h, side)
                await update_job(job_id, {"target_size": [target_w, target_h], "state": "choose_scale"})
                await _safe_edit(q, "Choose result scale.", scales("expandscale", job_id))
            else:
                await update_job(job_id, {"state": "amount"})
                await _safe_edit(q, "Send expansion amount in pixels.\nExample: 500")
            return

        if kind == "cancel":
            await set_job_status(job_id, "cancelled", cancelled_at=datetime.now(timezone.utc))
            await delete_media(job.get("file_id"))
            if job.get("output_file_id"):
                await delete_media(job["output_file_id"])
            context.user_data.clear()
            await _safe_edit(q, "Cancelled. Send another image.")
            return

    except Exception as exc:
        logger.exception("Image callback failed for job %s", job_id)
        await q.message.reply_text(f"❌ {exc}")


async def text(update, context):
    if not update.message or not update.message.text:
        return False
    job = await get_active_job(update.effective_user.id)
    if not job:
        return False
    state = job.get("state")
    if state not in {"custom", "amount"}:
        return False

    job_id = str(job["_id"])
    try:
        if state == "custom":
            custom = _parse_custom(update.message.text)
            await update_job(job_id, {"custom_size": list(custom), "ratio": "custom", "state": "choose_side"})
            await update.message.reply_text("Choose which side to expand.", reply_markup=sides(job_id))
        else:
            amount = int(update.message.text.strip())
            if not 1 <= amount <= 2000:
                raise ValueError("Expansion amount must be between 1 and 2000 pixels.")
            await update_job(job_id, {"expand_amount": amount, "state": "choose_scale"})
            await update.message.reply_text("Choose result scale.", reply_markup=scales("expandscale", job_id))
        return True
    except Exception as exc:
        await update.message.reply_text(f"❌ {exc}")
        return True
