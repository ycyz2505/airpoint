from __future__ import annotations

import ctypes
import os
import sys
import threading
import time

from . import cursorsize
from . import startup
from . import updater
from .config import Config
from .controller import Controller
from .hotkeys import Hotkeys
from .logging_util import make_logger
from .mousehook import MouseHook, MousePause
from .overlay import ControlBar
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


def _replay_cli() -> None:
    """Analyze a recording without starting the service."""
    index = sys.argv.index("--replay")
    rest = [item for item in sys.argv[index + 1:] if not item.startswith("-")]
    if not rest:
        _message("用法：OpticalInputSvc.exe --replay <录像.avi>")
        return

    from .replay import analyze

    video = rest[0]
    csv_path = rest[1] if len(rest) > 1 else None
    try:
        summary = analyze(video, csv_path=csv_path)
    except Exception as exc:
        _message(f"回放失败：{exc}")
        return

    text = (
        f"帧数：{summary['frames']}（{summary['duration_s']} 秒）\n"
        f"姿态命中：{summary['pose_hit_pct']}%\n"
        f"手部命中：{summary['hand_hit_pct']}%\n"
        f"手掌中位：{summary['palm_px']['median']}px\n"
        f"档位分布：{summary['tier']}\n"
        f"光标路径：{summary['cursor']['path_px']}px\n"
        f"抖动：dx {summary['cursor']['dx_std']} / "
        f"dy {summary['cursor']['dy_std']}\n"
        f"点击：{summary['gestures']['clicks']}，"
        f"右键：{summary['gestures']['right_clicks']}，"
        f"拖拽：{summary['gestures']['drags']}"
    )
    _message(text)


