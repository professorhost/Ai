# app/handlers/admin.py

from html import escape

from telegram.error import BadRequest

from app.config import settings
from app.database import (
    get_bot_settings,
    list_apis,
    add_api,
    toggle_api,
    delete_api,
    set_bot,
)
from app.keyboards import (
    bs_variables,
    bs_variable_detail,
    api_menu,
    api_list,
    privacy,
)
from app.security import (
    encrypt_secret,
    decrypt_secret,
    mask_secret,
)


# These are intentionally database-backed values. Environment secrets such as
# TELEGRAM_BOT_TOKEN and MONGODB_URI are never exposed or editable from /bs.
VARIABLES = (
    ("PIXELCUT_TIMEOUT", "PIXELCUT_TIMEOUT"),
    ("MAX_UPLOAD_MB", "MAX_UPLOAD_MB"),
    ("OUTPUT_QUALITY", "OUTPUT_QUALITY"),
    ("GLOBAL_THUMBNAIL", "GLOBAL_THUMBNAIL"),
    ("API_ROTATION", "API_ROTATION"),
    ("ENCRYPTION_DISPLAY", "ENCRYPTION_DISPLAY"),
    ("SHOW_API_KEYS", "SHOW_API_KEYS"),
    ("PIXELCUT_APIS", "PIXELCUT_APIS"),
)
PAGE_SIZE = 8


def is_admin(uid: int) -> bool:
    return uid in settings.admin_ids


