from __future__ import annotations

import tkinter as tk

import cv2

from .config import Config


def _label(text: str, command, parent) -> tk.Button:
    return tk.Button(
        parent,
        text=text,
        command=command,
        font=("Microsoft YaHei UI", 12),
        relief="flat",
        bg="#2b2b2b",
        fg="#f0f0f0",
        activebackground="#3d3d3d",
        activeforeground="#ffffff",
        padx=12,
        pady=6,
        bd=0,
        highlightthickness=0,
    )


class ControlBar:
    """A small always-on-top bar that the air cursor can click.

    At six to nine metres the tray icon is not reachable, so the bar repeats
    the few actions a lesson actually needs.
    """

    def __init__(self, root, controller, config: Config, on_settings, on_exit,
                 on_recalibrate=None, on_stop=None):
        self.root = root
        self.controller = controller
        self.config = config
        self.on_settings = on_settings
        self.on_exit = on_exit
        self.on_recalibrate = on_recalibrate
        self.on_stop = on_stop
        self.win = None
        self._toggle = None
        self._status = None
        self._tier_button = None
        self._gesture_button = None
        self._seq = -1

    def show(self) -> None:
        if self.win is not None:
            try:
                if self.win.winfo_exists():
                    self.win.lift()
                    return
            except tk.TclError:
                pass

        win = tk.Toplevel(self.root)
        self.win = win
        win.title("OpticalInputSvc 控制条")
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        try:
            win.attributes("-alpha", 0.88)
        except tk.TclError:
            pass
        win.configure(bg="#1b1b1b")

        frame = tk.Frame(win, bg="#1b1b1b", padx=8, pady=6)
        frame.pack()

        self._status = tk.Label(
            frame,
            text="● 暂停",
            font=("Microsoft YaHei UI", 11),
            bg="#1b1b1b",
            fg="#ffb400",
            padx=6,
        )
        self._status.pack(side="left")

        self._toggle = _label("激活", self._toggle_active, frame)
        self._toggle.pack(side="left", padx=2)
        _label("左键", lambda: self._click("left"), frame).pack(
            side="left", padx=2
        )
        _label("右键", lambda: self._click("right"), frame).pack(
            side="left", padx=2
        )
        _label("模式", self._toggle_mode, frame).pack(side="left", padx=2)
        self._tier_button = _label("档位", self._cycle_tier, frame)
        self._tier_button.pack(side="left", padx=2)
        self._gesture_button = _label("手势", self._cycle_click, frame)
        self._gesture_button.pack(side="left", padx=2)
        _label("标定", self._recalibrate, frame).pack(side="left", padx=2)
        _label("预览", self._preview, frame).pack(side="left", padx=2)
        _label("设置", self._settings, frame).pack(side="left", padx=2)
        _label("急停", self._stop, frame).pack(side="left", padx=2)
        _label("退出", self._exit, frame).pack(side="left", padx=2)

        win.update_idletasks()
        width = win.winfo_reqwidth()
        screen_w = win.winfo_screenwidth()
        screen_h = win.winfo_screenheight()
        win.geometry(f"+{(screen_w - width) // 2}+{screen_h - 96}")

        win.protocol("WM_DELETE_WINDOW", self.hide)
        self._refresh()
        self._poll()

    def hide(self) -> None:
        if self.win is not None:
            try:
                self.win.destroy()
            except tk.TclError:
                pass
        self.win = None

    def _toggle_active(self) -> None:
        self.controller.set_active(not self.controller.active)
        self._refresh()

    def _click(self, name: str) -> None:
        from . import inject

        inject.button(name, True)
        inject.button(name, False)

    def _toggle_mode(self) -> None:
        current = str(self.config.get("pointer.mode", "auto"))
        order = ["auto", "absolute", "relative"]
        try:
            index = order.index(current)
        except ValueError:
            index = 0
        self.config.set("pointer.mode", order[(index + 1) % len(order)])
        self.controller.reload()

    def _cycle_tier(self) -> None:
        order = ["", "A", "B", "C", "D", "E"]
        current = str(self.config.get("tier.lock", "")).upper()
        try:
            index = order.index(current)
        except ValueError:
            index = 0
        value = order[(index + 1) % len(order)]
        self.config.set("tier.lock", value)
        self.config.set("tier.mode", "manual" if value else "auto")
        self.config.save()
        self._refresh()

    def _cycle_click(self) -> None:
        order = ["auto", "pinch", "fist"]
        current = str(self.config.get("gesture.click", "auto"))
        try:
            index = order.index(current)
        except ValueError:
            index = 0
        self.config.set("gesture.click", order[(index + 1) % len(order)])
        self.config.save()
        self._refresh()

    def _preview(self) -> None:
        from .preview import open_preview

        open_preview(self.root, self.controller.preview, self.controller,
                     self.config)

    def _settings(self) -> None:
        if self.on_settings is not None:
            self.on_settings()

    def _recalibrate(self) -> None:
        if self.on_recalibrate is not None:
            self.on_recalibrate()

    def _stop(self) -> None:
        self.controller.set_active(False)
        if self.on_stop is not None:
            self.on_stop()
        self._refresh()

    def _exit(self) -> None:
        if self.on_exit is not None:
            self.on_exit()

    def _refresh(self) -> None:
        if self.win is None:
            return
        active = self.controller.active
        recording = self.controller.recording
        text = "● 录制" if recording else ("● 激活" if active else "● 暂停")
        color = "#ff4040" if recording else ("#22c55e" if active else "#ffb400")
        try:
            self._status.configure(text=text, fg=color)
            self._toggle.configure(text="暂停" if active else "激活")
            lock = str(self.config.get("tier.lock", "")).upper()
            self._tier_button.configure(text=f"档位{lock}" if lock else "档位")
            click = str(self.config.get("gesture.click", "auto"))
            names = {"auto": "自动", "pinch": "捏合", "fist": "握拳"}
            self._gesture_button.configure(
                text=f"手势{names.get(click, click)}"
            )
        except tk.TclError:
            pass

    def _poll(self) -> None:
        if self.win is None:
            return
        self._refresh()
        try:
            self.win.after(400, self._poll)
        except tk.TclError:
            pass


