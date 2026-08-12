from telegram import Update
from telegram.ext import ContextTypes
from app.database import ensure_user

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await ensure_user(update.effective_user.id)
    context.user_data.clear()
    await update.message.reply_text(
        "<b>Image AI</b>\n\nSend your image/document to get started.\n"
        "Supported: JPG, JPEG, PNG.",
        parse_mode="HTML",
    )