def _pages():
    return max(1, (len(VARIABLES) + PAGE_SIZE - 1) // PAGE_SIZE)


def _page_items(page: int):
    pages = _pages()
    page = max(1, min(page, pages))
    start = (page - 1) * PAGE_SIZE
    return page, pages, VARIABLES[start:start + PAGE_SIZE]


async def _safe_answer(q, text=None, show_alert=False):
    try:
        await q.answer(text=text, show_alert=show_alert)
    except BadRequest as exc:
        message = str(exc).lower()
        if (
            "query is too old" in message
            or "query id is invalid" in message
            or "response timeout expired" in message
        ):
            return
        raise


async def _render_variables(message, page=1):
    page, pages, items = _page_items(page)
    await message.edit_text(
        f"Config Variables | Page: {page}",
        reply_markup=bs_variables(items, page, pages),
    )


async def show(update, context):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin access only.")
        return

    page, pages, items = _page_items(1)
    await update.message.reply_text(
        f"Config Variables | Page: {page}",
        reply_markup=bs_variables(items, page, pages),
    )


async def _variable_value(key: str):
    s = await get_bot_settings()

    if key == "PIXELCUT_TIMEOUT":
        return s["processing"]["timeout"], "seconds", "number"
    if key == "MAX_UPLOAD_MB":
        return s["processing"]["max_upload_mb"], "MB", "number"
    if key == "OUTPUT_QUALITY":
        return s["output"]["jpeg_quality"], "1–100", "number"
    if key == "GLOBAL_THUMBNAIL":
        return s["output"]["thumbnail"], "ON/OFF", "bool"
    if key == "API_ROTATION":
        return s["rotation"]["enabled"], "ON/OFF", "bool"
    if key == "ENCRYPTION_DISPLAY":
        return s["privacy"]["encryption"], "ON/OFF", "bool"
    if key == "SHOW_API_KEYS":
        return s["privacy"]["show_api_keys"], "ON/OFF", "bool"
    if key == "PIXELCUT_APIS":
        docs = await list_apis()
        enabled = sum(1 for d in docs if d.get("enabled"))
        return f"{enabled} enabled / {len(docs)} total", "", "api"

    raise ValueError("Unknown config variable.")


def _detail_text(key: str, value, unit: str):
    if key == "PIXELCUT_APIS":
        return f"Variable: <b>{escape(key)}</b>\n\nValue: <b>{escape(str(value))}</b>"
    return (
        f"Variable: <b>{escape(key)}</b>\n\n"
        f"Value: <b>{escape(str(value))}</b>"
        + (f" {escape(unit)}" if unit else "")
    )


async def callback(update, context):
    q = update.callback_query

    if not q:
        return

    if not is_admin(q.from_user.id):
        await _safe_answer(q, "Admin only.", True)
        return

    await _safe_answer(q)

    parts = (q.data or "").split(":")
    if len(parts) < 2:
        return

    action = parts[1]

    if action == "page":
        try:
            page = int(parts[2]) if len(parts) > 2 else 1
        except ValueError:
            page = 1
        await _render_variables(q.message, page)
        return

    if action == "back":
        await _render_variables(q.message, 1)
        return

    if action == "close":
        context.user_data.pop("admin_wait", None)
        try:
            await q.message.delete()
        except Exception:
            await q.edit_message_text("Closed.")
        return

    if action == "var" and len(parts) == 3:
        key = parts[2]

        if key == "PIXELCUT_APIS":
            await q.edit_message_text(
                "🔑 Pixelcut APIs",
                reply_markup=api_menu(),
            )
            return

        try:
            value, unit, kind = await _variable_value(key)
        except Exception as exc:
            await _safe_answer(q, str(exc), True)
            return

        await q.edit_message_text(
            _detail_text(key, value, unit),
            parse_mode="HTML",
            reply_markup=bs_variable_detail(key),
        )
        return

    if action == "edit" and len(parts) == 3:
        key = parts[2]

        if key in {
            "GLOBAL_THUMBNAIL",
            "API_ROTATION",
            "ENCRYPTION_DISPLAY",
            "SHOW_API_KEYS",
        }:
            current, _, _ = await _variable_value(key)
            path = {
                "GLOBAL_THUMBNAIL": "output.thumbnail",
                "API_ROTATION": "rotation.enabled",
                "ENCRYPTION_DISPLAY": "privacy.encryption",
                "SHOW_API_KEYS": "privacy.show_api_keys",
            }[key]
            new_value = not bool(current)

            if key == "ENCRYPTION_DISPLAY" and new_value:
                await set_bot("privacy.show_api_keys", False)

            if key == "SHOW_API_KEYS":
                s = await get_bot_settings()
                if s["privacy"]["encryption"]:
                    await _safe_answer(
                        q,
                        "Turn ENCRYPTION_DISPLAY OFF first.",
                        True,
                    )
                    return

            await set_bot(path, new_value)
            value, unit, _ = await _variable_value(key)
            await q.edit_message_text(
                _detail_text(key, value, unit),
                parse_mode="HTML",
                reply_markup=bs_variable_detail(key),
            )
            return

        if key == "PIXELCUT_APIS":
            await q.edit_message_text(
                "🔑 Pixelcut APIs",
                reply_markup=api_menu(),
            )
            return

        context.user_data["admin_wait"] = key
        prompts = {
            "PIXELCUT_TIMEOUT": "Send timeout in seconds (10–600).",
            "MAX_UPLOAD_MB": "Send max upload size in MB (1–20).",
            "OUTPUT_QUALITY": "Send JPG quality (1–100).",
        }
        await q.message.reply_text(prompts.get(key, "Send the new value."))
        return

    # ------------------------------------------------------------------
    # Legacy API/privacy callbacks remain supported.
    # ------------------------------------------------------------------
    if action == "apis":
        await q.edit_message_text("🔑 Pixelcut APIs", reply_markup=api_menu())
        return

    if action == "privacy":
        s = await get_bot_settings()
        await q.edit_message_text(
            "🔐 Privacy",
            reply_markup=privacy(
                s["privacy"]["encryption"],
                s["privacy"]["show_api_keys"],
            ),
        )
        return

    if action == "rotation_toggle":
        s = await get_bot_settings()
        await set_bot("rotation.enabled", not s["rotation"]["enabled"])
        return

    if action == "thumbnail":
        s = await get_bot_settings()
        await set_bot("output.thumbnail", not s["output"]["thumbnail"])
        return

    if action == "enc":
        s = await get_bot_settings()
        new_value = not s["privacy"]["encryption"]
        await set_bot("privacy.encryption", new_value)
        if new_value:
            await set_bot("privacy.show_api_keys", False)
        s = await get_bot_settings()
        await q.edit_message_reply_markup(
            reply_markup=privacy(
                s["privacy"]["encryption"],
                s["privacy"]["show_api_keys"],
            )
        )
        return

    if action == "show":
        s = await get_bot_settings()
        if s["privacy"]["encryption"]:
            await _safe_answer(q, "Turn Encryption display OFF before showing API keys.", True)
            return
        await set_bot("privacy.show_api_keys", not s["privacy"]["show_api_keys"])
        s = await get_bot_settings()
        await q.edit_message_reply_markup(
            reply_markup=privacy(
                s["privacy"]["encryption"],
                s["privacy"]["show_api_keys"],
            )
        )
        return

    if action == "add":
        context.user_data["admin_wait"] = "api"
        await q.message.reply_text(
            "Send: Label | API_KEY\n"
            "The API key will be encrypted before being stored."
        )
        return

    if action == "list":
        docs = await list_apis()
        s = await get_bot_settings()

        if not docs:
            await q.edit_message_text("No APIs configured.", reply_markup=api_menu())
            return

        rows = []
        for doc in docs:
            try:
                key = decrypt_secret(doc["key"], settings.settings_encryption_key)
                if not s["privacy"]["encryption"] and s["privacy"]["show_api_keys"]:
                    shown = escape(key)
                else:
                    shown = escape(mask_secret(key))
            except Exception:
                shown = "[credential unavailable]"

            label = escape(str(doc.get("label", "Unnamed API")))
            rows.append(f"<b>{label}</b> — {shown}")

        await q.edit_message_text(
            "\n".join(rows),
            parse_mode="HTML",
            reply_markup=api_list([
                (
                    str(doc["_id"]),
                    doc.get("label", "Unnamed API"),
                    doc.get("enabled", False),
                )
                for doc in docs
            ]),
        )
        return

    if action == "toggle" and len(parts) == 3:
        ok = await toggle_api(parts[2])
        await _safe_answer(q, "API status updated." if ok else "API not found.", not ok)
        docs = await list_apis()
        await q.edit_message_reply_markup(
            reply_markup=api_list([
                (str(doc["_id"]), doc.get("label", "Unnamed API"), doc.get("enabled", False))
                for doc in docs
            ])
        )
        return

    if action == "delete" and len(parts) == 3:
        ok = await delete_api(parts[2])
        await _safe_answer(q, "API deleted." if ok else "API not found.", not ok)
        docs = await list_apis()
        await q.edit_message_reply_markup(
            reply_markup=api_list([
                (str(doc["_id"]), doc.get("label", "Unnamed API"), doc.get("enabled", False))
                for doc in docs
            ])
        )
        return


async def text(update, context):
    action = context.user_data.get("admin_wait")

    if not action or not is_admin(update.effective_user.id):
        return False

    if not update.message or not update.message.text:
        return False

    value = update.message.text.strip()

    try:
        if action == "api":
            if "|" not in value:
                raise ValueError
            label, key = [item.strip() for item in value.split("|", 1)]
            if not label or not key or len(label) > 40 or len(key) > 500:
                raise ValueError
            if not settings.settings_encryption_key:
                raise RuntimeError("SETTINGS_ENCRYPTION_KEY is not configured.")
            await add_api(label, encrypt_secret(key, settings.settings_encryption_key))
            try:
                await update.message.delete()
            except Exception:
                pass

        elif action == "PIXELCUT_TIMEOUT":
            number = int(value)
            if not 10 <= number <= 600:
                raise ValueError
            await set_bot("processing.timeout", number)

        elif action == "MAX_UPLOAD_MB":
            number = int(value)
            if not 1 <= number <= 20:
                raise ValueError
            await set_bot("processing.max_upload_mb", number)

        elif action == "OUTPUT_QUALITY":
            number = int(value)
            if not 1 <= number <= 100:
                raise ValueError
            await set_bot("output.jpeg_quality", number)

        # Legacy input names retained for compatibility.
        elif action == "timeout":
            number = int(value)
            if not 10 <= number <= 600:
                raise ValueError
            await set_bot("processing.timeout", number)
        elif action == "maxupload":
            number = int(value)
            if not 1 <= number <= 20:
                raise ValueError
            await set_bot("processing.max_upload_mb", number)
        elif action == "quality":
            number = int(value)
            if not 1 <= number <= 100:
                raise ValueError
            await set_bot("output.jpeg_quality", number)
        else:
            raise ValueError

    except RuntimeError as exc:
        await update.message.reply_text(f"❌ {exc}")
        return True
    except Exception:
        await update.message.reply_text("❌ Invalid value.")
        return True

    context.user_data.pop("admin_wait", None)
    await update.message.reply_text("✅ Saved to MongoDB.")
    return True
