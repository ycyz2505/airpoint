from __future__ import annotations

import ctypes
import tkinter as tk
from ctypes import wintypes

try:
    from PIL import Image, ImageDraw, ImageTk
except ImportError:  # geometry helpers stay importable without Pillow
    Image = ImageDraw = ImageTk = None

SRCCOPY = 0x00CC0020
HALFTONE = 4
BI_RGB = 0
DIB_RGB_COLORS = 0

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000

_ACTIVE_MS = 80
_IDLE_MS = 400


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 3),
    ]


_USER32 = None
_GDI32 = None
_GDI_READY = False


def _dlls():
    """Load the GDI entry points once, with pointer-sized handle types."""
    global _USER32, _GDI32, _GDI_READY
    if _GDI_READY:
        return _USER32, _GDI32
    _GDI_READY = True
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

        user32.GetDesktopWindow.restype = wintypes.HWND
        user32.GetDC.argtypes = [wintypes.HWND]
        user32.GetDC.restype = ctypes.c_void_p
        user32.ReleaseDC.argtypes = [wintypes.HWND, ctypes.c_void_p]
        user32.ReleaseDC.restype = ctypes.c_int
        user32.GetParent.argtypes = [wintypes.HWND]
        user32.GetParent.restype = wintypes.HWND

        gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
        gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
        gdi32.CreateCompatibleBitmap.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
        ]
        gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
        gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        gdi32.SelectObject.restype = ctypes.c_void_p
        gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
        gdi32.DeleteObject.restype = wintypes.BOOL
        gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
        gdi32.DeleteDC.restype = wintypes.BOOL
        gdi32.SetStretchBltMode.argtypes = [ctypes.c_void_p, ctypes.c_int]
        gdi32.SetStretchBltMode.restype = ctypes.c_int
        gdi32.StretchBlt.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.DWORD,
        ]
        gdi32.StretchBlt.restype = wintypes.BOOL
        gdi32.GetDIBits.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.UINT,
            wintypes.UINT,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.UINT,
        ]
        gdi32.GetDIBits.restype = ctypes.c_int
    except (OSError, AttributeError):
        _USER32 = None
        _GDI32 = None
        return None, None
    _USER32 = user32
    _GDI32 = gdi32
    return _USER32, _GDI32


def source_rect(
    cursor_x: float,
    cursor_y: float,
    screen_w: int,
    screen_h: int,
    zoom: float,
    size: int,
    origin: tuple[float, float] = (0.0, 0.0),
) -> tuple[int, int, int]:
    """Square screen region that fills the magnifier at the given zoom.

    ``cursor_*`` and the returned rect are virtual-desktop coordinates;
    ``origin`` is the target display's top-left, so the region stays on that
    display instead of being clamped to the primary one.
    """
    zoom = max(1.0, float(zoom))
    size = max(16, int(size))
    ox = int(round(float(origin[0])))
    oy = int(round(float(origin[1])))
    side = max(8, int(round(size / zoom)))
    side = min(side, int(screen_w), int(screen_h))
    left = int(round(cursor_x - ox - side / 2.0))
    top = int(round(cursor_y - oy - side / 2.0))
    left = max(0, min(int(screen_w) - side, left))
    top = max(0, min(int(screen_h) - side, top))
    return left + ox, top + oy, side


def window_position(
    cursor_x: float,
    cursor_y: float,
    screen_w: int,
    screen_h: int,
    size: int,
    offset: int = 48,
    origin: tuple[float, float] = (0.0, 0.0),
) -> tuple[int, int]:
    """Place the window beside the cursor, flipping near the screen edges."""
    size = max(16, int(size))
    ox = int(round(float(origin[0])))
    oy = int(round(float(origin[1])))
    x = int(round(cursor_x)) + offset
    y = int(round(cursor_y)) + offset
    if x + size > ox + screen_w:
        x = int(round(cursor_x)) - offset - size
    if y + size > oy + screen_h:
        y = int(round(cursor_y)) - offset - size
    x = max(ox, min(ox + int(screen_w) - size, x))
    y = max(oy, min(oy + int(screen_h) - size, y))
    return x, y


