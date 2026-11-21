from __future__ import annotations

import ctypes
import sys
import threading
import time

from . import updater
from .paths import data_dir, log_dir, updates_dir
from .tray import create_icon
from .version import __version__

ERROR_ALREADY_EXISTS = 183
_MUTEX = None


def _single_instance() -> bool:
    global _MUTEX
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_bool,
        ctypes.c_wchar_p,
    ]
    _MUTEX = kernel32.CreateMutexW(None, False, "Local\\OpticalInputSvc")
    if not _MUTEX:
        return False
    return ctypes.get_last_error() != ERROR_ALREADY_EXISTS


def _dpi() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass


def main() -> None:
    if "--version" in sys.argv:
        print(__version__)
        return
    _dpi()
    if not _single_instance():
        return

    data_dir()
    log_dir()
    updates_dir()
    log = updater.make_logger()
    log(f"start {__version__}")

    icon = None

    def check() -> None:
        threading.Thread(
            target=updater.check_and_update,
            args=(log,),
            kwargs={"on_ready": icon.stop if icon else None, "delay": 0.0},
            daemon=True,
        ).start()

    icon = create_icon(check, lambda: icon.stop())

    marker = updates_dir() / f"started-{__version__}.ok"
    try:
        marker.write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass

    if "--no-update" not in sys.argv:
        threading.Thread(
            target=updater.check_and_update,
            args=(log,),
            kwargs={"on_ready": icon.stop},
            daemon=True,
        ).start()

    icon.run()
