# app/services/remove_bg.py

from .pixelcut import run


async def remove_background(
    image_url: str,
):
    return await run(
        "/v1/remove-background",
        {
            "image_url": image_url,
            "format": "png",
        },
    )
