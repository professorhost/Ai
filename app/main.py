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
                async with httpx.AsyncClient(
                    timeout=10
                ) as client:
                    await client.get(
                        settings.uptime_url
                    )

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
    evaluation and raise NotImplementedError.

    We explicitly compare the database object with None.
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
            # The service loop will establish the connection.
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
