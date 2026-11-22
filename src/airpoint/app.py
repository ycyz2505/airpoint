from __future__ import annotations

import ctypes
import contextlib
import io
import os
import sys
import threading
import time
from tkinter import TclError

from . import cursorsize
from . import inject
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
from .ui import (
    UiThread,
    ask_confirm,
    ask_new_password,
    ask_password,
    open_settings,
    show_error,
)
from .version import __version__

ERROR_ALREADY_EXISTS = 183
EVENT_MODIFY_STATE = 0x0002
_MUTEX = None
_EVENT = None
NOTIFY_EVENT = "Local\\OpticalInputSvcShow"


def _notify_running_instance() -> bool:
    """Poke the instance that already holds the mutex (§12.3).

    A second double-click must not open a window, but silently doing nothing
    looks like the program is broken, so it asks the running copy to flash
    its tray balloon instead.
    """
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenEventW.restype = ctypes.c_void_p
    kernel32.OpenEventW.argtypes = [
        ctypes.c_uint32,
        ctypes.c_bool,
        ctypes.c_wchar_p,
    ]
    kernel32.SetEvent.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, NOTIFY_EVENT)
    if not handle:
        return False
    try:
        return bool(kernel32.SetEvent(handle))
    finally:
        kernel32.CloseHandle(handle)


def _single_instance() -> bool:
    global _MUTEX, _EVENT
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
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        _notify_running_instance()
        return False
    kernel32.CreateEventW.restype = ctypes.c_void_p
    kernel32.CreateEventW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_bool,
        ctypes.c_bool,
        ctypes.c_wchar_p,
    ]
    _EVENT = kernel32.CreateEventW(None, False, False, NOTIFY_EVENT)
    return True


def _dpi() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass


def _replay_cli() -> None:
    """Analyze a recording without starting the service."""
    index = sys.argv.index("--replay")
    rest = sys.argv[index + 1:]
    if not rest or rest[0].startswith("-"):
        _message(
            "用法：OpticalInputSvc.exe --replay <录像.avi> "
            "[--csv 输出.csv] [--annotate 标注.avi] [--profile 档案] "
            "[--compare 另一档案]"
        )
        return

    from .replay import main as replay_main

    # The packaged build has no console, so argparse's errors and the summary
    # table are captured and shown in a dialog instead of disappearing.
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer), \
                contextlib.redirect_stderr(buffer):
            replay_main([*rest, "--brief"])
    except SystemExit as exc:
        if int(getattr(exc, "code", 0) or 0) != 0:
            _message(buffer.getvalue().strip() or "参数有误。")
        return
    except Exception as exc:
        _message(f"回放失败：{exc}")
        return
    text = buffer.getvalue().strip()
    if text:
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


