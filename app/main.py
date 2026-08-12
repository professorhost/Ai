# app/main.py

from contextlib import asynccontextmanager
import asyncio
import logging

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from app.config import settings
from app.database import (
    close,
    connect,
    get_db,
    get_media,
    cleanup_expired_jobs,
    cleanup_expired_media,
)
from app.job_manager import (
    start_workers,
    stop_workers,
    queue_size,
    worker_count,
)
from app.monitoring import (
    install_logging,
    mark_webhook,
    status_payload,
)


# ---------------------------------------------------------
# Logging
# ---------------------------------------------------------

install_logging()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------
# Global service state
# ---------------------------------------------------------

telegram_app = None

_service_task = None
_uptime_task = None
_cleanup_task = None
_stop_event = None

_services_ready = False
_startup_error = None


# ---------------------------------------------------------
# Uptime self-ping
# ---------------------------------------------------------

async def _uptime_loop():
    """
    Periodically ping the configured uptime URL.

    This task never blocks FastAPI startup and exits cleanly
    during application shutdown.
    """

    while _stop_event is not None and not _stop_event.is_set():

        if settings.uptime_url:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.get(settings.uptime_url)

            except asyncio.CancelledError:
                raise

            except Exception:
                logger.warning(
                    "Uptime self-ping failed.",
                    exc_info=True,
                )

        try:
            await asyncio.wait_for(
                _stop_event.wait(),
                timeout=settings.uptime_interval,
            )

        except asyncio.TimeoutError:
            continue

        except asyncio.CancelledError:
            raise


# ---------------------------------------------------------
# Durable storage cleanup
# ---------------------------------------------------------

async def _cleanup_loop():
    """
    Periodically remove expired durable jobs/media.

    IMPORTANT:
    Never use:

        if get_db():

    PyMongo/Motor Database objects do not support boolean
    evaluation and may raise NotImplementedError.

    Explicitly compare the database object with None.
    """

    while _stop_event is not None and not _stop_event.is_set():

        try:
            database = get_db()

            if database is not None:
                await cleanup_expired_jobs()
                await cleanup_expired_media()

        except asyncio.CancelledError:
            raise

        except RuntimeError:
            # MongoDB may not be connected yet.
            pass

        except Exception:
            logger.warning(
                "Durable storage cleanup failed.",
                exc_info=True,
            )

        try:
            await asyncio.wait_for(
                _stop_event.wait(),
                timeout=300,
            )

        except asyncio.TimeoutError:
            continue

        except asyncio.CancelledError:
            raise


# ---------------------------------------------------------
# Background service runner
# ---------------------------------------------------------

async def _service_loop():
    """
    Background service loop.

    Starts Telegram workers and keeps them alive until
    application shutdown.
    """

    global _services_ready
    global _startup_error

    try:
        await start_workers()

        _services_ready = True
        _startup_error = None

        logger.info("Background services started successfully.")

        while _stop_event is not None and not _stop_event.is_set():
            try:
                await asyncio.wait_for(
                    _stop_event.wait(),
                    timeout=30,
                )

            except asyncio.TimeoutError:
                continue

            except asyncio.CancelledError:
                raise

    except asyncio.CancelledError:
        raise

    except Exception as exc:
        _services_ready = False
        _startup_error = str(exc)

        logger.exception(
            "Background service loop failed: %s",
            exc,
        )

    finally:
        _services_ready = False

        try:
            await stop_workers()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Failed to stop background workers."
            )


# ---------------------------------------------------------
# Lifespan
# ---------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):

    global _service_task
    global _uptime_task
    global _cleanup_task
    global _stop_event
    global _services_ready
    global _startup_error

    logger.info("Starting application...")

    _stop_event = asyncio.Event()
    _services_ready = False
    _startup_error = None

    # -----------------------------------------------------
    # Database connection
    # -----------------------------------------------------

    try:
        await connect()
        logger.info("Database connected successfully.")

    except Exception as exc:
        _startup_error = str(exc)

        logger.exception(
            "Database connection failed: %s",
            exc,
        )

    # -----------------------------------------------------
    # Start background services
    # -----------------------------------------------------

    try:
        _service_task = asyncio.create_task(
            _service_loop(),
            name="service-loop",
        )

    except Exception as exc:
        _startup_error = str(exc)

        logger.exception(
            "Failed to create service task: %s",
            exc,
        )

    # -----------------------------------------------------
    # Start uptime task
    # -----------------------------------------------------

    if settings.uptime_url:
        try:
            _uptime_task = asyncio.create_task(
                _uptime_loop(),
                name="uptime-loop",
            )

        except Exception as exc:
            logger.exception(
                "Failed to create uptime task: %s",
                exc,
            )

    # -----------------------------------------------------
    # Start cleanup task
    # -----------------------------------------------------

    try:
        _cleanup_task = asyncio.create_task(
            _cleanup_loop(),
            name="cleanup-loop",
        )

    except Exception as exc:
        logger.exception(
            "Failed to create cleanup task: %s",
            exc,
        )

    logger.info("Application startup completed.")

    try:
        yield

    finally:
        logger.info("Application shutdown started.")

        # -------------------------------------------------
        # Signal background loops to stop
        # -------------------------------------------------

        if _stop_event is not None:
            _stop_event.set()

        # -------------------------------------------------
        # Cancel background tasks
        # -------------------------------------------------

        tasks = [
            _service_task,
            _uptime_task,
            _cleanup_task,
        ]

        for task in tasks:
            if task is not None and not task.done():
                task.cancel()

        # -------------------------------------------------
        # Wait for background tasks
        # -------------------------------------------------

        for task in tasks:
            if task is None:
                continue

            try:
                await task

            except asyncio.CancelledError:
                pass

            except Exception:
                logger.exception(
                    "Background task shutdown failed."
                )

        # -------------------------------------------------
        # Stop workers
        # -------------------------------------------------

        try:
            await stop_workers()

        except asyncio.CancelledError:
            pass

        except Exception:
            logger.exception(
                "Failed to stop workers during shutdown."
            )

        # -------------------------------------------------
        # Close database
        # -------------------------------------------------

        try:
            await close()
            logger.info("Database connection closed.")

        except asyncio.CancelledError:
            pass

        except Exception:
            logger.exception(
                "Failed to close database."
            )

        _service_task = None
        _uptime_task = None
        _cleanup_task = None
        _stop_event = None

        _services_ready = False

        logger.info("Application shutdown completed.")


