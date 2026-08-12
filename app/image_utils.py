from io import BytesIO
from pathlib import Path
import re
from PIL import Image

ALLOWED_MIME = {"image/jpeg", "image/png"}

def validate_image(data: bytes, max_bytes: int):
    if not data:
        raise ValueError("Empty image.")
    if len(data) > max_bytes:
        raise ValueError("Image exceeds the upload limit.")
    try:
        with Image.open(BytesIO(data)) as im:
            if im.format not in {"JPEG", "PNG"}:
                raise ValueError("Only JPG, JPEG and PNG images are supported.")
            if im.width < 1 or im.height < 1:
                raise ValueError("Invalid image dimensions.")
            im.verify()
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Unsupported or corrupt image.") from exc

def image_info(data: bytes):
    with Image.open(BytesIO(data)) as im:
        return im.width, im.height, im.format

def convert_output(data: bytes, requested_format: str, jpeg_quality: int):
    requested_format = requested_format.upper()
    with Image.open(BytesIO(data)) as im:
        if requested_format == "PNG":
            out = BytesIO()
            if "A" in im.getbands() or im.mode in ("RGBA", "LA", "P"):
                im.save(out, "PNG", optimize=True)
            else:
                im.convert("RGB").save(out, "PNG", optimize=True)
            return out.getvalue(), "image/png", "png"
        out = BytesIO()
        im.convert("RGB").save(out, "JPEG", quality=jpeg_quality, optimize=True)
        return out.getvalue(), "image/jpeg", "jpg"

def make_thumbnail(data: bytes, max_side: int = 320):
    with Image.open(BytesIO(data)) as im:
        im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        out = BytesIO()
        im.convert("RGB").save(out, "JPEG", quality=85, optimize=True)
        return out.getvalue()

def normalize_filename(name: str, prefix: str, suffix: str, scale, ext: str, include_scale: bool = True):
    stem = Path(name or "image").stem
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem)[:80] or "image"
    prefix = re.sub(r"[^A-Za-z0-9._-]+", "_", prefix or "")[:40]
    suffix = re.sub(r"[^A-Za-z0-9._-]+", "_", suffix or "")[:40]
    scale_part = f"_{scale}x" if include_scale and scale in (2, 4) else ""
    return f"{prefix}{stem}{scale_part}{suffix}.{ext.lower()}"
