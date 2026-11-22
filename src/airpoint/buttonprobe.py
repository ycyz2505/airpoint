from __future__ import annotations

import ctypes
import queue
import threading
import tkinter as tk
from ctypes import wintypes
from tkinter import ttk

from .inject import MOUSE_MARKER

# §14.2 #5: does the classroom mouse send its side buttons as raw XButton
# messages, or has a driver turned them into keystrokes? Only the field can
# answer that, so this probe listens to both hooks and says which one fired.

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
HC_ACTION = 0
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_MBUTTONDOWN = 0x0207
WM_XBUTTONDOWN = 0x020B
LLMHF_INJECTED = 0x0001
LLKHF_INJECTED = 0x00000010
WM_QUIT = 0x0012

VK_BROWSER_BACK = 0xA6
VK_BROWSER_FORWARD = 0xA7
VK_BROWSER_REFRESH = 0xA8
VK_BROWSER_SEARCH = 0xAA

_KEY_NAMES = {
    VK_BROWSER_BACK: "浏览器后退",
    VK_BROWSER_FORWARD: "浏览器前进",
    VK_BROWSER_REFRESH: "浏览器刷新",
    VK_BROWSER_SEARCH: "浏览器搜索",
    0x5D: "应用菜单键",
    0x0D: "回车",
    0x20: "空格",
    0x1B: "Esc",
}


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


MOUSE_PROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)
KEY_PROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)

user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int, ctypes.c_void_p, wintypes.HINSTANCE, wintypes.DWORD
]
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
user32.CallNextHookEx.argtypes = [
    ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.GetMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT
]
user32.PostThreadMessageW.argtypes = [
    wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
]
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

def button_name(message: int, mouse_data: int) -> str | None:
    """Which physical button a hook message stands for, or None."""
    if message == WM_MBUTTONDOWN:
        return "中键"
    if message == WM_XBUTTONDOWN:
        which = (int(mouse_data) >> 16) & 0xFFFF
        if which == 1:
            return "侧键 1（后退）"
        if which == 2:
            return "侧键 2（前进）"
        return f"未知侧键 {which}"
    return None


def key_name(vk: int) -> str:
    vk = int(vk) & 0xFFFF
    if vk in _KEY_NAMES:
        return _KEY_NAMES[vk]
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk)
    return f"VK 0x{vk:02X}"


def verdict(seen) -> str:
    """Plain-language answer for §14.2 #5, from the names that fired."""
    names = list(seen)
    side = [name for name in names if name.startswith("侧键")]
    keys = [name for name in names if not name.startswith(("侧键", "中键"))]
    if side:
        return (
            f"收到 {side[0]} 的原始消息，侧键可以直接用，"
            "不用装驱动，也不用改映射。"
        )
    if keys:
        return (
            f"没有原始侧键消息，但收到了按键「{keys[0]}」——"
            "侧键被鼠标驱动改成了键盘事件。请在驱动里把它改回"
            "「后退 / 前进」，或到设置里把这个按键当成热键用。"
        )
    if "中键" in names:
        return (
            "只收到中键。如果按的是侧键，说明侧键没有发消息，"
            "可能要在鼠标驱动里启用。"
        )
    return "还没按到。按住鼠标侧键（滚轮旁边那两个）再试。"


class _Listener:
    """A temporary pair of low-level hooks, owned by one thread."""

    def __init__(self) -> None:
        self.events: queue.Queue[str] = queue.Queue()
        self._mouse_hook = None
        self._key_hook = None
        self._thread_id = 0
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="airpoint-button-probe"
        )
        self._mouse_proc = MOUSE_PROC(self._on_mouse)
        self._key_proc = KEY_PROC(self._on_key)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)

    def _on_mouse(self, n_code, w_param, l_param):
        if n_code == HC_ACTION:
            try:
                info = ctypes.cast(
                    l_param, ctypes.POINTER(MSLLHOOKSTRUCT)
                ).contents
                if not (info.flags & LLMHF_INJECTED) and (
                    info.dwExtraInfo != MOUSE_MARKER
                ):
                    name = button_name(w_param, info.mouseData)
                    if name:
                        self.events.put(name)
            except Exception:
                pass
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _on_key(self, n_code, w_param, l_param):
        if n_code == HC_ACTION and w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
            try:
                info = ctypes.cast(
                    l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)
                ).contents
                if not (info.flags & LLKHF_INJECTED):
                    self.events.put(f"键盘 {key_name(info.vkCode)}")
            except Exception:
                pass
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _run(self) -> None:
        self._thread_id = int(kernel32.GetCurrentThreadId())
        self._mouse_hook = user32.SetWindowsHookExW(
            WH_MOUSE_LL, self._mouse_proc, None, 0
        )
        self._key_hook = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._key_proc, None, 0
        )
        if not self._mouse_hook and not self._key_hook:
            self.events.put("hook 安装失败，无法自检。")
            return
        message = wintypes.MSG()
        while user32.GetMessageW(
            ctypes.byref(message), None, 0, 0
        ) > 0:
            pass
        for hook in (self._mouse_hook, self._key_hook):
            if hook:
                user32.UnhookWindowsHookEx(hook)
        self._mouse_hook = self._key_hook = None


def open_probe(parent) -> None:
    """§14.2 #5: a small window that says what the mouse buttons send."""
    win = tk.Toplevel(parent)
    win.title("OpticalInputSvc 侧键自检")
    win.geometry("520x360")
    win.attributes("-topmost", True)

    tk.Label(
        win,
        text="按住鼠标侧键（滚轮两边那两个）两三次。",
        font=("Microsoft YaHei UI", 13, "bold"),
        anchor="w",
    ).pack(fill="x", padx=16, pady=(14, 2))
    tk.Label(
        win,
        text="这个窗口只记录按键，不动光标，也不改任何设置。",
        fg="#666666",
        anchor="w",
    ).pack(fill="x", padx=16, pady=(0, 10))

    log_var = tk.StringVar(value="等待按键…")
    tk.Label(
        win,
        textvariable=log_var,
        font=("Consolas", 11),
        justify="left",
        anchor="nw",
        fg="#1b6ea8",
        height=6,
        wraplength=470,
    ).pack(fill="x", padx=16)

    verdict_var = tk.StringVar(value=verdict([]))
    tk.Label(
        win,
        textvariable=verdict_var,
        font=("Microsoft YaHei UI", 11),
        justify="left",
        anchor="w",
        wraplength=470,
        fg="#8a5a00",
    ).pack(fill="x", padx=16, pady=(10, 0))

    listener = _Listener()
    seen: list[str] = []
    closed = {"value": False}

    def poll() -> None:
        changed = False
        while True:
            try:
                name = listener.events.get_nowait()
            except queue.Empty:
                break
            seen.append(name)
            changed = True
        if changed:
            shown = seen[-8:]
            log_var.set("\n".join(shown))
            verdict_var.set(verdict(seen))
        if closed["value"]:
            return
        try:
            win.after(100, poll)
        except tk.TclError:
            pass

    def close() -> None:
        closed["value"] = True
        listener.stop()
        win.destroy()

    ttk.Button(win, text="关闭", command=close).pack(pady=(16, 0))
    win.protocol("WM_DELETE_WINDOW", close)
    listener.start()
    win.after(100, poll)
