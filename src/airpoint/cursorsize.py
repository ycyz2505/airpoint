from __future__ import annotations

import ctypes

SPI_GETCURSORSIZE = 0x2028
SPI_SETCURSORSIZE = 0x2029
SPIF_SENDCHANGE = 0x0002


def current() -> int:
    """The Windows cursor size multiplier, or 0 when it cannot be read."""
    try:
        user32 = ctypes.windll.user32
        value = ctypes.c_int(0)
        ok = user32.SystemParametersInfoW(
            SPI_GETCURSORSIZE, 0, ctypes.byref(value), 0
        )
        return int(value.value) if ok else 0
    except Exception:
        return 0


def set_size(size: int) -> bool:
    """Change the cursor size for this session only, not the saved setting."""
    try:
        user32 = ctypes.windll.user32
        size = max(1, min(8, int(size)))
        return bool(
            user32.SystemParametersInfoW(
                SPI_SETCURSORSIZE, size, None, SPIF_SENDCHANGE
            )
        )
    except Exception:
        return False
