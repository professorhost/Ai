# app/main.py

from contextlib import asynccontextmanager
import asyncio
import logging

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from app.config import settings
from app.database import close, connect, get_db, get_media, cleanup_expired_jobs, cleanup_expired_media
from app.job_manager import start_workers, stop_workers, queue_size, worker_count
from app.monitoring import install_logging, mark_webhook, status_payload

install_logging()
logger = logging.getLogger(__name__)

telegram_app = None
_service_task = None
_uptime_task = None
_stop_event = None
_services_ready = False
_startup_error = None


async def _uptime_loop():
    while not _stop_event.is_set():
        if settings.uptime_url:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.get(settings.uptime_url)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Uptime self-ping failed.", exc_info=True)
        try:
            await asyncio.wait_for(_stop_event.wait(), timeout=settings.uptime_interval)
        except asyncio.TimeoutError:
            continue


async def _cleanup_loop():
    while not _stop_event.is_set():
        try:
            if get_db():
                await cleanup_expired_jobs()
                await cleanup_expired_media()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Durable storage cleanup failed.", exc_info=True)
        try:
            await asyncio.wait_for(_stop_event.wait(), timeout=300)
        except asyncio.TimeoutError:
            continue


async def _build_telegram():
    global telegram_app
    from app.telegram import build_application

    telegram_app = build_application()
    await telegram_app.initialize()
    await telegram_app.start()

    if settings.public_base_url:
        webhook_url = settings.public_base_url.rstrip("/") + "/telegram/webhook"
        await telegram_app.bot.set_webhook(
            url=webhook_url,
            secret_token=settings.webhook_secret or None,
            drop_pending_updates=False,
        )
        logger.info("Telegram webhook configured: %s", webhook_url)
    else:
        logger.warning("PUBLIC_BASE_URL is not configured; webhook not set.")

    await start_workers(telegram_app.bot, worker_count=2)


async def _teardown_services(close_database=False):
    global telegram_app, _services_ready
    _services_ready = False
    try:
        await stop_workers()
    except Exception:
        logger.exception("Failed to stop image workers during recovery.")
    if telegram_app is not None:
        try:
            await telegram_app.stop()
        except Exception:
            logger.exception("Failed to stop Telegram during recovery.")
        try:
            await telegram_app.shutdown()
        except Exception:
            logger.exception("Failed to shutdown Telegram during recovery.")
        telegram_app = None
    if close_database:
        try:
            await close()
        except Exception:
            logger.exception("Failed to close MongoDB during recovery.")


async def _service_loop():
    global _services_ready, _startup_error, telegram_app
    backoff = 2
    while not _stop_event.is_set():
        try:
            if not settings.mongodb_uri:
                raise RuntimeError("MONGODB_URI is not configured.")
            if not settings.telegram_bot_token:
                raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")

            from app.database import db
            if db is None:
                logger.info("Connecting to MongoDB...")
                await connect()
                logger.info("MongoDB connection established.")

            if telegram_app is None:
                logger.info("Starting Telegram application...")
                await _build_telegram()

            # Keep the long-running service self-healing. A dropped Mongo or
            # Telegram connection causes a clean teardown and automatic retry.
            await get_db().command("ping")
            await telegram_app.bot.get_me()

            _services_ready = True
            _startup_error = None
            backoff = 2
            await asyncio.wait_for(_stop_event.wait(), timeout=30)

        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _services_ready = False
            _startup_error = f"{type(exc).__name__}: {exc}"
            logger.exception("Service initialization/recovery failed.")
            await _teardown_services(close_database=True)
            try:
                await asyncio.wait_for(_stop_event.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 30)


async def service_snapshot():
    jobs = 0
    try:
        jobs = await get_db().jobs.count_documents({"status": {"$in": ["queued", "processing", "ready_to_send"]}})
    except Exception:
        pass
    data = status_payload(telegram_app is not None, _services_ready, _startup_error)
    data.update({"jobs": jobs, "queue": queue_size(), "workers": worker_count()})
    return data


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _service_task, _uptime_task, _cleanup_task, _stop_event, telegram_app
    logger.info("FastAPI lifespan starting.")
    _stop_event = asyncio.Event()
    _service_task = asyncio.create_task(_service_loop())
    _cleanup_task = asyncio.create_task(_cleanup_loop())
    if settings.uptime_url:
        _uptime_task = asyncio.create_task(_uptime_loop())
    logger.info("FastAPI ready; health checks are non-blocking.")
    yield

    logger.info("FastAPI shutdown started.")
    _stop_event.set()
    for task in (_uptime_task, _cleanup_task, _service_task):
        if task:
            task.cancel()
    for task in (_uptime_task, _cleanup_task, _service_task):
        if task:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Background task shutdown failed.")

    try:
        await stop_workers()
    except Exception:
        logger.exception("Worker shutdown failed.")

    if telegram_app:
        try:
            await telegram_app.stop()
        except Exception:
            logger.exception("Telegram stop failed.")
        try:
            await telegram_app.shutdown()
        except Exception:
            logger.exception("Telegram shutdown failed.")
        telegram_app = None

    await close()
    logger.info("FastAPI shutdown completed.")


app = FastAPI(title="Image AI Telegram Bot", version="8.0.0", lifespan=lifespan)


@app.get("/")
async def root():
    snap = await service_snapshot()
    return {"service": "Image AI Telegram Bot", "status": "ok", **snap}


@app.get("/api/healthz")
async def healthz():
    # Always fast and intentionally independent of MongoDB/Telegram.
    return {"status": "ok"}


@app.get("/api/status")
async def status():
    return {"status": "ok", **await service_snapshot()}


@app.get("/api/ping")
async def api_ping():
    from app.monitoring import ping
    result = await ping(telegram_app)
    return {"status": "ok", **result}


@app.get("/api/logs")
async def api_logs():
    from app.monitoring import recent_logs
    return {"status": "ok", "logs": recent_logs(50)}


@app.get("/media/{file_id}")
async def media(file_id: str):
    raw = await get_media(file_id)
    if not raw:
        return JSONResponse({"error": "not found"}, status_code=404)
    meta, data = raw
    return Response(
        data,
        media_type=meta["content_type"],
        headers={"Cache-Control": "no-store, no-cache", "X-Robots-Tag": "noindex, nofollow"},
    )


@app.post("/telegram/webhook")
async def webhook(request: Request):
    if settings.webhook_secret:
        supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if supplied != settings.webhook_secret:
            return JSONResponse({"ok": False}, status_code=401)
    if telegram_app is None:
        return JSONResponse({"ok": False, "error": "bot unavailable"}, status_code=503)

    try:
        from telegram import Update
        update = Update.de_json(await request.json(), telegram_app.bot)
        mark_webhook()
        await telegram_app.process_update(update)
        return {"ok": True}
    except Exception as exc:
        logger.exception("Telegram webhook processing failed.")
        return JSONResponse({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status_code=500)
