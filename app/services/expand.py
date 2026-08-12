# app/services/expand.py

from .pixelcut import run


MAX_SIDE = 2000

VALID_SIDES = {
    "left",
    "right",
    "top",
    "bottom",
    "all",
}


def build_expansion(
    width: int,
    height: int,
    target_width: int,
    target_height: int,
    side: str,
):
    if width <= 0 or height <= 0:
        raise ValueError(
            "Invalid original image dimensions."
        )

    if target_width <= 0 or target_height <= 0:
        raise ValueError(
            "Invalid target dimensions."
        )

    if target_width < width or target_height < height:
        raise ValueError(
            "Expand can only increase dimensions; "
            "choose a larger target."
        )

    if side not in VALID_SIDES:
        raise ValueError(
            "Invalid expand side."
        )

    dw = target_width - width
    dh = target_height - height

    # Left/right expansion must keep the
    # original height unchanged.
    if side in {"left", "right"} and dh != 0:
        raise ValueError(
            "For left/right expansion, the target "
            "height must remain unchanged."
        )

    # Top/bottom expansion must keep the
    # original width unchanged.
    if side in {"top", "bottom"} and dw != 0:
        raise ValueError(
            "For top/bottom expansion, the target "
            "width must remain unchanged."
        )

    if side == "left":
        left = dw
        right = 0
        top = 0
        bottom = 0

    elif side == "right":
        left = 0
        right = dw
        top = 0
        bottom = 0

    elif side == "top":
        left = 0
        right = 0
        top = dh
        bottom = 0

    elif side == "bottom":
        left = 0
        right = 0
        top = 0
        bottom = dh

    else:  # all
        left = dw // 2
        right = dw - left
        top = dh // 2
        bottom = dh - top

    values = (
        left,
        top,
        right,
        bottom,
    )

    if max(values) > MAX_SIDE:
        raise ValueError(
            "Pixelcut allows up to 2000 pixels "
            "per expansion side."
        )

    if sum(values) == 0:
        raise ValueError(
            "At least one expansion side must "
            "be greater than zero."
        )

    return (
        left,
        top,
        right,
        bottom,
    )


async def expand(
    image_url: str,
    width: int,
    height: int,
    target_width: int,
    target_height: int,
    side: str,
):
    left, top, right, bottom = build_expansion(
        width,
        height,
        target_width,
        target_height,
        side,
    )

    return await run(
        "/v1/outpaint",
        {
            "image_url": image_url,
            "left": left,
            "top": top,
            "right": right,
            "bottom": bottom,
            "creativity": 0,
            "output_format": "jpeg",
        },
    )