class HoldRing:
    """A small ring under the cursor that fills while a long press builds.

    The ring is the only cue that the press is still a press and not yet a
    right click, which matters at six metres where the cursor itself is tiny.
    """

    def __init__(self, root, controller, config: Config):
        self.root = root
        self.controller = controller
        self.config = config
        self.win = None
        self._canvas = None
        self._arc = None
        self._shown = False
        self._size = 64

    def _build(self) -> None:
        from .magnifier import _click_through

        win = tk.Toplevel(self.root)
        self.win = win
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        try:
            win.attributes("-alpha", 0.85)
        except tk.TclError:
            pass
        win.configure(bg="#101010")
        size = self._size
        canvas = tk.Canvas(
            win, width=size, height=size, bg="#101010",
            highlightthickness=0, bd=0,
        )
        canvas.pack()
        self._canvas = canvas
        self._arc = canvas.create_arc(
            6, 6, size - 6, size - 6,
            start=90, extent=0, style="arc",
            outline="#00d4ff", width=5,
        )
        win.update_idletasks()
        _click_through(win)
        win.withdraw()

    def show(self, progress: float) -> None:
        if self.win is None:
            self._build()
        if self.win is None:
            return
        from . import inject

        x, y = inject.get_cursor()
        size = self._size
        try:
            self.win.geometry(f"+{int(x - size / 2)}+{int(y - size / 2)}")
            self._canvas.itemconfigure(
                self._arc, extent=-max(0.0, min(1.0, progress)) * 360
            )
            if not self._shown:
                self.win.deiconify()
                self._shown = True
        except tk.TclError:
            self.win = None

    def hide(self) -> None:
        if self.win is None or not self._shown:
            return
        try:
            self.win.withdraw()
        except tk.TclError:
            self.win = None
        self._shown = False

    def stop(self) -> None:
        if self.win is not None:
            try:
                self.win.destroy()
            except tk.TclError:
                pass
        self.win = None


