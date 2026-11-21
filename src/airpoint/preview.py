from __future__ import annotations

import threading

import cv2
import tkinter as tk
from PIL import Image, ImageTk
from tkinter import ttk


class PreviewBus:
    def __init__(self):
        self._lock = threading.Lock()
        self._frame = None
        self._stats = {}
        self._enabled = False
        self._seq = 0

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, value: bool) -> None:
        self._enabled = bool(value)

    def publish(self, frame, stats: dict) -> None:
        if not self._enabled:
            return
        with self._lock:
            self._seq += 1
            self._frame = frame
            data = dict(stats)
            data["seq"] = self._seq
            self._stats = data

    def latest(self):
        with self._lock:
            return self._frame, dict(self._stats)


def open_preview(root, bus: PreviewBus, controller) -> None:
    existing = getattr(root, "_preview_window", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.lift()
                return
        except tk.TclError:
            pass

    win = tk.Toplevel(root)
    root._preview_window = win
    win.title("OpticalInputSvc 预览诊断")
    win.geometry("720x600")
    win.minsize(640, 520)

    image_label = ttk.Label(win, anchor="center")
    image_label.pack(fill="both", expand=True, padx=8, pady=(8, 0))

    stats_var = tk.StringVar(value="未激活 / 无画面")
    ttk.Label(win, textvariable=stats_var, justify="left").pack(
        fill="x", padx=10, pady=6
    )

    bar = ttk.Frame(win)
    bar.pack(fill="x", padx=10, pady=(0, 10))
    toggle_button = ttk.Button(bar, text="激活")
    toggle_button.pack(side="left")
    ttk.Button(bar, text="关闭", command=lambda: close()).pack(side="right")

    photo = None
    last_seq = -1

    def toggle() -> None:
        controller.set_active(not controller.active)
        toggle_button.configure(
            text="暂停" if controller.active else "激活"
        )

    toggle_button.configure(command=toggle)

    def close() -> None:
        bus.set_enabled(False)
        win.destroy()

    def format_stats(stats: dict) -> str:
        if not stats:
            return "未激活 / 无画面"
        return (
            f"状态：{'激活' if stats.get('active') else '暂停'}    "
            f"摄像头：{stats.get('width', 0)}x{stats.get('height', 0)}    "
            f"FPS：{stats.get('fps', 0.0):.1f}    "
            f"手腕：{stats.get('hit', 0.0):.0f}%\n"
            f"姿态：{'就绪' if stats.get('pose') else '未就绪'}    "
            f"手：{stats.get('hand', 'right')}    "
            f"增益：{stats.get('gain', 0.0):.2f}    "
            f"手腕置信：{stats.get('wrist_conf', 0.0):.2f}"
        )

    def poll() -> None:
        nonlocal photo, last_seq
        frame, stats = bus.latest()
        if stats:
            stats["active"] = controller.active
        seq = int(stats.get("seq", 0))
        if frame is not None and seq != last_seq:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            photo = ImageTk.PhotoImage(image=Image.fromarray(rgb))
            image_label.configure(image=photo)
            last_seq = seq
        stats_var.set(format_stats(stats))
        toggle_button.configure(
            text="暂停" if controller.active else "激活"
        )
        try:
            win.after(80, poll)
        except tk.TclError:
            pass

    win.protocol("WM_DELETE_WINDOW", close)
    bus.set_enabled(True)
    win.after(80, poll)
    win.lift()
