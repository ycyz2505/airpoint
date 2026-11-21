from __future__ import annotations

import ctypes
import sys
import threading
import time

from . import updater
from .config import Config
from .controller import Controller
from .hotkeys import Hotkeys
from .logging_util import make_logger
from .mousehook import MouseHook, MousePause
from .paths import data_dir, log_dir, updates_dir
from .preview import PreviewBus, open_preview
from .profiles import ProfileStore
from .security import Security
from .tray import create_icon
from .ui import UiThread, ask_new_password, ask_password, open_settings, show_error
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
    config = Config()
    ProfileStore(config).load_last()
    security = Security(config)
    log = make_logger("app")
    log(f"start {__version__}")

    controller = Controller(config, log, preview=PreviewBus())
    controller.start()
    mouse_pause = MousePause(
        controller,
        log,
        float(config.get("pointer.mouse_pause_sec", 3)),
    )

    ui = UiThread()
    ui.start()

    icon = None

    def first_run(root) -> None:
        if security.has_password:
            return
        password = ask_new_password(root)
        if password:
            security.set_password(password)

    def locked(root, action) -> None:
        if not security.has_password:
            password = ask_new_password(root)
            if password is None:
                return
            security.set_password(password)
            action()
            return
        password = ask_password(root)
        if password is None:
            return
        if not security.check(password):
            wait = security.locked_for()
            show_error(
                root,
                f"密码错误。请 {wait} 秒后再试。" if wait else "密码错误。",
            )
            return
        action()

    def on_toggle() -> None:
        mouse_pause.cancel()
        controller.set_active(not controller.active)

    def on_settings() -> None:
        ui.post(
            lambda root: locked(
                root,
                lambda: open_settings(
                    root,
                    config,
                    security,
                    __version__,
                    on_saved=controller.reload,
                ),
            )
        )

    def on_preview() -> None:
        ui.post(
            lambda root: locked(
                root,
                lambda: open_preview(
                    root, controller.preview, controller, config
                ),
            )
        )

    def do_exit() -> None:
        mouse_pause.cancel()
        controller.stop()
        if icon is not None:
            icon.stop()

    def on_exit() -> None:
        ui.post(lambda root: locked(root, do_exit))

    def on_check() -> None:
        threading.Thread(
            target=updater.check_and_update,
            args=(log,),
            kwargs={"on_ready": icon.stop, "delay": 0.0},
            daemon=True,
        ).start()

    def on_record() -> None:
        if controller.recording:
            controller.stop_recording()
        else:
            controller.start_recording()

    icon = create_icon(
        on_toggle,
        on_preview,
        on_settings,
        on_check,
        on_record,
        on_exit,
        lambda: controller.active,
        lambda: controller.recording,
    )

    marker = updates_dir() / f"started-{__version__}.ok"
    try:
        marker.write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass

    if not security.has_password:
        ui.post(first_run)

    hotkeys = Hotkeys(
        config,
        log,
        on_toggle,
        lambda: controller.set_active(False),
    )
    hotkeys.start()

    if bool(config.get("pointer.mouse_pause", True)):
        MouseHook(log, mouse_pause.trigger).start()

    if bool(config.get("pointer.active_on_start", False)):
        controller.set_active(True)

    if "--no-update" not in sys.argv and bool(
        config.get("update.enabled", True)
    ):
        threading.Thread(
            target=updater.check_and_update,
            args=(log,),
            kwargs={"on_ready": icon.stop},
            daemon=True,
        ).start()

    icon.run()