def _watch_second_launch(icon, log) -> None:
    """Flash the tray icon when a second copy is started (§12.3)."""
    if not _EVENT:
        return

    def loop() -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.WaitForSingleObject.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32
        ]
        while True:
            result = kernel32.WaitForSingleObject(_EVENT, 60_000)
            if result != 0:
                continue
            try:
                icon.notify("服务已在运行", "OpticalInputSvc")
            except Exception as exc:
                log(f"second-launch notice failed: {exc}")

    threading.Thread(
        target=loop, daemon=True, name="airpoint-second-launch"
    ).start()


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
    profiles = ProfileStore(config)
    loaded_profile = profiles.load_last()
    security = Security(config)
    log = make_logger("app", config)
    log(f"start {__version__}")
    if config.migrated:
        moved = ", ".join(
            f"{old}->{new}" if new else f"{old} dropped"
            for old, new in config.migrated
        )
        log(f"config migrated: {moved}")
        try:
            # Persist the upgraded shape so the next start is a no-op; the
            # pre-migration file is kept as config.json.bak by save().
            config.save()
        except OSError as exc:
            log(f"config migration not written: {exc}")
    updater.cleanup_leftovers(log)
    update_log = make_logger("update", config)

    if str(config.get("update.apply_when", "startup")) != "manual":
        updater.apply_staged(update_log)

    if getattr(sys, "frozen", False):
        want = bool(config.get("startup.auto_run", False))
        if want != startup.is_enabled():
            if not startup.set_enabled(want):
                log("autostart registry update failed")

    controller = Controller(config, log, preview=PreviewBus())
    # §9.5: a start of a fresh process may inherit a button left down by a
    # crash, so clear it before the first frame instead of waiting for a click.
    inject.release_all()
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
        except TclError:
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
        except TclError:
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
            message = controller.take_notice()
            while message:
                if icon is not None:
                    try:
                        icon.notify(message, "OpticalInputSvc")
                    except Exception as exc:
                        log(f"notice failed: {exc}")
                message = controller.take_notice()
            try:
                root.after(200, poll)
            except TclError:
                pass

        poll()

    ui.post(start_bar_poll)

    def on_hotkey_mode() -> None:
        mouse_pause.cancel()
        on_mode()

    def on_saved() -> None:
        controller.reload()
        hotkeys.rebind()
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
            args=(update_log,),
            kwargs={"on_ready": icon.stop, "delay": 0.0, "config": config},
            daemon=True,
        ).start()

    def on_record() -> None:
        if controller.recording:
            # Stopping is always allowed; §10.4 gates the start, not the end.
            controller.stop_recording()
            return

        def start(root) -> None:
            controller.start_recording()

        ui.post(lambda root: locked(root, lambda: start(root)))

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
        )

    def on_survey() -> None:
        ui.post(lambda root: locked(root, lambda: open_survey_now(root)))

    def open_survey_now(root) -> None:
        from .survey import open_survey

        open_survey(root, controller.preview, controller, config)

    def on_report() -> None:
        ui.post(lambda root: locked(root, lambda: run_env_report(root)))

    def run_env_report(root) -> None:
        """§14.2: answer the classroom questions that need no eyes on screen."""
        from .envreport import (
            build_report, camera_probe, describe_displays, write_report,
        )

        def work() -> None:
            # Opening the camera takes a second or two, and it is exclusive,
            # so the probe runs off the UI thread with the controller paused.
            displays = describe_displays()
            was_active = controller.active
            if was_active:
                controller.set_active(False)
                time.sleep(0.6)
            try:
                camera = camera_probe(config, log)
            finally:
                if was_active:
                    controller.set_active(True)
            text = build_report(displays, camera, config, __version__)
            try:
                path = write_report(displays, camera, config, __version__)
            except OSError as exc:
                ui.post(
                    lambda root, msg=str(exc): show_error(
                        root, f"报告写入失败：{msg}"
                    )
                )
                return
            ui.post(lambda root: show_env_report(root, text, path))

        threading.Thread(
            target=work, daemon=True, name="airpoint-env-report"
        ).start()

    def show_env_report(root, text: str, path) -> None:
        try:
            import tkinter as tk
            from tkinter import messagebox

            box = tk.Toplevel(root)
            box.title("OpticalInputSvc 环境自检")
            box.geometry("640x480")
            view = tk.Text(box, wrap="word", font=("Consolas", 10))
            view.pack(fill="both", expand=True, padx=8, pady=8)
            view.insert("1.0", text)
            view.configure(state="disabled")
            messagebox.showinfo(
                "OpticalInputSvc", f"报告已保存到\n{path}", parent=box
            )
        except TclError:
            pass

    def on_probe() -> None:
        ui.post(lambda root: locked(root, lambda: open_button_probe(root)))

    def open_button_probe(root) -> None:
        from .buttonprobe import open_probe

        open_probe(root)

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
        on_report=on_report,
        on_probe=on_probe,
        describe=controller.describe,
    )
    _watch_second_launch(icon, log)
    marker = updates_dir() / f"started-{__version__}.ok"
    try:
        marker.write_text(str(time.time()), encoding="utf-8")
        (updates_dir() / "started-pending.ok").write_text(
            str(time.time()), encoding="utf-8"
        )
    except OSError:
        pass

    installed = updater.take_pending_notice()
    if installed:
        def notify_update() -> None:
            try:
                icon.notify(f"已更新到 {installed}", "OpticalInputSvc")
            except Exception:
                pass

        threading.Timer(4.0, notify_update).start()

    if not security.has_password:
        ui.post(first_run)
    elif loaded_profile and profiles.seat_changed():
        def ask_redraw(root) -> None:
            if not ask_confirm(
                root,
                f"检测到新位置（当前档案「{loaded_profile}」是给别的位置"
                "存的），重新画框？",
            ):
                return
            open_wizard_now(root)

        ui.post(ask_redraw)

    hotkeys = Hotkeys(
        config,
        log,
        on_toggle,
        lambda: controller.set_active(False),
        on_mode=on_hotkey_mode,
        on_bar=on_bar,
    )
    hotkeys.start()
    config.watch(hotkeys.rebind)

    if bool(config.get("overlay.control_bar", False)):
        ui.post(lambda root: show_bar(root))

    if bool(config.get("pointer.mouse_pause", True)):
        MouseHook(log, mouse_pause.trigger, mouse_pause.confirm).start()

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
                update_log, on_ready=icon.stop, delay=0.0, config=config
            )

    if "--no-update" not in sys.argv and bool(
        config.get("update.enabled", True)
    ):
        apply_now = str(config.get("update.apply_when", "startup")) == "startup"
        if bool(config.get("update.check_on_start", True)):
            threading.Thread(
                target=updater.check_and_update,
                args=(update_log,),
                kwargs={
                    "on_ready": icon.stop,
                    "apply_now": apply_now,
                    "config": config,
                },
                daemon=True,
            ).start()
        threading.Thread(target=update_loop, daemon=True).start()

    icon.run()
