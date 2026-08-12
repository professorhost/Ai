import asyncio
import httpx
from app.config import settings
from app.database import list_apis, next_api, mark_api_used, get_bot_settings
from app.security import decrypt_secret

BASE_URL = "https://api.developer.pixelcut.ai"

class PixelcutError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable

async def _request(api_key: str, endpoint: str, data: dict, timeout: int):
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
            response = await client.post(f"{BASE_URL}{endpoint}", headers=headers, json=data)
    except httpx.TimeoutException as exc:
        raise PixelcutError("Pixelcut request timed out.", retryable=True) from exc
    except httpx.RequestError as exc:
        raise PixelcutError("Could not connect to Pixelcut.", retryable=True) from exc

    if response.status_code in (401, 403):
        raise PixelcutError("Pixelcut authentication failed.", response.status_code, True)
    if response.status_code == 429:
        raise PixelcutError("Pixelcut rate limit reached.", 429, True)
    if response.status_code >= 500:
        raise PixelcutError("Pixelcut service is temporarily unavailable.", response.status_code, True)
    if response.status_code >= 400:
        raise PixelcutError("Pixelcut rejected the request.", response.status_code, False)

    try:
        body = response.json()
    except ValueError as exc:
        raise PixelcutError("Pixelcut returned an invalid response.", response.status_code, False) from exc

    url = body.get("result_url")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise PixelcutError("Pixelcut returned no valid result URL.", response.status_code, False)
    return url

async def run(endpoint: str, data: dict):
    bot = await get_bot_settings()
    timeout = int(bot["processing"].get("timeout", settings.default_timeout))
    all_apis = await list_apis()
    if not all_apis:
        raise PixelcutError("No Pixelcut API key is configured.")
    if not any(d.get("enabled") for d in all_apis):
        raise PixelcutError("No enabled Pixelcut API key is configured.")

    excluded = []
    last_error = None
    # Try each enabled key at most once for this operation.
    for _ in range(len(all_apis)):
        api = await next_api(excluded, rotate=bool(bot["rotation"].get("enabled", True)))
        if not api:
            break
        excluded.append(api["_id"])
        try:
            key = decrypt_secret(api["key"], settings.settings_encryption_key)
            result_url = await _request(key, endpoint, data, timeout)
            await mark_api_used(api["_id"])
            return await download(result_url, timeout)
        except PixelcutError as exc:
            last_error = exc
            if not exc.retryable:
                raise
        except RuntimeError as exc:
            last_error = PixelcutError("Pixelcut credential configuration error.")
        except Exception:
            last_error = PixelcutError("Pixelcut processing failed.", retryable=True)

    raise last_error or PixelcutError("Pixelcut processing failed.")

async def download(url: str, timeout: int):
    if not url.startswith("https://"):
        raise PixelcutError("Pixelcut returned an invalid result URL.")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout), follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise PixelcutError("Downloading the Pixelcut result timed out.", retryable=True) from exc
    except httpx.RequestError as exc:
        raise PixelcutError("Could not download the Pixelcut result.", retryable=True) from exc
    content_type = response.headers.get("content-type", "image/jpeg").split(";")[0]
    if content_type not in {"image/jpeg", "image/png"}:
        content_type = "image/jpeg"
    return response.content, content_type