def _message(text: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo("OpticalInputSvc", text)
        root.destroy()
    except Exception:
        pass


def main() -> None:
    if "--version" in sys.argv:
        print(__version__)
        return
    if "--replay" in sys.argv:
        _replay_cli()
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
    log = make_logger("app", config)
    log(f"start {__version__}")

    if str(config.get("update.apply_when", "startup")) != "manual":
        updater.apply_staged(log)

    if getattr(sys, "frozen", False):
        want = bool(config.get("startup.auto_run", False))
        if want != startup.is_enabled():
            if not startup.set_enabled(want):
                log("autostart registry update failed")

    controller = Controller(config, log, preview=PreviewBus())
    controller.start()
    mouse_pause = MousePause(
        controller,
        log,
        float(config.get("pointer.mouse_pause_sec", 3)),
    )

    ui = UiThread()
    ui.start()

    original_cursor = cursorsize.current()

    def apply_cursor_size() -> None:
        if bool(config.get("overlay.big_cursor", False)):
            cursorsize.set_size(int(config.get("overlay.big_cursor_size", 3)))
        elif original_cursor:
            cursorsize.set_size(original_cursor)

    ui.post(lambda root: apply_cursor_size())

    def restore_cursor() -> None:
        if original_cursor:
            cursorsize.set_size(original_cursor)

    magnifier = None

    def start_magnifier(root) -> None:
        nonlocal magnifier
        if magnifier is not None:
            return
        from .magnifier import WritingMagnifier

        magnifier = WritingMagnifier(root, controller, config)
        magnifier.start()

    ui.post(start_magnifier)

    hold_ring = None

    def start_hold_ring(root) -> None:
        nonlocal hold_ring
        if hold_ring is not None:
            return
        from .overlay import HoldRing

        hold_ring = HoldRing(root, controller, config)
        _poll_hold_ring()

    def _poll_hold_ring() -> None:
        if hold_ring is None:
            return
        progress = float(controller.hold_progress or 0.0)
        if progress > 0.02 and bool(config.get("overlay.hold_ring", True)):
            hold_ring.show(progress)
        else:
            hold_ring.hide()
        root = hold_ring.root
        try:
            root.after(50, _poll_hold_ring)
        except tk.TclError:
            pass

    ui.post(start_hold_ring)

    hud = None

    def start_hud(root) -> None:
        nonlocal hud
        if hud is not None:
            return
        from .overlay import ScreenHud

        hud = ScreenHud(root, controller, config)
        _poll_hud()

    def _poll_hud() -> None:
        if hud is None:
            return
        hud.refresh()
        root = hud.root
        try:
            root.after(300, _poll_hud)
        except tk.TclError:
            pass

    ui.post(start_hud)

    icon = None

    def first_run(root) -> None:
        if security.has_password:
            return
        password = ask_new_password(root)
        if password:
            security.set_password(password)
            if not bool(config.get("wizard.done", False)):
                open_wizard_now(root)

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

    def on_mode() -> None:
        current = str(config.get("pointer.mode", "auto"))
        order = ["auto", "absolute", "relative"]
        try:
            index = order.index(current)
        except ValueError:
            index = 0
        config.set("pointer.mode", order[(index + 1) % len(order)])
        config.save()
        controller.reload()
        log(f"pointer mode {config.get('pointer.mode')}")

    bar = None

    def on_bar() -> None:
        ui.post(lambda root: show_bar(root))

    def show_bar(root) -> None:
        nonlocal bar
        if bool(config.get("overlay.presentation", False)):
            log("control bar hidden: presentation mode")
            return
        if bar is None:
            bar = ControlBar(
                root,
                controller,
                config,
                on_settings=lambda: on_settings(),
                on_exit=lambda: on_exit(),
                on_recalibrate=lambda: on_recalibrate(),
            )
        bar.show()

    def toggle_bar(root) -> None:
        if bar is not None and bar.win is not None:
            bar.hide()
            return
        show_bar(root)

    def start_bar_poll(root) -> None:
        def poll() -> None:
            if controller.take_bar_request():
                toggle_bar(root)
            try:
                root.after(200, poll)
            except tk.TclError:
                pass

        poll()

    ui.post(start_bar_poll)

    def on_hotkey_mode() -> None:
        mouse_pause.cancel()
        on_mode()

    def on_saved() -> None:
        controller.reload()
        ui.post(lambda root: apply_cursor_size())

    def on_settings() -> None:
        ui.post(
            lambda root: locked(
                root,
                lambda: open_settings(
                    root,
                    config,
                    security,
                    __version__,
                    on_saved=on_saved,
                    controller=controller,
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
        if magnifier is not None:
            magnifier.stop()
        if hold_ring is not None:
            hold_ring.stop()
        if hud is not None:
            hud.stop()
        restore_cursor()
        controller.stop()
        if icon is not None:
            icon.stop()

    def on_exit() -> None:
        ui.post(lambda root: locked(root, do_exit))

    def on_check() -> None:
        threading.Thread(
            target=updater.check_and_update,
            args=(log,),
            kwargs={"on_ready": icon.stop, "delay": 0.0, "config": config},
            daemon=True,
        ).start()

    def on_record() -> None:
        if controller.recording:
            controller.stop_recording()
        else:
            controller.start_recording()

    def on_recalibrate() -> None:
        ui.post(lambda root: locked(root, lambda: recalibrate(root)))

    def recalibrate(root) -> None:
        open_preview(root, controller.preview, controller, config)
        if not config.get("pointer.box"):
            show_error(root, "请先在预览窗口里画框，再做标定。")
            return
        from .calibrate import run_calibration

        controller.set_active(True)
        run_calibration(
            root,
            controller.preview,
            controller,
            config,
            root.winfo_screenwidth(),
            root.winfo_screenheight(),
        )

    def on_survey() -> None:
        ui.post(lambda root: locked(root, lambda: open_survey_now(root)))

    def open_survey_now(root) -> None:
        from .survey import open_survey

        open_survey(root, controller.preview, controller, config)

    def on_wizard() -> None:
        ui.post(lambda root: locked(root, lambda: open_wizard_now(root)))

    def open_wizard_now(root) -> None:
        from .wizard import open_wizard

        open_wizard(root, controller.preview, controller, config)

    def on_data_dir() -> None:
        ui.post(lambda root: locked(root, lambda: open_data_dir(root)))

    def open_data_dir(root) -> None:
        try:
            os.startfile(str(data_dir()))
        except OSError as exc:
            show_error(root, f"无法打开数据目录：{exc}")

    icon = create_icon(
        on_toggle,
        on_preview,
        on_settings,
        on_check,
        on_record,
        on_exit,
        lambda: controller.active,
        lambda: controller.recording,
        on_recalibrate=on_recalibrate,
        on_data_dir=on_data_dir,
        on_survey=on_survey,
        on_wizard=on_wizard,
        describe=controller.describe,
    )
    marker = updates_dir() / f"started-{__version__}.ok"
    try:
        marker.write_text(str(time.time()), encoding="utf-8")
        (updates_dir() / "started-pending.ok").write_text(
            str(time.time()), encoding="utf-8"
        )
    except OSError:
        pass

    if not security.has_password:
        ui.post(first_run)

    hotkeys = Hotkeys(
        config,
        log,
        on_toggle,
        lambda: controller.set_active(False),
        on_mode=on_hotkey_mode,
        on_bar=on_bar,
    )
    hotkeys.start()

    if bool(config.get("overlay.control_bar", False)):
        ui.post(lambda root: show_bar(root))

    if bool(config.get("pointer.mouse_pause", True)):
        MouseHook(log, mouse_pause.trigger).start()

    if bool(config.get("pointer.active_on_start", False)):
        controller.set_active(True)

    def update_loop() -> None:
        hours = float(config.get("update.interval_hours", 6) or 0)
        while hours > 0:
            time.sleep(max(0.5, hours) * 3600.0)
            if not bool(config.get("update.enabled", True)):
                continue
            if controller.recording:
                continue
            updater.check_and_update(
                log, on_ready=icon.stop, delay=0.0, config=config
            )

    if "--no-update" not in sys.argv and bool(
        config.get("update.enabled", True)
    ):
        apply_now = str(config.get("update.apply_when", "startup")) == "startup"
        if bool(config.get("update.check_on_start", True)):
            threading.Thread(
                target=updater.check_and_update,
                args=(log,),
                kwargs={
                    "on_ready": icon.stop,
                    "apply_now": apply_now,
                    "config": config,
                },
                daemon=True,
            ).start()
        threading.Thread(target=update_loop, daemon=True).start()

    icon.run()
