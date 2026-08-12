# app/monitoring.py

import logging
import time
from collections import deque
from datetime import datetime, timezone

from app.config import settings

_started_monotonic = time.monotonic()
_started_at = datetime.now(timezone.utc)
_last_webhook = None
_log_buffer = deque(maxlen=200)


class RingLogHandler(logging.Handler):
    def emit(self, record):
        try:
            _log_buffer.append({
                "time": datetime.fromtimestamp(record.created, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                "level": record.levelname,
                "message": self.format(record),
            })
        except Exception:
            pass


def install_logging():
    root = logging.getLogger()
    if not any(isinstance(h, RingLogHandler) for h in root.handlers):
        handler = RingLogHandler()
        handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
        root.addHandler(handler)
    root.setLevel(logging.INFO)


def mark_webhook():
    global _last_webhook
    _last_webhook = datetime.now(timezone.utc)


def uptime_seconds() -> int:
    return max(0, int(time.monotonic() - _started_monotonic))


def format_uptime() -> str:
    seconds = uptime_seconds()
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{days}d {hours:02d}h {minutes:02d}m {seconds:02d}s"


def recent_logs(limit: int = 20):
    limit = max(1, min(int(limit), 50))
    return list(_log_buffer)[-limit:]


def status_payload(telegram_ready: bool, services_ready: bool, startup_error=None):
    return {
        "uptime": format_uptime(),
        "uptime_seconds": uptime_seconds(),
        "started_at": _started_at.isoformat(),
        "telegram": telegram_ready,
        "services_ready": services_ready,
        "last_webhook": _last_webhook.isoformat() if _last_webhook else None,
        "startup_error": startup_error,
    }


async def ping(telegram_app=None):
    result = {"app": "ok", "mongodb": "unknown", "telegram": "unknown"}
    try:
        from app.database import get_db
        await get_db().command("ping")
        result["mongodb"] = "ok"
    except Exception as exc:
        result["mongodb"] = f"error: {type(exc).__name__}"
    if telegram_app is not None:
        try:
            await telegram_app.bot.get_me()
            result["telegram"] = "ok"
        except Exception as exc:
            result["telegram"] = f"error: {type(exc).__name__}"
    return result


def is_admin(user_id: int) -> bool:
    return user_id in settings.admin_ids

async def uptime_command(update, context):
    if not update.message:
        return
    from app.main import service_snapshot
    snap = await service_snapshot()
    await update.message.reply_text(
        "📈 Uptime\n\n"
        f"Process: {format_uptime()}\n"
        f"Started: {_started_at.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        f"Telegram: {'ONLINE' if snap.get('telegram') else 'OFFLINE'}\n"
        f"Services: {'READY' if snap.get('ready') else 'STARTING/ERROR'}\n"
        f"Jobs: {snap.get('jobs', 0)} queued/active\n"
        f"Workers: {snap.get('workers', 0)}"
    )


async def ping_command(update, context):
    if not update.message:
        return
    from app.main import telegram_app
    result = await ping(telegram_app)
    await update.message.reply_text(
        "🏓 Ping\n\n"
        f"App: {result['app']}\n"
        f"MongoDB: {result['mongodb']}\n"
        f"Telegram: {result['telegram']}"
    )


async def log_command(update, context):
    if not update.message:
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin access only.")
        return
    try:
        limit = int(context.args[0]) if context.args else 20
    except (TypeError, ValueError):
        limit = 20
    rows = recent_logs(limit)
    if not rows:
        await update.message.reply_text("No logs available yet.")
        return
    text = "📋 Recent logs\n\n" + "\n".join(
        f"[{row['time']}] {row['level']} — {row['message']}" for row in rows
    )
    if len(text) > 4000:
        text = text[-4000:]
    await update.message.reply_text(text)
