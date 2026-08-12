from telegram import Update
from telegram.ext import ContextTypes
from app.database import get_user_settings, update_user_settings, reset_user_settings
from app.keyboards import us

async def show(update, context):
    s = await get_user_settings(update.effective_user.id)
    await update.message.reply_text(
        f"<b>User Settings</b>\n\nFormat: {s['format']}\nJPG quality: {s['jpeg_quality']}\n"
        f"Prefix: {s['prefix'] or 'None'}\nSuffix: {s['suffix'] or 'None'}\n"
        f"Scale filename: {'ON' if s['scale_in_filename'] else 'OFF'}\n"
        f"Thumbnail: {'ON' if s['thumbnail'] else 'OFF'}",
        parse_mode="HTML", reply_markup=us()
    )

async def callback(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    action = q.data.split(":")[1]
    s = await get_user_settings(uid)
    if action == "format":
        await update_user_settings(uid, {"format": "JPG" if s["format"] == "PNG" else "PNG"})
    elif action == "thumb":
        await update_user_settings(uid, {"thumbnail": not s["thumbnail"]})
    elif action == "scale_name":
        await update_user_settings(uid, {"scale_in_filename": not s["scale_in_filename"]})
    elif action == "reset":
        await reset_user_settings(uid)
    elif action in ("prefix", "suffix", "quality"):
        context.user_data["user_setting_wait"] = action
        await q.message.reply_text({
            "prefix": "Send prefix.",
            "suffix": "Send suffix.",
            "quality": "Send JPG quality 1–100.",
        }[action])
        return
    s = await get_user_settings(uid)
    await q.edit_message_text(
        f"<b>User Settings</b>\n\nFormat: {s['format']}\nJPG quality: {s['jpeg_quality']}\n"
        f"Prefix: {s['prefix'] or 'None'}\nSuffix: {s['suffix'] or 'None'}\n"
        f"Scale filename: {'ON' if s['scale_in_filename'] else 'OFF'}\n"
        f"Thumbnail: {'ON' if s['thumbnail'] else 'OFF'}",
        parse_mode="HTML", reply_markup=us()
    )

async def text(update, context):
    action = context.user_data.get("user_setting_wait")
    if not action:
        return False
    value = update.message.text.strip()
    try:
        if action == "quality":
            value = int(value)
            if not 1 <= value <= 100:
                raise ValueError
        elif len(value) > 40:
            raise ValueError
        await update_user_settings(update.effective_user.id, {action: value})
    except Exception:
        await update.message.reply_text("Invalid value.")
        return True
    context.user_data.pop("user_setting_wait", None)
    await show(update, context)
    return True
