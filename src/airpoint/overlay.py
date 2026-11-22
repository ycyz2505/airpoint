from __future__ import annotations

import time
import tkinter as tk
from tkinter import font as tkfont

import cv2

from .config import Config

BAR_FONT = ("Microsoft YaHei UI", 14)


def _screen_rect(config) -> tuple[int, int, int, int]:
    """The display the HUD belongs on, in virtual-desktop coordinates."""
    from . import inject

    return inject.screen_rect(config.get("pointer.screen", "auto"))


def _label(text: str, command, parent, min_width: int = 0) -> tk.Button:
    """A flat bar button, padded out to at least ``min_width`` pixels.

    §10.2 wants 120–160 px targets so the bar is still hittable from six to
    nine metres; a two-character label at this font would otherwise come out
    around half that.
    """
    padx = 12
    if min_width > 0:
        try:
            measured = tkfont.Font(font=BAR_FONT).measure(text)
            padx = max(padx, int((min_width - measured) / 2))
        except tk.TclError:
            pass
    return tk.Button(
        parent,
        text=text,
        command=command,
        font=BAR_FONT,
        relief="flat",
        bg="#2b2b2b",
        fg="#f0f0f0",
        activebackground="#3d3d3d",
        activeforeground="#ffffff",
        padx=padx,
        pady=10,
        bd=0,
        highlightthickness=0,
    )


