from __future__ import annotations

import atexit
import ctypes
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = ctypes.c_bool
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype = ctypes.c_bool
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int

SM_CXSCREEN = 0
SM_CYSCREEN = 1
MONITORINFOF_PRIMARY = 1

INPUT_MOUSE = 0
MOUSE_MARKER = 0x41505400
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("mi", MOUSEINPUT),
    ]


user32.SendInput.argtypes = [
    wintypes.UINT,
    ctypes.POINTER(INPUT),
    ctypes.c_int,
]
user32.SendInput.restype = wintypes.UINT


def _mouse(flags: int, data: int = 0) -> bool:
    item = INPUT()
    item.type = INPUT_MOUSE
    item.mi.dwFlags = flags
    item.mi.mouseData = data & 0xFFFFFFFF
    item.mi.dwExtraInfo = MOUSE_MARKER
    sent = user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(INPUT))
    return bool(sent)


def set_cursor(x: float, y: float) -> bool:
    return bool(user32.SetCursorPos(int(round(x)), int(round(y))))


def get_cursor() -> tuple[int, int]:
    point = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        return 0, 0
    return point.x, point.y


def screen_size() -> tuple[int, int]:
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


_MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL,
    wintypes.HANDLE,
    wintypes.HDC,
    ctypes.POINTER(wintypes.RECT),
    wintypes.LPARAM,
)

user32.EnumDisplayMonitors.argtypes = [
    wintypes.HDC,
    ctypes.c_void_p,
    _MONITORENUMPROC,
    wintypes.LPARAM,
]
user32.EnumDisplayMonitors.restype = wintypes.BOOL
user32.GetMonitorInfoW.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(MONITORINFOEXW),
]
user32.GetMonitorInfoW.restype = wintypes.BOOL

_MONITOR_CACHE_SECONDS = 5.0
_monitor_cache: tuple[float, list] = (0.0, [])


def monitors(force: bool = False) -> list[dict]:
    """Every attached display, left to right, with its physical rect.

    §7.1: the frame loop needs more than the primary monitor's metrics, so
    this returns the whole set. Enumeration costs a handful of syscalls, which
    is not worth paying per frame, hence the short cache.
    """
    global _monitor_cache
    now = time.monotonic()
    cached_at, cached = _monitor_cache
    if not force and cached and now - cached_at < _MONITOR_CACHE_SECONDS:
        return cached

    found: list[dict] = []

    def collect(handle, _hdc, _rect, _data) -> bool:
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if not user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            return True
        found.append({
            "name": info.szDevice,
            "left": int(info.rcMonitor.left),
            "top": int(info.rcMonitor.top),
            "width": int(info.rcMonitor.right - info.rcMonitor.left),
            "height": int(info.rcMonitor.bottom - info.rcMonitor.top),
            "primary": bool(info.dwFlags & MONITORINFOF_PRIMARY),
        })
        return True

    try:
        user32.EnumDisplayMonitors(None, None, _MONITORENUMPROC(collect), 0)
    except (OSError, AttributeError):
        found = []
    found.sort(key=lambda item: (item["left"], item["top"]))
    _monitor_cache = (now, found)
    return found


def virtual_rect() -> tuple[int, int, int, int]:
    """The virtual desktop, i.e. every monitor's bounding box."""
    return (
        user32.GetSystemMetrics(76),   # SM_XVIRTUALSCREEN
        user32.GetSystemMetrics(77),   # SM_YVIRTUALSCREEN
        user32.GetSystemMetrics(78),   # SM_CXVIRTUALSCREEN
        user32.GetSystemMetrics(79),   # SM_CYVIRTUALSCREEN
    )


def screen_rect(target: str = "auto") -> tuple[int, int, int, int]:
    """Physical ``(left, top, width, height)`` of the display to aim at.

    ``target`` is ``auto``/``primary``, ``all`` for the whole virtual desktop,
    a 1-based display number, or a device name such as ``\\\\.\\DISPLAY2``.
    An unknown target falls back to the primary display, which is also what
    happens when Windows will not enumerate anything.
    """
    wanted = str(target or "auto").strip().lower()
    width, height = screen_size()

    if wanted in ("all", "virtual", "全部", "所有"):
        left, top, span_w, span_h = virtual_rect()
        if span_w > 0 and span_h > 0:
            return left, top, span_w, span_h

    if wanted not in ("", "auto", "primary", "主屏", "默认"):
        for index, monitor in enumerate(monitors(), start=1):
            name = monitor["name"].lower()
            bare = name.split("\\")[-1]
            if wanted == str(index) or wanted in (name, bare):
                return (
                    monitor["left"],
                    monitor["top"],
                    monitor["width"],
                    monitor["height"],
                )

    for monitor in monitors():
        if monitor["primary"]:
            return (
                monitor["left"],
                monitor["top"],
                monitor["width"],
                monitor["height"],
            )
    return 0, 0, width, height


def screen_name(target: str = "auto") -> str:
    """Human-readable name of the display ``screen_rect`` would pick."""
    left, top, width, height = screen_rect(target)
    for index, monitor in enumerate(monitors(), start=1):
        if (
            monitor["left"],
            monitor["top"],
            monitor["width"],
            monitor["height"],
        ) == (left, top, width, height):
            label = monitor["name"].split("\\")[-1]
            return f"{label} {width}x{height}" + (
                "（主屏）" if monitor["primary"] else ""
            )
    return f"{width}x{height}"


def button(name: str, down: bool) -> bool:
    flags = {
        "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
        "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
        "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
    }.get(name)
    if flags is None:
        return False
    return _mouse(flags[0] if down else flags[1])


def wheel(delta: int) -> bool:
    return _mouse(MOUSEEVENTF_WHEEL, delta)


def release_all() -> None:
    for flags in (
        MOUSEEVENTF_LEFTUP,
        MOUSEEVENTF_RIGHTUP,
        MOUSEEVENTF_MIDDLEUP,
    ):
        _mouse(flags)


# §9.5: a second net behind the controller's try/finally, so an interpreter
# exit that never reaches Controller.stop still lifts a synthetic button.
atexit.register(release_all)
