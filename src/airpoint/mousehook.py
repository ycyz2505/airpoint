from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

from .inject import MOUSE_MARKER

user32 = ctypes.WinDLL("user32", use_last_error=True)

WH_MOUSE_LL = 14
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C
WM_MOUSEWHEEL = 0x020A
HC_ACTION = 0
LLMHF_INJECTED = 0x00000001

# §9.6: only the side buttons confirm, never left or right. A left click on a
# desktop icon must not also hand the cursor back to the air pointer.
_CONFIRM_MESSAGES = {WM_MBUTTONDOWN, WM_XBUTTONDOWN}

_MOUSE_MESSAGES = {
    WM_MOUSEMOVE,
    WM_LBUTTONDOWN,
    WM_LBUTTONUP,
    WM_RBUTTONDOWN,
    WM_RBUTTONUP,
    WM_MBUTTONDOWN,
    WM_MBUTTONUP,
    WM_XBUTTONDOWN,
    WM_XBUTTONUP,
    WM_MOUSEWHEEL,
}


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)

user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int,
    HOOKPROC,
    wintypes.HINSTANCE,
    wintypes.DWORD,
]
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.CallNextHookEx.argtypes = [
    ctypes.c_void_p,
    ctypes.c_int,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.GetMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG),
    wintypes.HWND,
    wintypes.UINT,
    wintypes.UINT,
]
user32.GetMessageW.restype = wintypes.BOOL


class MousePause:
    """§9.6: pause the air cursor while the physical mouse is in use.

    The controller closes its camera while paused, so there is no live hand
    position to test here. "Hand inside the box" is enforced by the box gate
    in the frame loop instead: the cursor does not move while the hand is
    outside, so resuming with the hand away from the box cannot move anything.
    """

    def __init__(self, controller, log, seconds: float):
        self.controller = controller
        self.log = log
        self.seconds = max(0.5, float(seconds))
        self._lock = threading.Lock()
        self._paused = False
        self._timer = None

    def trigger(self) -> None:
        with self._lock:
            if not self._paused:
                # Only an active cursor can be taken over; once paused, the
                # controller is inactive, so this check must come first.
                if not self.controller.active:
                    return
                self._paused = True
                self.controller.set_mouse_takeover(True)
                self.controller.set_active(False)
                self.log("mouse pause")
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.seconds, self._resume)
            self._timer.daemon = True
            self._timer.start()

    def _resume(self) -> None:
        with self._lock:
            if not self._paused:
                return
            self._paused = False
            self._timer = None
        self.controller.set_mouse_takeover(False)
        self.controller.set_active(True)
        self.log("mouse resume")

    def confirm(self) -> None:
        """§9.6: a physical button while paused hands control back at once."""
        with self._lock:
            paused = self._paused
        if paused:
            self._resume()

    def cancel(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._paused = False
        self.controller.set_mouse_takeover(False)


class MouseHook:
    def __init__(self, log, on_physical, on_confirm=None):
        self.log = log
        self.on_physical = on_physical
        self.on_confirm = on_confirm
        self._hook = None
        self._proc = HOOKPROC(self._callback)
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="airpoint-mousehook"
        )

    def start(self) -> None:
        self._thread.start()

    def _callback(self, n_code, w_param, l_param):
        if n_code == HC_ACTION and w_param in _MOUSE_MESSAGES:
            try:
                info = ctypes.cast(
                    l_param, ctypes.POINTER(MSLLHOOKSTRUCT)
                ).contents
                if (
                    not (info.flags & LLMHF_INJECTED)
                    and info.dwExtraInfo != MOUSE_MARKER
                ):
                    self.on_physical()
                    if w_param in _CONFIRM_MESSAGES and self.on_confirm is not None:
                        # §9.6: a side button on the physical mouse is the
                        # "physical confirm" that hands control back now.
                        self.on_confirm()
            except Exception:
                pass
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _run(self) -> None:
        self._hook = user32.SetWindowsHookExW(
            WH_MOUSE_LL, self._proc, None, 0
        )
        if not self._hook:
            self.log(
                f"mouse hook failed: {ctypes.get_last_error()}"
            )
            return
        self.log("mouse hook ready")
        message = wintypes.MSG()
        while user32.GetMessageW(
            ctypes.byref(message), None, 0, 0
        ) != 0:
            pass
