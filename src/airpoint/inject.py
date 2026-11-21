from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = ctypes.c_bool
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype = ctypes.c_bool
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int

INPUT_MOUSE = 0
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


def _mouse(flags: int, data: int = 0) -> None:
    item = INPUT()
    item.type = INPUT_MOUSE
    item.mi.dwFlags = flags
    item.mi.mouseData = data & 0xFFFFFFFF
    user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(INPUT))


def set_cursor(x: float, y: float) -> None:
    user32.SetCursorPos(int(round(x)), int(round(y)))


def get_cursor() -> tuple[int, int]:
    point = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        return 0, 0
    return point.x, point.y


def screen_size() -> tuple[int, int]:
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def button(name: str, down: bool) -> None:
    flags = {
        "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
        "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
        "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
    }.get(name)
    if flags is None:
        return
    _mouse(flags[0] if down else flags[1])


def wheel(delta: int) -> None:
    _mouse(MOUSEEVENTF_WHEEL, delta)


def release_all() -> None:
    for flags in (
        MOUSEEVENTF_LEFTUP,
        MOUSEEVENTF_RIGHTUP,
        MOUSEEVENTF_MIDDLEUP,
    ):
        _mouse(flags)
