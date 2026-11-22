from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

from .paths import data_dir

# §14.2: the classroom questions that can be answered without a person
# watching the screen. A report written to the data folder is what turns
# "看起来还行" into numbers somebody can act on.

MDT_EFFECTIVE_DPI = 0
MONITOR_DEFAULTTONEAREST = 2


def _dpi_at(x: int, y: int) -> int:
    """Effective DPI of the display containing ``(x, y)``, or 0 if unknown."""
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        shcore = ctypes.WinDLL("shcore", use_last_error=True)
        user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        user32.MonitorFromPoint.restype = wintypes.HANDLE
        shcore.GetDpiForMonitor.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.POINTER(wintypes.UINT),
            ctypes.POINTER(wintypes.UINT),
        ]
        shcore.GetDpiForMonitor.restype = ctypes.c_long
        handle = user32.MonitorFromPoint(
            wintypes.POINT(int(x), int(y)), MONITOR_DEFAULTTONEAREST
        )
        if not handle:
            return 0
        dpi_x, dpi_y = wintypes.UINT(0), wintypes.UINT(0)
        if shcore.GetDpiForMonitor(
            handle, MDT_EFFECTIVE_DPI,
            ctypes.byref(dpi_x), ctypes.byref(dpi_y),
        ) == 0:
            return int(dpi_x.value)
    except (OSError, AttributeError):
        pass
    return 0


def describe_displays() -> list[dict]:
    """Every display with its physical rect, DPI and scaling (§7.1, §14.2)."""
    from . import inject

    rows = []
    for index, monitor in enumerate(inject.monitors(force=True), start=1):
        dpi = _dpi_at(
            monitor["left"] + monitor["width"] // 2,
            monitor["top"] + monitor["height"] // 2,
        )
        rows.append({
            "index": index,
            "name": monitor["name"],
            "left": monitor["left"],
            "top": monitor["top"],
            "width": monitor["width"],
            "height": monitor["height"],
            "primary": monitor["primary"],
            "dpi": dpi,
            "scale": round(dpi / 96.0, 2) if dpi else 0.0,
        })
    return rows


def camera_probe(config, log=None, limit: int = 4) -> list[dict]:
    """Try every advertised mode and record what the driver actually gave.

    §14.2 asks whether 4K, the frame rate and manual exposure are available.
    cv2 happily accepts a property and then ignores it, so each result is
    read back rather than assumed.
    """
    import cv2

    from .capture import apply_exposure, targets_for

    device = str(config.get("camera.device", "auto"))
    if device.lower() == "auto":
        indices = list(range(limit))
    else:
        try:
            indices = [int(device)]
        except ValueError:
            indices = [0]

    fps_wanted = int(config.get("camera.fps", 30))
    results = []
    for index in indices:
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap.release()
            continue
        for width, height in targets_for(config):
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            cap.set(cv2.CAP_PROP_FPS, fps_wanted)
            ok, frame = cap.read()
            got_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            got_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            got_fps = cap.get(cv2.CAP_PROP_FPS)
            results.append({
                "device": index,
                "asked": (width, height),
                "got": (got_w or 0, got_h or 0),
                "fps": float(got_fps or 0.0),
                "frame": bool(ok and frame is not None),
            })
            if ok and frame is not None:
                break
        manual = apply_exposure(cap, log)
        exposure = cap.get(cv2.CAP_PROP_EXPOSURE)
        results.append({
            "device": index,
            "exposure_manual": manual,
            "exposure": float(exposure or 0.0),
        })
        cap.release()
        # The first device that actually delivered a frame is the classroom
        # camera; probing the rest would only add noise and camera time.
        if any(item.get("frame") for item in results):
            break
    return results


def build_report(displays, camera, config, version: str) -> str:
    """Plain-text report for §14.2 items 1 and 6, plus the live settings."""
    lines = [
        f"OpticalInputSvc {version} 环境自检",
        time.strftime("%Y-%m-%d %H:%M:%S"),
        "",
        "== 显示 ==",
    ]
    if not displays:
        lines.append("  没有枚举到显示器。")
    for row in displays:
        scale = f"{row['scale']:.0%}" if row.get("scale") else "未知"
        lines.append(
            f"  #{row['index']} {row['name']}"
            f"{'（主屏）' if row['primary'] else ''} "
            f"{row['width']}x{row['height']} @({row['left']},{row['top']}) "
            f"缩放 {scale}"
        )
    lines.append(
        f"  目标屏设置：{config.get('pointer.screen', 'auto')}"
    )

    lines.append("")
    lines.append("== 摄像头 ==")
    modes = [item for item in camera if "asked" in item]
    exposures = [item for item in camera if "exposure_manual" in item]
    if not modes:
        lines.append("  打不开摄像头，或没有返回画面。")
    for item in modes:
        asked = "x".join(str(value) for value in item["asked"])
        got = "x".join(str(value) for value in item["got"])
        lines.append(
            f"  #{item['device']} 请求 {asked} → 实际 {got} "
            f"@{item['fps']:.0f} fps "
            f"{'有画面' if item['frame'] else '无画面'}"
        )
    for item in exposures:
        lines.append(
            f"  #{item['device']} 手动曝光"
            f"{'可用' if item['exposure_manual'] else '不可用'} "
            f"（读数 {item['exposure']:g}）"
        )
    lines.append(
        f"  期望：4K {'开' if config.get('camera.prefer_4k', True) else '关'}，"
        f"帧率 {config.get('camera.fps', 30)}，"
        f"后端 {config.get('camera.backend', 'dshow')}"
    )

    lines.append("")
    lines.append("== 判定 ==")
    has_4k = any(
        item["got"][0] >= 3000 and item["frame"] for item in modes
    )
    lines.append(
        f"  4K：{'可用' if has_4k else '不可用，按 1080p 规划'}"
    )
    slow = [item for item in modes if item["frame"] and item["fps"] < 24]
    if slow:
        lines.append(
            f"  帧率不足：{slow[0]['fps']:.0f} fps，手部跟踪会变钝。"
        )
    manual = any(item["exposure_manual"] for item in exposures)
    lines.append(
        "  手动曝光：" + (
            "可用，短曝光能压住投影抖动" if manual
            else "不可用，只能靠自动曝光"
        )
    )
    if len(displays) > 1:
        lines.append(
            f"  多屏：{len(displays)} 个，"
            f"目标屏建议设成 {config.get('pointer.screen', 'auto')} 对应的编号。"
        )
    return "\n".join(lines) + "\n"


def write_report(displays, camera, config, version: str):
    """Save the report under ``D:\\OpticalInput\\reports`` and return its path."""
    folder = data_dir() / "reports"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / time.strftime("env-%Y%m%d-%H%M%S.txt")
    path.write_text(
        build_report(displays, camera, config, version), encoding="utf-8"
    )
    return path
