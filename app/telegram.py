# app/telegram.py

import logging

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from app.config import settings

from app.handlers.start import start

from app.handlers.settings import (
    show as user_settings,
    callback as user_settings_callback,
    text as user_settings_text,
)

from app.handlers.admin import (
    show as admin_settings,
    callback as admin_callback,
    text as admin_text,
)

from app.handlers.image import (
    receive,
    callback as image_callback,
    text as image_text,
)
from app.monitoring import uptime_command, ping_command, log_command


logger = logging.getLogger(__name__)


# ============================================================
# TEXT ROUTER
# ============================================================

async def text_router(
    update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Route normal text messages safely.

    Priority:
        1. Image/Expand text
        2. User settings text
        3. Admin settings text

    We use one text handler so each normal text update is consumed once.
    """

    try:

        # ----------------------------------------------------
        # IMAGE HANDLER
        # ----------------------------------------------------

        consumed = await image_text(
            update,
            context,
        )

        if consumed:
            return

        # ----------------------------------------------------
        # USER SETTINGS
        # ----------------------------------------------------

        consumed = await user_settings_text(
            update,
            context,
        )

        if consumed:
            return

        # ----------------------------------------------------
        # ADMIN SETTINGS
        # ----------------------------------------------------

        consumed = await admin_text(
            update,
            context,
        )

        if consumed:
            return

    except Exception:
        logger.exception(
            "Text router failed"
        )

        if update.message:
            try:
                await update.message.reply_text(
                    "❌ Something went wrong while processing your message."
                )
            except Exception:
                logger.exception(
                    "Failed to send text-router error"
                )


# ============================================================
# BUILD APPLICATION
# ============================================================

def build_application():

    # --------------------------------------------------------
    # TOKEN CHECK
    # --------------------------------------------------------

    if not settings.telegram_bot_token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not configured."
        )

    # --------------------------------------------------------
    # APPLICATION
    # --------------------------------------------------------

    app = (
        Application.builder()
        .token(
            settings.telegram_bot_token
        )
        .build()
    )

    # ========================================================
    # COMMANDS
    # ========================================================

    app.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    app.add_handler(
        CommandHandler(
            "us",
            user_settings,
        )
    )

    app.add_handler(
        CommandHandler(
            "bs",
            admin_settings,
        )
    )

    app.add_handler(CommandHandler("uptime", uptime_command))
    app.add_handler(CommandHandler("ping", ping_command))
    app.add_handler(CommandHandler("log", log_command))

    # ========================================================
    # USER SETTINGS CALLBACKS
    # ========================================================

    app.add_handler(
        CallbackQueryHandler(
            user_settings_callback,
            pattern=r"^us:",
        )
    )

    # ========================================================
    # ADMIN CALLBACKS
    # ========================================================

    app.add_handler(
        CallbackQueryHandler(
            admin_callback,
            pattern=r"^(bs:|api:|privacy:)",
        )
    )

    # ========================================================
    # IMAGE CALLBACKS
    # ========================================================

    # IMPORTANT:
    #
    # Do NOT use block=False here.
    #
    # Image operations can take time. block=True prevents
    # callback state from being raced by another update.
    #
    app.add_handler(
        CallbackQueryHandler(
            image_callback,
            pattern=(
                r"^(op:|"
                r"upscale:|"
                r"remove:|"
                r"ratio:|"
                r"side:|"
                r"expandscale:|"
                r"cancel(?:$|:))"
            ),
        )
    )

    # ========================================================
    # TEXT
    # ========================================================

    # ONE text handler only.
    #
    # A single text handler avoids constructor incompatibilities and
    # prevents multiple text handlers from consuming the same update.
    app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            text_router,
        )
    )

    # ========================================================
    # IMAGE RECEIVER
    # ========================================================

    app.add_handler(
        MessageHandler(
            filters.PHOTO
            | filters.Document.IMAGE,
            receive,
        )
    )

    return app