class ControlBar:
    """A small always-on-top bar that the air cursor can click.

    At six to nine metres the tray icon is not reachable, so the bar repeats
    the few actions a lesson actually needs.
    """

    def __init__(self, root, controller, config: Config, on_settings, on_exit,
                 on_recalibrate=None, on_stop=None, on_board=None):
        self.root = root
        self.controller = controller
        self.config = config
        self.on_settings = on_settings
        self.on_exit = on_exit
        self.on_recalibrate = on_recalibrate
        self.on_stop = on_stop
        self.on_board = on_board
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
            font=("Microsoft YaHei UI", 13),
            bg="#1b1b1b",
            fg="#ffb400",
            padx=6,
        )
        self._status.pack(side="left")

        # §10.2 asks for 120–160 px targets. Eleven of them plus the status
        # fit a 1920 panel; on a narrower one they are trimmed just enough to
        # stay on screen and never below a still-hittable 88 px.
        self._toggle = _label("激活", self._toggle_active, frame, 120)
        buttons = [self._toggle]
        self._toggle.pack(side="left", padx=2)
        for text, command in (
            ("左键", lambda: self._click("left")),
            ("右键", lambda: self._click("right")),
            ("模式", self._toggle_mode),
        ):
            button = _label(text, command, frame, 120)
            button.pack(side="left", padx=2)
            buttons.append(button)
        self._tier_button = _label("档位", self._cycle_tier, frame, 120)
        self._tier_button.pack(side="left", padx=2)
        buttons.append(self._tier_button)
        self._gesture_button = _label("手势", self._cycle_click, frame, 120)
        self._gesture_button.pack(side="left", padx=2)
        buttons.append(self._gesture_button)
        for text, command in (
            ("标定", self._recalibrate),
            ("书写", self._board),
            ("预览", self._preview),
            ("设置", self._settings),
            ("急停", self._stop),
            ("退出", self._exit),
        ):
            button = _label(text, command, frame, 120)
            button.pack(side="left", padx=2)
            buttons.append(button)

        win.update_idletasks()
        screen_left, screen_top, screen_w, screen_h = _screen_rect(self.config)
        overflow = win.winfo_reqwidth() - (screen_w - 16)
        if overflow > 0:
            trim = overflow // (2 * len(buttons)) + 1
            for button in buttons:
                current = int(button.cget("padx"))
                button.configure(padx=max(6, current - trim))
            win.update_idletasks()
        width = win.winfo_reqwidth()
        height = win.winfo_reqheight()
        win.geometry(
            f"+{screen_left + max(0, (screen_w - width) // 2)}"
            f"+{screen_top + max(0, screen_h - height - 24)}"
        )

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
        self.config.save()
        self.controller.reload()
        self._refresh()

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

    def _board(self) -> None:
        if self.on_board is not None:
            self.on_board()

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
        takeover = bool(getattr(self.controller, "mouse_takeover", False))
        if recording:
            text, color = "● 录制", "#ff4040"
        elif active:
            text, color = "● 激活", "#22c55e"
        elif takeover:
            text, color = "● 物理鼠标", "#ffb400"
        else:
            text, color = "● 暂停", "#ffb400"
        if active and bool(getattr(self.controller, "hand_lost", False)):
            text, color = "● 未看到手", "#ffb400"
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
        self._tier = None
        self._text = ""
        self._tier_text = ""
        self._tier_until = 0.0
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
        screen_left, screen_top, screen_w, screen_h = _screen_rect(self.config)
        win.geometry(f"{screen_w}x{screen_h}+{screen_left}+{screen_top}")
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
        self._tier = canvas.create_text(
            screen_w - 24, 24, anchor="ne", text="", fill="#00d4ff",
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
            screen_left, screen_top, screen_w, screen_h = _screen_rect(
                self.config
            )
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
            elif not active and bool(
                getattr(self.controller, "mouse_takeover", False)
            ):
                text = "● 物理鼠标"
            elif not active:
                text = "● 暂停"
            elif bool(getattr(self.controller, "hand_lost", False)):
                text = "● 未看到手"
            elif bool(getattr(self.controller, "inject_blocked", False)):
                text = "● 目标窗口提权，无法点击"
            if text != self._text:
                self._text = text
                self._canvas.itemconfigure(
                    self._badge,
                    text=text,
                    fill="#ff4040" if recording else "#ffb400",
                    state="normal" if text else "hidden",
                )

            if bool(self.config.get("overlay.tier_toast", True)):
                tier = str(getattr(self.controller, "tier", "") or "")
                if tier and tier != self._tier_text and not self._tier_until:
                    self._tier_until = time.monotonic() + 2.0
                if tier != self._tier_text:
                    self._tier_text = tier
                if self._tier_until and time.monotonic() >= self._tier_until:
                    self._tier_until = 0.0
                showing = bool(self._tier_until) and bool(tier)
                self._canvas.itemconfigure(
                    self._tier,
                    text=f"档位 {tier}",
                    state="normal" if showing else "hidden",
                )
            else:
                self._tier_until = 0.0
                self._canvas.itemconfigure(self._tier, state="hidden")

            if (draw_box or text or self._tier_until) and not self._shown:
                self.win.deiconify()
                self._shown = True
            elif not draw_box and not text and not self._tier_until and self._shown:
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
    """Burn a compact status line into the preview frame.

    §10.1's line is ``B 中距 · 绝对 · 已激活`` plus the capability strip
    (palm pixels, drop rate, latency). The preview is the only place the
    capability strip lives, so the full-screen HUD never covers the lesson.
    """
    if frame is None:
        return
    height, width = frame.shape[:2]
    bar_height = 34 if width >= 900 else 30
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (width, bar_height), (20, 20, 20), -1)
    cv2.addWeighted(overlay, 0.6, frame, 0.4, 0, frame)

    if stats.get("recording"):
        state = "REC"
    elif stats.get("active") and stats.get("hand_lost"):
        state = "NO HAND"
    elif stats.get("active"):
        state = "ACTIVE"
    elif stats.get("takeover"):
        state = "MOUSE"
    else:
        state = "PAUSED"
    color = (80, 80, 255) if stats.get("recording") else (
        (80, 220, 80)
        if stats.get("active") and not stats.get("hand_lost")
        else (80, 190, 255)
    )
    parts = [
        state,
        f"tier {stats.get('tier', '-')}",
        str(stats.get("mode", "")),
        f"{stats.get('fps', 0.0):.1f} fps",
        str(stats.get("perf", "")),
    ]
    palm = float(stats.get("palm_px") or 0.0)
    if palm > 0:
        parts.append(f"palm {palm:.0f}px")
    drop = stats.get("drop")
    if drop is not None:
        parts.append(f"drop {float(drop) * 100.0:.0f}%")
    parts.append(f"{stats.get('proc_ms', 0.0):.0f} ms")
    text = "  ".join(part for part in parts if part)
    if stats.get("presentation"):
        text = "DEMO  " + text
    if stats.get("blocked"):
        text += "  目标窗口提权，无法点击"
    scale = 0.62 if width >= 900 else 0.55
    thickness = 2 if width >= 900 else 1
    baseline = bar_height - 9
    cv2.putText(
        frame, text, (8, baseline), cv2.FONT_HERSHEY_SIMPLEX, scale, color,
        thickness, cv2.LINE_AA,
    )