class ScreenHud:
    """Draws the mapping box and a state badge over the real desktop.

    Transparent, click-through, and never focused, so the box can be seen from
    the back of the room while the teacher keeps using the whiteboard.
    """

    KEY = "#010203"

    def __init__(self, root, controller, config: Config):
        self.root = root
        self.controller = controller
        self.config = config
        self.win = None
        self._canvas = None
        self._box = None
        self._badge = None
        self._text = ""
        self._shown = False

    def _build(self) -> None:
        from .magnifier import _click_through

        win = tk.Toplevel(self.root)
        self.win = win
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=self.KEY)
        try:
            win.attributes("-transparentcolor", self.KEY)
        except tk.TclError:
            pass
        screen_w = win.winfo_screenwidth()
        screen_h = win.winfo_screenheight()
        win.geometry(f"{screen_w}x{screen_h}+0+0")
        canvas = tk.Canvas(
            win, width=screen_w, height=screen_h, bg=self.KEY,
            highlightthickness=0, bd=0,
        )
        canvas.pack()
        self._canvas = canvas
        self._box = canvas.create_rectangle(
            0, 0, 0, 0, outline="#00d4ff", width=3, state="hidden",
        )
        self._badge = canvas.create_text(
            24, 24, anchor="nw", text="", fill="#ffb400",
            font=("Microsoft YaHei UI", 20, "bold"), state="hidden",
        )
        win.update_idletasks()
        _click_through(win)
        win.withdraw()

    def refresh(self) -> None:
        if self.win is None:
            self._build()
        if self.win is None:
            return
        try:
            screen_w = self.win.winfo_screenwidth()
            screen_h = self.win.winfo_screenheight()
            box = self.config.get("pointer.box") or []
            draw_box = bool(self.config.get("overlay.box", False)) and len(box) == 4
            if draw_box:
                x0, y0, x1, y1 = [float(item) for item in box]
                self._canvas.coords(
                    self._box,
                    int(x0 * screen_w), int(y0 * screen_h),
                    int(x1 * screen_w), int(y1 * screen_h),
                )
                self._canvas.itemconfigure(self._box, state="normal")
            else:
                self._canvas.itemconfigure(self._box, state="hidden")

            recording = self.controller.recording
            active = self.controller.active
            text = ""
            if recording:
                text = "● 录制"
            elif not active:
                text = "● 暂停"
            if text != self._text:
                self._text = text
                self._canvas.itemconfigure(
                    self._badge,
                    text=text,
                    fill="#ff4040" if recording else "#ffb400",
                    state="normal" if text else "hidden",
                )

            if (draw_box or text) and not self._shown:
                self.win.deiconify()
                self._shown = True
            elif not draw_box and not text and self._shown:
                self.win.withdraw()
                self._shown = False
        except tk.TclError:
            self.win = None

    def stop(self) -> None:
        if self.win is not None:
            try:
                self.win.destroy()
            except tk.TclError:
                pass
        self.win = None


def draw_status(frame, stats: dict) -> None:
    """Burn a compact status line into the preview frame."""
    if frame is None:
        return
    height, width = frame.shape[:2]
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (width, 30), (20, 20, 20), -1)
    cv2.addWeighted(overlay, 0.6, frame, 0.4, 0, frame)

    state = "REC" if stats.get("recording") else (
        "ACTIVE" if stats.get("active") else "PAUSED"
    )
    color = (80, 80, 255) if stats.get("recording") else (
        (80, 220, 80) if stats.get("active") else (80, 190, 255)
    )
    text = (
        f"{state}  {stats.get('mode', '')}  tier {stats.get('tier', '-')}  "
        f"{stats.get('fps', 0.0):.1f} fps  {stats.get('perf', '')}"
    )
    if stats.get("presentation"):
        text = "DEMO  " + text
    cv2.putText(
        frame, text, (8, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1,
        cv2.LINE_AA,
    )