# ---------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------

app = FastAPI(
    title="Telegram Bot API",
    lifespan=lifespan,
)


# ---------------------------------------------------------
# Health check
# ---------------------------------------------------------

@app.get("/")
async def root():
    return {
        "status": "ok",
        "service": "telegram-bot",
    }


@app.get("/health")
async def health():
    database = get_db()

    database_ready = database is not None

    return {
        "status": "ok" if database_ready else "degraded",
        "database": database_ready,
        "services_ready": _services_ready,
        "startup_error": _startup_error,
        "queue_size": queue_size(),
        "worker_count": worker_count(),
    }


# ---------------------------------------------------------
# Status endpoint
# ---------------------------------------------------------

@app.get("/status")
async def status():
    try:
        payload = status_payload()

        if asyncio.iscoroutine(payload):
            payload = await payload

        if isinstance(payload, dict):
            payload.setdefault(
                "services_ready",
                _services_ready,
            )

            payload.setdefault(
                "startup_error",
                _startup_error,
            )

        return payload

    except Exception as exc:
        logger.exception(
            "Status endpoint failed."
        )

        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "error": str(exc),
                "services_ready": _services_ready,
            },
        )


# ---------------------------------------------------------
# Telegram webhook
# ---------------------------------------------------------

@app.post("/webhook")
@app.post("/webhook/")
async def telegram_webhook(request: Request):

    global telegram_app

    try:
        data = await request.json()

    except Exception:
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "error": "Invalid JSON payload",
            },
        )

    try:
        mark_webhook()

    except Exception:
        logger.warning(
            "Failed to mark webhook activity.",
            exc_info=True,
        )

    # -----------------------------------------------------
    # Telegram application
    # -----------------------------------------------------

    if telegram_app is None:
        return JSONResponse(
            status_code=503,
            content={
                "ok": False,
                "error": "Telegram application is not ready",
            },
        )

    try:
        from telegram import Update

        update = Update.de_json(
            data,
            telegram_app.bot,
        )

        await telegram_app.process_update(update)

        return Response(
            content="OK",
            media_type="text/plain",
        )

    except Exception as exc:
        logger.exception(
            "Telegram webhook processing failed: %s",
            exc,
        )

        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": "Webhook processing failed",
            },
        )


# ---------------------------------------------------------
# Media endpoint
# ---------------------------------------------------------

@app.get("/media/{media_id}")
async def media(media_id: str):

    try:
        media_item = await get_media(media_id)

    except Exception as exc:
        logger.exception(
            "Failed to load media %s: %s",
            media_id,
            exc,
        )

        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": "Failed to load media",
            },
        )

    if not media_item:
        return JSONResponse(
            status_code=404,
            content={
                "ok": False,
                "error": "Media not found",
            },
        )

    # -----------------------------------------------------
    # Extract media content
    # -----------------------------------------------------

    content = media_item.get("content")

    if content is None:
        content = media_item.get("data")

    if content is None:
        return JSONResponse(
            status_code=404,
            content={
                "ok": False,
                "error": "Media content not found",
            },
        )

    content_type = (
        media_item.get("content_type")
        or media_item.get("mime_type")
        or "application/octet-stream"
    )

    filename = media_item.get("filename")

    headers = {}

    if filename:
        headers["Content-Disposition"] = (
            f'inline; filename="{filename}"'
        )

    return Response(
        content=content,
        media_type=content_type,
        headers=headers,
    )


# ---------------------------------------------------------
# Exception handler
# ---------------------------------------------------------

@app.exception_handler(Exception)
async def global_exception_handler(
    request: Request,
    exc: Exception,
):
    logger.exception(
        "Unhandled application error: %s",
        exc,
    )

    return JSONResponse(
        status_code=500,
        content={
            "ok": False,
            "error": "Internal server error",
        },
    )
