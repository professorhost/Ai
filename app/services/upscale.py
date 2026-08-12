# app/services/upscale.py

from .pixelcut import run


async def upscale(
    image_url: str,
    scale: int,
):
    if scale not in (2, 4):
        raise ValueError(
            "Scale must be 2 or 4."
        )

    return await run(
        "/v1/upscale",
        {
            "image_url": image_url,
            "scale": scale,
        },
    )
