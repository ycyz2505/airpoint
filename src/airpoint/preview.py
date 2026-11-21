from __future__ import annotations

import threading

import cv2
import tkinter as tk
from PIL import Image, ImageTk
from tkinter import messagebox, ttk

from .calibrate import run_calibration
from .config import DEFAULTS


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


def open_preview(root, bus: PreviewBus, controller, config) -> None:
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
    win.geometry("780x900")
    win.minsize(700, 760)

    canvas = tk.Canvas(win, width=640, height=360, highlightthickness=0)
    canvas.pack(padx=8, pady=(8, 0))

    stats_var = tk.StringVar(value="未激活 / 无画面")
    ttk.Label(win, textvariable=stats_var, justify="left").pack(
        fill="x", padx=10, pady=6
    )

    tune = ttk.LabelFrame(win, text="手感", padding=8)
    tune.pack(fill="x", padx=10, pady=(0, 6))

    sliders = {}

    def add_slider(row, label, key, low, high, step):
        ttk.Label(tune, text=label).grid(
            row=row, column=0, sticky="w", padx=(0, 8), pady=1
        )
        var = tk.DoubleVar(value=float(config.get(key, low)))
        scale = tk.Scale(
            tune,
            from_=low,
            to=high,
            resolution=step,
            orient="horizontal",
            variable=var,
            showvalue=True,
            length=340,
            command=lambda value, k=key: config.set(k, float(value)),
        )
        scale.grid(row=row, column=1, sticky="ew", padx=(0, 8), pady=1)
        tune.columnconfigure(1, weight=1)
        sliders[key] = (var, scale)

    add_slider(0, "增益", "pointer.gain_relative", 0.4, 4.0, 0.1)
    add_slider(1, "平滑（越小越稳）", "pointer.mincutoff", 0.2, 3.0, 0.1)
    add_slider(2, "跟随（越大越快）", "pointer.beta", 0.0, 1.5, 0.05)
    add_slider(3, "预测（ms）", "pointer.prediction_ms", 0, 100, 5)
    add_slider(4, "死区（px）", "pointer.deadzone_px", 0.0, 5.0, 0.1)
    add_slider(5, "单帧最大位移（px）", "pointer.max_step_px", 50, 600, 10)
    add_slider(6, "加速起点", "pointer.accel_v0", 0.2, 2.0, 0.1)
    add_slider(7, "加速上限", "pointer.accel_max", 1.0, 3.0, 0.1)

    accel_var = tk.BooleanVar(
        value=bool(config.get("pointer.accel_enabled", True))
    )

    def toggle_accel() -> None:
        config.set("pointer.accel_enabled", bool(accel_var.get()))

    ttk.Checkbutton(
        tune, text="速度加速", variable=accel_var, command=toggle_accel
    ).grid(row=8, column=0, columnspan=2, sticky="w", pady=(4, 0))

    bar = ttk.Frame(win)
    bar.pack(fill="x", padx=10, pady=(0, 10))
    toggle_button = ttk.Button(bar, text="激活")
    toggle_button.pack(side="left")
    box_button = ttk.Button(bar, text="画框")
    box_button.pack(side="left", padx=6)
    ttk.Button(bar, text="清除框", command=lambda: clear_box()).pack(
        side="left", padx=(0, 6)
    )
    ttk.Button(bar, text="5 点标定", command=lambda: calibrate()).pack(
        side="left", padx=(0, 6)
    )
    ttk.Button(
        bar, text="清除标定", command=lambda: clear_calibration()
    ).pack(side="left", padx=(0, 6))
    ttk.Button(bar, text="保存参数", command=lambda: save()).pack(
        side="left", padx=(0, 6)
    )
    ttk.Button(bar, text="重置手感", command=lambda: reset()).pack(
        side="left", padx=(0, 6)
    )
    ttk.Button(bar, text="关闭", command=lambda: close()).pack(side="right")

    photo = None
    last_seq = -1
    img_w = 0
    img_h = 0
    box_mode = False
    drag_start = None

    def toggle() -> None:
        controller.set_active(not controller.active)
        toggle_button.configure(
            text="暂停" if controller.active else "激活"
        )

    def save() -> None:
        config.save()
        messagebox.showinfo("OpticalInputSvc", "参数已保存。", parent=win)

    def reset() -> None:
        defaults = DEFAULTS.get("pointer", {})
        for key, (var, _scale) in sliders.items():
            short = key.split(".", 1)[1]
            value = float(defaults.get(short, var.get()))
            var.set(value)
            config.set(key, value)
        accel_var.set(bool(defaults.get("accel_enabled", True)))
        config.set("pointer.accel_enabled", bool(accel_var.get()))
        messagebox.showinfo(
            "OpticalInputSvc", "已恢复手感默认值，点保存写入。", parent=win
        )

    def calibrate() -> None:
        if not controller.active:
            messagebox.showinfo(
                "OpticalInputSvc", "请先激活鼠标。", parent=win
            )
            return
        if not config.get("pointer.box"):
            messagebox.showinfo(
                "OpticalInputSvc",
                "请先画框；绝对模式需要一个活动区。",
                parent=win,
            )
            return
        run_calibration(
            win.master,
            bus,
            controller,
            config,
            win.winfo_screenwidth(),
            win.winfo_screenheight(),
        )

    def clear_calibration() -> None:
        config.set("pointer.calibration", [])
        config.set("pointer.calibration_size", [])
        config.save()

    def draw_box() -> None:
        canvas.delete("box")
        box = config.get("pointer.box", [])
        if (
            isinstance(box, list)
            and len(box) == 4
            and img_w > 0
            and img_h > 0
        ):
            x0, y0, x1, y1 = [float(item) for item in box]
            canvas.create_rectangle(
                x0 * img_w,
                y0 * img_h,
                x1 * img_w,
                y1 * img_h,
                outline="#ffd400",
                width=2,
                tags="box",
            )

    def toggle_box() -> None:
        nonlocal box_mode, drag_start
        box_mode = not box_mode
        drag_start = None
        canvas.delete("temp")
        canvas.configure(cursor="crosshair" if box_mode else "")
        box_button.configure(text="取消画框" if box_mode else "画框")

    def clear_box() -> None:
        config.set("pointer.box", [])
        config.save()
        draw_box()

    def on_press(event) -> None:
        nonlocal drag_start
        if not box_mode:
            return
        drag_start = (event.x, event.y)
        canvas.delete("temp")

    def on_drag(event) -> None:
        if not box_mode or drag_start is None:
            return
        canvas.delete("temp")
        canvas.create_rectangle(
            drag_start[0],
            drag_start[1],
            event.x,
            event.y,
            outline="#ff4040",
            width=2,
            tags="temp",
        )

    def on_release(event) -> None:
        nonlocal drag_start, box_mode
        if not box_mode or drag_start is None:
            return
        x0, y0 = drag_start
        x1, y1 = event.x, event.y
        drag_start = None
        canvas.delete("temp")
        if img_w <= 0 or img_h <= 0:
            return
        if abs(x1 - x0) < 20 or abs(y1 - y0) < 20:
            return

        nx0, nx1 = sorted((x0 / img_w, x1 / img_w))
        ny0, ny1 = sorted((y0 / img_h, y1 / img_h))
        nx0 = max(0.0, min(1.0, nx0))
        nx1 = max(0.0, min(1.0, nx1))
        ny0 = max(0.0, min(1.0, ny0))
        ny1 = max(0.0, min(1.0, ny1))
        config.set("pointer.box", [nx0, ny0, nx1, ny1])
        config.save()

        box_mode = False
        canvas.configure(cursor="")
        box_button.configure(text="画框")
        draw_box()

    box_button.configure(command=toggle_box)
    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)

    def close() -> None:
        bus.set_enabled(False)
        win.destroy()

    def format_stats(stats: dict) -> str:
        if not stats:
            return "未激活 / 无画面"
        return (
            f"状态：{'激活' if stats.get('active') else '暂停'}"
            f"{' · 标定中' if stats.get('calibrating') else ''}    "
            f"摄像头：{stats.get('width', 0)}x{stats.get('height', 0)}    "
            f"FPS：{stats.get('fps', 0.0):.1f}    "
            f"手腕：{stats.get('hit', 0.0):.0f}%\n"
            f"姿态：{'就绪' if stats.get('pose') else '未就绪'}    "
            f"手部：{'就绪' if stats.get('hands') else '未就绪'}    "
            f"方案：{stats.get('scheme', 'single')}    "
            f"模式：{stats.get('mode', 'relative')}    "
            f"框内：{'是' if stats.get('inside') else '否'}    "
            f"标定：{'已' if stats.get('calibrated') else '无'}\n"
            f"手：{stats.get('hand', 'right')}    "
            f"增益：{stats.get('gain', 0.0):.2f}    "
            f"手腕置信：{stats.get('wrist_conf', 0.0):.2f}    "
            f"捏合比：{stats.get('pinch_ratio', 0.0):.2f}    "
            f"握拳比：{stats.get('fist_ratio', 0.0):.2f}"
        )

    def poll() -> None:
        nonlocal photo, last_seq, img_w, img_h
        frame, stats = bus.latest()
        if stats:
            stats["active"] = controller.active
        seq = int(stats.get("seq", 0))

        if (
            frame is not None
            and seq != last_seq
            and not (box_mode and drag_start is not None)
        ):
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = Image.fromarray(rgb)
            photo = ImageTk.PhotoImage(image=image)
            img_w, img_h = image.width, image.height
            canvas.delete("all")
            canvas.create_image(0, 0, anchor="nw", image=photo)
            canvas.configure(width=img_w, height=img_h)
            draw_box()
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
