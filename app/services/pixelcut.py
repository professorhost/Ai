import logging
from typing import Any

import httpx

from app.config import settings
from app.database import (
    get_bot_settings,
    list_apis,
    mark_api_used,
    next_api,
)
from app.security import decrypt_secret


logger = logging.getLogger(__name__)

BASE_URL = "https://api.developer.pixelcut.ai"


class PixelcutError(RuntimeError):
    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


def _response_message(response: httpx.Response) -> str:
    """
    Extract a useful error message from Pixelcut without
    exposing the API key.
    """

    try:
        body = response.json()

        if isinstance(body, dict):
            for key in (
                "message",
                "error",
                "detail",
                "description",
            ):
                value = body.get(key)

                if isinstance(value, str) and value.strip():
                    return value.strip()

            # Some APIs return {"error": {"message": "..."}}
            error = body.get("error")

            if isinstance(error, dict):
                value = error.get("message")

                if isinstance(value, str) and value.strip():
                    return value.strip()

        elif isinstance(body, str) and body.strip():
            return body.strip()

    except ValueError:
        pass

    text = response.text.strip()

    if text:
        return text[:500]

    return ""


async def _request(
    api_key: str,
    endpoint: str,
    data: dict[str, Any],
    timeout: int,
) -> str:
    if not api_key or not api_key.strip():
        raise PixelcutError(
            "Pixelcut API key is empty.",
            retryable=False,
        )

    endpoint = endpoint.strip()

    if not endpoint.startswith("/"):
        endpoint = f"/{endpoint}"

    headers = {
        "X-API-Key": api_key.strip(),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    url = f"{BASE_URL}{endpoint}"

    try:
        request_timeout = httpx.Timeout(
            connect=min(timeout, 30),
            read=timeout,
            write=timeout,
            pool=min(timeout, 30),
        )

        async with httpx.AsyncClient(
            timeout=request_timeout,
            follow_redirects=True,
        ) as client:
            response = await client.post(
                url,
                headers=headers,
                json=data,
            )

    except httpx.TimeoutException as exc:
        logger.warning(
            "Pixelcut request timed out: endpoint=%s",
            endpoint,
        )
        raise PixelcutError(
            "Pixelcut request timed out.",
            retryable=True,
        ) from exc

    except httpx.RequestError as exc:
        logger.warning(
            "Pixelcut connection error: endpoint=%s error=%s",
            endpoint,
            str(exc),
        )
        raise PixelcutError(
            "Could not connect to Pixelcut.",
            retryable=True,
        ) from exc

    if response.status_code in (401, 403):
        message = _response_message(response)

        logger.error(
            "Pixelcut authentication failed: "
            "status=%s endpoint=%s message=%s",
            response.status_code,
            endpoint,
            message or "no message",
        )

        if response.status_code == 403:
            error_text = (
                "Pixelcut rejected the API key (403 Forbidden)."
            )
        else:
            error_text = (
                "Pixelcut rejected the API key (401 Unauthorized)."
            )

        if message:
            error_text = f"{error_text} {message}"

        raise PixelcutError(
            error_text,
            response.status_code,
            retryable=True,
        )

    if response.status_code == 429:
        message = _response_message(response)

        logger.warning(
            "Pixelcut rate limit: endpoint=%s message=%s",
            endpoint,
            message or "no message",
        )

        raise PixelcutError(
            "Pixelcut rate limit reached.",
            429,
            retryable=True,
        )

    if 500 <= response.status_code <= 599:
        message = _response_message(response)

        logger.warning(
            "Pixelcut server error: status=%s endpoint=%s message=%s",
            response.status_code,
            endpoint,
            message or "no message",
        )

        raise PixelcutError(
            "Pixelcut service is temporarily unavailable.",
            response.status_code,
            retryable=True,
        )

    if response.status_code >= 400:
        message = _response_message(response)

        logger.error(
            "Pixelcut rejected request: status=%s endpoint=%s message=%s",
            response.status_code,
            endpoint,
            message or "no message",
        )

        error_text = "Pixelcut rejected the request."

        if message:
            error_text = f"{error_text} {message}"

        raise PixelcutError(
            error_text,
            response.status_code,
            retryable=False,
        )

    try:
        body = response.json()
    except ValueError as exc:
        logger.error(
            "Pixelcut returned invalid JSON: status=%s endpoint=%s",
            response.status_code,
            endpoint,
        )
        raise PixelcutError(
            "Pixelcut returned an invalid response.",
            response.status_code,
            retryable=False,
        ) from exc

    if not isinstance(body, dict):
        raise PixelcutError(
            "Pixelcut returned an invalid response.",
            response.status_code,
            retryable=False,
        )

    result_url = body.get("result_url")

    if not isinstance(result_url, str):
        # Some responses may use a nested result object.
        result = body.get("result")

        if isinstance(result, dict):
            result_url = result.get("url")

    if (
        not isinstance(result_url, str)
        or not result_url.startswith("https://")
    ):
        logger.error(
            "Pixelcut returned no valid result URL: endpoint=%s",
            endpoint,
        )

        raise PixelcutError(
            "Pixelcut returned no valid result URL.",
            response.status_code,
            retryable=False,
        )

    return result_url


async def run(
    endpoint: str,
    data: dict[str, Any],
):
    bot = await get_bot_settings()

    processing = bot.get("processing", {}) if bot else {}
    rotation = bot.get("rotation", {}) if bot else {}

    try:
        timeout = int(
            processing.get(
                "timeout",
                settings.default_timeout,
            )
        )
    except (TypeError, ValueError):
        timeout = settings.default_timeout

    timeout = max(10, min(600, timeout))

    all_apis = await list_apis()

    # ---------------------------------------------------------
    # Database API keys
    # ---------------------------------------------------------
    enabled_apis = [
        api
        for api in all_apis
        if api.get("enabled") is True
    ]

    # ---------------------------------------------------------
    # Optional environment API key fallback
    # ---------------------------------------------------------
    env_key = settings.pixelcut_api_key

    if not enabled_apis and env_key:
        logger.info(
            "Using PIXELCUT_API_KEY environment variable."
        )

        try:
            result_url = await _request(
                env_key,
                endpoint,
                data,
                timeout,
            )

            return await download(
                result_url,
                timeout,
            )

        except PixelcutError:
            raise

    if not enabled_apis:
        raise PixelcutError(
            "No enabled Pixelcut API key is configured."
        )

    excluded: list[Any] = []
    last_error: PixelcutError | None = None

    rotation_enabled = bool(
        rotation.get("enabled", True)
    )

    # Try each enabled database key at most once.
    for _ in range(len(enabled_apis)):
        api = await next_api(
            excluded,
            rotate=rotation_enabled,
        )

        if not api:
            break

        api_id = api.get("_id")

        if api_id is not None:
            excluded.append(api_id)

        try:
            encrypted_key = api.get("key")

            if not encrypted_key:
                raise PixelcutError(
                    "Pixelcut API key is missing.",
                    retryable=False,
                )

            try:
                key = decrypt_secret(
                    encrypted_key,
                    settings.settings_encryption_key,
                )
            except Exception as exc:
                logger.error(
                    "Could not decrypt Pixelcut API key: api_id=%s",
                    api_id,
                )
                raise PixelcutError(
                    "Pixelcut credential configuration error.",
                    retryable=False,
                ) from exc

            result_url = await _request(
                key,
                endpoint,
                data,
                timeout,
            )

            if api_id is not None:
                await mark_api_used(api_id)

            return await download(
                result_url,
                timeout,
            )

        except PixelcutError as exc:
            last_error = exc

            logger.warning(
                "Pixelcut API attempt failed: "
                "api_id=%s status=%s retryable=%s error=%s",
                api_id,
                exc.status_code,
                exc.retryable,
                str(exc),
            )

            # Continue to the next API key for authentication,
            # rate-limit and temporary server failures.
            if not exc.retryable:
                raise

        except Exception as exc:
            logger.exception(
                "Unexpected Pixelcut processing error: api_id=%s",
                api_id,
            )

            last_error = PixelcutError(
                "Pixelcut processing failed.",
                retryable=True,
            )

    raise last_error or PixelcutError(
        "Pixelcut processing failed."
    )


async def download(
    url: str,
    timeout: int,
):
    if not isinstance(url, str) or not url.startswith("https://"):
        raise PixelcutError(
            "Pixelcut returned an invalid result URL."
        )

    try:
        request_timeout = httpx.Timeout(
            connect=min(timeout, 30),
            read=timeout,
            write=timeout,
            pool=min(timeout, 30),
        )

        async with httpx.AsyncClient(
            timeout=request_timeout,
            follow_redirects=True,
        ) as client:
            response = await client.get(url)

    except httpx.TimeoutException as exc:
        raise PixelcutError(
            "Downloading the Pixelcut result timed out.",
            retryable=True,
        ) from exc

    except httpx.RequestError as exc:
        raise PixelcutError(
            "Could not download the Pixelcut result.",
            retryable=True,
        ) from exc

    if response.status_code >= 400:
        logger.error(
            "Failed to download Pixelcut result: status=%s",
            response.status_code,
        )

        raise PixelcutError(
            "Could not download the Pixelcut result.",
            response.status_code,
            retryable=True,
        )

    content_type = (
        response.headers
        .get("content-type", "image/jpeg")
        .split(";")[0]
        .strip()
        .lower()
    )

    if content_type not in {
        "image/jpeg",
        "image/png",
    }:
        content_type = "image/jpeg"

    return response.content, content_type
