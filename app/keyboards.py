from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def operations(job_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✂️ REMOVE BG", callback_data=f"op:{job_id}:remove"),
         InlineKeyboardButton("✨ UPSCALE", callback_data=f"op:{job_id}:upscale")],
        [InlineKeyboardButton("🖼️ EXPAND", callback_data=f"op:{job_id}:expand")],
    ])


def scales(prefix, job_id, include_original=False):
    rows = []
    if include_original:
        rows.append([InlineKeyboardButton("Original", callback_data=f"{prefix}:{job_id}:1")])
    rows.append([
        InlineKeyboardButton("2×", callback_data=f"{prefix}:{job_id}:2"),
        InlineKeyboardButton("4×", callback_data=f"{prefix}:{job_id}:4"),
    ])
    rows.append([InlineKeyboardButton("✖ Cancel", callback_data=f"cancel:{job_id}")])
    return InlineKeyboardMarkup(rows)


def ratios(job_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("1:1", callback_data=f"ratio:{job_id}:1:1"),
         InlineKeyboardButton("4:3", callback_data=f"ratio:{job_id}:4:3"),
         InlineKeyboardButton("4:5", callback_data=f"ratio:{job_id}:4:5")],
        [InlineKeyboardButton("9:16", callback_data=f"ratio:{job_id}:9:16"),
         InlineKeyboardButton("16:9", callback_data=f"ratio:{job_id}:16:9"),
         InlineKeyboardButton("2.39:1", callback_data=f"ratio:{job_id}:2.39:1")],
        [InlineKeyboardButton("A4", callback_data=f"ratio:{job_id}:a4"),
         InlineKeyboardButton("Letter", callback_data=f"ratio:{job_id}:letter")],
        [InlineKeyboardButton("Custom", callback_data=f"ratio:{job_id}:custom"),
         InlineKeyboardButton("✖ Cancel", callback_data=f"cancel:{job_id}")],
    ])


def sides(job_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬆ TOP", callback_data=f"side:{job_id}:top"),
         InlineKeyboardButton("⬇ BOTTOM", callback_data=f"side:{job_id}:bottom")],
        [InlineKeyboardButton("⬅ LEFT", callback_data=f"side:{job_id}:left"),
         InlineKeyboardButton("➡ RIGHT", callback_data=f"side:{job_id}:right")],
        [InlineKeyboardButton("⬆⬇⬅➡ ALL SIDES", callback_data=f"side:{job_id}:all")],
        [InlineKeyboardButton("✖ Cancel", callback_data=f"cancel:{job_id}")],
    ])


def us():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📄 Format", callback_data="us:format"),
         InlineKeyboardButton("🖼 Thumbnail", callback_data="us:thumb")],
        [InlineKeyboardButton("🔖 Prefix", callback_data="us:prefix"),
         InlineKeyboardButton("🔖 Suffix", callback_data="us:suffix")],
        [InlineKeyboardButton("🔢 Scale filename", callback_data="us:scale_name"),
         InlineKeyboardButton("🎚 JPG quality", callback_data="us:quality")],
        [InlineKeyboardButton("♻️ Reset", callback_data="us:reset")],
    ])


# ---------------------------------------------------------------------------
# Admin / /bs UI
# ---------------------------------------------------------------------------

def _nav_rows(page: int, pages: int):
    rows = [
        [
            InlineKeyboardButton("Back", callback_data="bs:back"),
            InlineKeyboardButton("Close", callback_data="bs:close"),
        ]
    ]

    if pages > 1:
        page_buttons = [
            InlineKeyboardButton(str(n), callback_data=f"bs:page:{n}")
            for n in range(1, pages + 1)
        ]
        # Telegram callback data is small, and current pages are intentionally
        # kept compact. Split long page lists into rows of 8.
        for i in range(0, len(page_buttons), 8):
            rows.append(page_buttons[i:i + 8])

    return rows


def bs_variables(items, page: int = 1, pages: int = 1):
    """Screenshot-style 2-column Config Variables keyboard."""
    rows = []
    for key, label in items:
        rows.append([
            InlineKeyboardButton(label, callback_data=f"bs:var:{key}"),
        ])

    # Rebuild as two columns without changing item order.
    two_col = []
    for i in range(0, len(rows), 2):
        left = rows[i][0]
        right = rows[i + 1][0] if i + 1 < len(rows) else None
        two_col.append([left] + ([right] if right else []))

    two_col.extend(_nav_rows(page, pages))
    return InlineKeyboardMarkup(two_col)


def bs_variable_detail(key: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Edit Value", callback_data=f"bs:edit:{key}")],
        [
            InlineKeyboardButton("Back", callback_data="bs:back"),
            InlineKeyboardButton("Close", callback_data="bs:close"),
        ],
    ])


def bs():
    # Kept for compatibility with older imports. The admin handler now renders
    # the paginated Config Variables UI through bs_variables().
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Config Variables", callback_data="bs:page:1")],
        [InlineKeyboardButton("Pixelcut APIs", callback_data="bs:apis")],
        [InlineKeyboardButton("Close", callback_data="bs:close")],
    ])


def api_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add API", callback_data="api:add"),
         InlineKeyboardButton("📋 API List", callback_data="api:list")],
        [InlineKeyboardButton("Back", callback_data="bs:page:1"),
         InlineKeyboardButton("Close", callback_data="bs:close")],
    ])


def privacy(enc, show):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Encryption display: {'ON 🔒' if enc else 'OFF 🔓'}", callback_data="privacy:enc")],
        [InlineKeyboardButton(f"Show API keys: {'ON 👁️' if show else 'OFF 🙈'}", callback_data="privacy:show")],
        [InlineKeyboardButton("Back", callback_data="bs:page:1"),
         InlineKeyboardButton("Close", callback_data="bs:close")],
    ])


def processing(timeout, max_upload):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"⏱ Timeout: {timeout}s", callback_data="bs:var:PIXELCUT_TIMEOUT"),
         InlineKeyboardButton(f"📦 Max upload: {max_upload}MB", callback_data="bs:var:MAX_UPLOAD_MB")],
        [InlineKeyboardButton("Back", callback_data="bs:page:1"),
         InlineKeyboardButton("Close", callback_data="bs:close")],
    ])


def output(quality, thumbnail):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🎚 JPG quality: {quality}", callback_data="bs:var:OUTPUT_QUALITY")],
        [InlineKeyboardButton(f"🖼 Global thumbnail: {'ON' if thumbnail else 'OFF'}", callback_data="bs:var:GLOBAL_THUMBNAIL")],
        [InlineKeyboardButton("Back", callback_data="bs:page:1"),
         InlineKeyboardButton("Close", callback_data="bs:close")],
    ])


def rotation(enabled):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"API rotation: {'ON 🔄' if enabled else 'OFF ⏸'}", callback_data="bs:var:API_ROTATION")],
        [InlineKeyboardButton("Back", callback_data="bs:page:1"),
         InlineKeyboardButton("Close", callback_data="bs:close")],
    ])


def api_list(items):
    rows = []
    for oid, label, enabled in items:
        rows.append([
            InlineKeyboardButton(("🟢 " if enabled else "🔴 ") + label[:30], callback_data=f"api:toggle:{oid}"),
            InlineKeyboardButton("🗑", callback_data=f"api:delete:{oid}"),
        ])
    rows += [
        [InlineKeyboardButton("➕ Add API", callback_data="api:add")],
        [InlineKeyboardButton("Back", callback_data="bs:page:1"),
         InlineKeyboardButton("Close", callback_data="bs:close")],
    ]
    return InlineKeyboardMarkup(rows)
