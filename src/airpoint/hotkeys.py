from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

user32.RegisterHotKey.argtypes = [
    wintypes.HWND,
    ctypes.c_int,
    wintypes.UINT,
    wintypes.UINT,
]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG),
    wintypes.HWND,
    wintypes.UINT,
    wintypes.UINT,
]
user32.GetMessageW.restype = wintypes.BOOL

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
WM_HOTKEY = 0x0312

VK = {
    "esc": 0x1B,
    "space": 0x20,
    "tab": 0x09,
    "f1": 0x70,
    "f2": 0x71,
    "f3": 0x72,
    "f4": 0x73,
    "f5": 0x74,
    "f6": 0x75,
    "f7": 0x76,
    "f8": 0x77,
    "f9": 0x78,
    "f10": 0x79,
    "f11": 0x7A,
    "f12": 0x7B,
}


def parse(spec: str) -> tuple[int, int | None]:
    modifiers = 0
    key = None
    for part in str(spec).lower().split("+"):
        part = part.strip()
        if part in ("ctrl", "control"):
            modifiers |= MOD_CONTROL
        elif part == "alt":
            modifiers |= MOD_ALT
        elif part == "shift":
            modifiers |= MOD_SHIFT
        elif part in ("win", "windows"):
            modifiers |= MOD_WIN
        elif part in VK:
            key = VK[part]
        elif len(part) == 1 and part.isalnum():
            key = ord(part.upper())
    return modifiers, key


class Hotkeys:
    def __init__(
        self, config, log, on_toggle, on_emergency, on_mode=None, on_bar=None
    ):
        self.config = config
        self.log = log
        self.on_toggle = on_toggle
        self.on_emergency = on_emergency
        self.on_mode = on_mode
        self.on_bar = on_bar
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="airpoint-hotkeys"
        )

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        bindings = []
        entries = [
            (1, "hotkey.toggle_active", self.on_toggle),
            (2, "hotkey.emergency_stop", self.on_emergency),
        ]
        if self.on_mode is not None:
            entries.append((3, "hotkey.toggle_mode", self.on_mode))
        if self.on_bar is not None:
            entries.append((4, "hotkey.control_bar", self.on_bar))
        for ident, key, callback in entries:
            modifiers, vk = parse(str(self.config.get(key, "")))
            if vk is None:
                self.log(f"hotkey invalid: {key}")
                continue
            if not user32.RegisterHotKey(None, ident, modifiers, vk):
                error = ctypes.get_last_error()
                self.log(f"hotkey register failed: {key} (win32 {error})")
                continue
            bindings.append((ident, callback))
            self.log(f"hotkey registered: {key}")

        if not bindings:
            return

        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) != 0:
            if message.message != WM_HOTKEY:
                continue
            for ident, callback in bindings:
                if message.wParam == ident:
                    try:
                        callback()
                    except Exception:
                        pass