def grab(
    screen_x: int,
    screen_y: int,
    side: int,
    out_w: int,
    out_h: int,
) -> Image.Image | None:
    """Copy a screen square and stretch it into an RGB image."""
    if Image is None:
        return None
    user32, gdi32 = _dlls()
    if user32 is None or gdi32 is None:
        return None

    desktop = user32.GetDesktopWindow()
    src = user32.GetDC(desktop)
    if not src:
        return None
    mem = gdi32.CreateCompatibleDC(src)
    bmp = gdi32.CreateCompatibleBitmap(src, out_w, out_h) if mem else None
    if not mem or not bmp:
        if mem:
            gdi32.DeleteDC(mem)
        user32.ReleaseDC(desktop, src)
        return None

    old = gdi32.SelectObject(mem, bmp)
    gdi32.SetStretchBltMode(mem, HALFTONE)
    gdi32.StretchBlt(
        mem,
        0,
        0,
        out_w,
        out_h,
        src,
        int(screen_x),
        int(screen_y),
        int(side),
        int(side),
        SRCCOPY,
    )

    header = BITMAPINFO()
    header.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    header.bmiHeader.biWidth = out_w
    header.bmiHeader.biHeight = -out_h
    header.bmiHeader.biPlanes = 1
    header.bmiHeader.biBitCount = 32
    header.bmiHeader.biCompression = BI_RGB

    buffer = ctypes.create_string_buffer(out_w * out_h * 4)
    lines = gdi32.GetDIBits(
        mem,
        bmp,
        0,
        out_h,
        buffer,
        ctypes.byref(header),
        DIB_RGB_COLORS,
    )

    gdi32.SelectObject(mem, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(desktop, src)

    if not lines:
        return None
    return Image.frombytes(
        "RGB", (out_w, out_h), buffer.raw, "raw", "BGRX", 0, 1
    )


def _click_through(win: tk.Toplevel) -> None:
    """Let the air cursor and the real mouse pass over the magnifier."""
    user32, _gdi32 = _dlls()
    if user32 is None:
        return
    try:
        win.update_idletasks()
        handle = win.winfo_id()
        parent = user32.GetParent(handle)
        hwnd = parent or handle
        getter = getattr(user32, "GetWindowLongPtrW", None)
        setter = getattr(user32, "SetWindowLongPtrW", None)
        if getter is None or setter is None:
            getter = user32.GetWindowLongW
            setter = user32.SetWindowLongW
        getter.argtypes = [wintypes.HWND, ctypes.c_int]
        getter.restype = ctypes.c_ssize_t
        setter.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        setter.restype = ctypes.c_ssize_t
        style = getter(hwnd, GWL_EXSTYLE)
        style |= WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        setter(hwnd, GWL_EXSTYLE, style)
    except Exception:
        pass


class WritingMagnifier:
    """A small zoomed view beside the cursor while a stroke is drawn."""

    def __init__(self, root, controller, config):
        self.root = root
        self.controller = controller
        self.config = config
        self.win = None
        self._label = None
        self._photo = None
        self._shown = False
        self._last = (0, 0)
        self._closed = False

    def _enabled(self) -> bool:
        return bool(self.config.get("overlay.magnifier", False))

    def _size(self) -> int:
        try:
            return max(80, min(600, int(self.config.get(
                "overlay.magnifier_size", 220
            ))))
        except (TypeError, ValueError):
            return 220

    def _zoom(self) -> float:
        try:
            return max(1.2, min(6.0, float(self.config.get(
                "overlay.magnifier_zoom", 2.5
            ))))
        except (TypeError, ValueError):
            return 2.5

    def start(self) -> None:
        if self.win is None:
            self._build()
        self._tick()

    def stop(self) -> None:
        self._closed = True
        self._hide()
        if self.win is not None:
            try:
                self.win.destroy()
            except tk.TclError:
                pass
        self.win = None

    def _build(self) -> None:
        win = tk.Toplevel(self.root)
        self.win = win
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        try:
            win.attributes("-alpha", 0.96)
        except tk.TclError:
            pass
        win.configure(bg="#101010")
        self._label = tk.Label(
            win, bd=0, highlightthickness=1,
            highlightbackground="#7a7a7a", bg="#101010",
        )
        self._label.pack()
        _click_through(win)
        win.withdraw()

    def _hide(self) -> None:
        if not self._shown or self.win is None:
            return
        self._shown = False
        try:
            self.win.withdraw()
        except tk.TclError:
            pass

    def _show(self) -> None:
        if self._shown or self.win is None:
            return
        self._shown = True
        try:
            self.win.deiconify()
            self.win.attributes("-topmost", True)
        except tk.TclError:
            pass

    def _tick(self) -> None:
        if self._closed or self.win is None:
            return
        writing = bool(getattr(self.controller, "writing", False))
        if not self._enabled() or not writing or not self.controller.active:
            self._hide()
        else:
            self._draw()
        interval = _ACTIVE_MS if self._shown else _IDLE_MS
        try:
            self.root.after(interval, self._tick)
        except tk.TclError:
            pass

    def _draw(self) -> None:
        from . import inject

        size = self._size()
        zoom = self._zoom()
        cursor_x, cursor_y = inject.get_cursor()
        screen_left, screen_top, screen_w, screen_h = inject.screen_rect(
            self.config.get("pointer.screen", "auto")
        )
        if screen_w <= 0 or screen_h <= 0:
            self._hide()
            return
        origin = (float(screen_left), float(screen_top))

        left, top, side = source_rect(
            cursor_x, cursor_y, screen_w, screen_h, zoom, size, origin
        )
        image = grab(left, top, side, size, size)
        if image is None:
            self._hide()
            return

        draw = ImageDraw.Draw(image)
        cx = (cursor_x - left) * size / float(side)
        cy = (cursor_y - top) * size / float(side)
        draw.line((cx - 10, cy, cx + 10, cy), fill=(255, 60, 60), width=1)
        draw.line((cx, cy - 10, cx, cy + 10), fill=(255, 60, 60), width=1)

        try:
            self._photo = ImageTk.PhotoImage(image=image)
            self._label.configure(image=self._photo, width=size, height=size)
        except tk.TclError:
            return

        wx, wy = window_position(
            cursor_x, cursor_y, screen_w, screen_h, size + 2, origin=origin
        )
        if (wx, wy) != self._last:
            self._last = (wx, wy)
            try:
                self.win.geometry(f"+{wx}+{wy}")
            except tk.TclError:
                return
        self._show()
