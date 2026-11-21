from __future__ import annotations

import pystray
from PIL import Image, ImageDraw


def _image() -> Image.Image:
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((6, 6, 58, 58), radius=14, fill=(64, 64, 64, 255))
    draw.rounded_rectangle(
        (18, 16, 46, 48), radius=4, outline=(220, 220, 220, 255), width=3
    )
    draw.ellipse((29, 29, 35, 35), fill=(220, 220, 220, 255))
    return image


def create_icon(on_settings, on_check, on_exit) -> pystray.Icon:
    return pystray.Icon(
        "OpticalInputSvc",
        _image(),
        "OpticalInputSvc · 运行中",
        menu=pystray.Menu(
            pystray.MenuItem("设置…", lambda: on_settings()),
            pystray.MenuItem("检查更新", lambda: on_check()),
            pystray.MenuItem("退出", lambda: on_exit()),
        ),
    )
