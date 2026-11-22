from __future__ import annotations

import threading
import time

import pystray
from PIL import Image, ImageDraw

NAME = "OpticalInputSvc"


def _title(is_active, is_recording) -> str:
    if is_recording():
        return f"{NAME} · 录制中"
    return f"{NAME} · 运行中" if is_active() else f"{NAME} · 已暂停"


def _sync_title(icon, is_active, is_recording) -> None:
    """Keep the tooltip honest while the menu is closed."""
    for _ in range(3600):
        time.sleep(1.0)
        try:
            if not icon.visible:
                continue
            title = _title(is_active, is_recording)
            if icon.title != title:
                icon.title = title
        except Exception:
            return


def _image() -> Image.Image:
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((6, 6, 58, 58), radius=14, fill=(64, 64, 64, 255))
    draw.rounded_rectangle(
        (18, 16, 46, 48), radius=4, outline=(220, 220, 220, 255), width=3
    )
    draw.ellipse((29, 29, 35, 35), fill=(220, 220, 220, 255))
    return image


def create_icon(
    on_toggle,
    on_preview,
    on_settings,
    on_check,
    on_record,
    on_exit,
    is_active,
    is_recording,
    on_recalibrate=None,
    on_data_dir=None,
    on_survey=None,
    on_wizard=None,
    on_report=None,
    describe=None,
) -> pystray.Icon:
    items = []
    if describe is not None:
        items.append(
            pystray.MenuItem(lambda item: describe(), None, enabled=False)
        )
        items.append(pystray.Menu.SEPARATOR)
    items.extend(
        [
            pystray.MenuItem(
                lambda item: "暂停鼠标" if is_active() else "激活鼠标",
                lambda: on_toggle(),
            ),
            pystray.MenuItem("预览诊断…", lambda: on_preview()),
            pystray.MenuItem(
                lambda item: (
                    "停止录制" if is_recording() else "开始录制"
                ),
                lambda: on_record(),
            ),
        ]
    )
    if on_survey is not None:
        items.append(pystray.MenuItem("勘测模式…", lambda: on_survey()))
    if on_report is not None:
        items.append(pystray.MenuItem("环境自检…", lambda: on_report()))
    if on_wizard is not None:
        items.append(pystray.MenuItem("首次向导…", lambda: on_wizard()))
    if on_recalibrate is not None:
        items.append(
            pystray.MenuItem("重新标定…", lambda: on_recalibrate())
        )
    items.append(pystray.MenuItem("设置…", lambda: on_settings()))
    items.append(pystray.MenuItem("检查更新", lambda: on_check()))
    if on_data_dir is not None:
        items.append(
            pystray.MenuItem("打开数据目录", lambda: on_data_dir())
        )
    items.append(pystray.MenuItem("退出", lambda: on_exit()))
    icon = pystray.Icon(
        NAME,
        _image(),
        _title(is_active, is_recording),
        menu=pystray.Menu(*items),
    )
    threading.Thread(
        target=_sync_title,
        args=(icon, is_active, is_recording),
        daemon=True,
        name="airpoint-tray-title",
    ).start()
    return icon
