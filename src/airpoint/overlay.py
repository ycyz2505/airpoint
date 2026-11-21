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

    def __init__(self, root, controller, config: Config, on_settings, on_exit):
        self.root = root
        self.controller = controller
        self.config = config
        self.on_settings = on_settings
        self.on_exit = on_exit
        self.win = None
        self._toggle = None
        self._status = None
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
        _label("预览", self._preview, frame).pack(side="left", padx=2)
        _label("设置", self._settings, frame).pack(side="left", padx=2)

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

    def _preview(self) -> None:
        from .preview import open_preview

        open_preview(self.root, self.controller.preview, self.controller,
                     self.config)

    def _settings(self) -> None:
        if self.on_settings is not None:
            self.on_settings()

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
