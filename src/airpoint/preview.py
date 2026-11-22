from __future__ import annotations

import threading

import cv2
import tkinter as tk
from PIL import Image, ImageTk
from pathlib import Path
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


def _target_aspect(config) -> float:
    """Aspect of the display the mapping box is fitted to (§7.1)."""
    from . import inject

    _left, _top, width, height = inject.screen_rect(
        config.get("pointer.screen", "auto")
    )
    return width / max(1, height)


BOX_MIN = 0.04


def resize_box(box, handle: str, x: float, y: float):
    """§8.5: drag one corner of the mapping box to ``(x, y)``.

    ``handle`` is ``nw``/``ne``/``sw``/``se``. The dragged corner moves, the
    opposite one stays put, and the box is kept at least ``BOX_MIN`` wide and
    tall so it can never collapse to a line and leave nothing to map.
    """
    x = max(0.0, min(1.0, float(x)))
    y = max(0.0, min(1.0, float(y)))
    bx0, by0, bx1, by1 = [float(item) for item in box]
    if handle == "nw":
        return [min(x, bx1 - BOX_MIN), min(y, by1 - BOX_MIN), bx1, by1]
    if handle == "ne":
        return [bx0, min(y, by1 - BOX_MIN), max(x, bx0 + BOX_MIN), by1]
    if handle == "sw":
        return [min(x, bx1 - BOX_MIN), by0, bx1, max(y, by0 + BOX_MIN)]
    if handle == "se":
        return [bx0, by0, max(x, bx0 + BOX_MIN), max(y, by0 + BOX_MIN)]
    return list(box)


def move_box(box, dx: float, dy: float):
    """§8.5: slide the whole box, stopping at the frame edge."""
    bx0, by0, bx1, by1 = [float(item) for item in box]
    width = bx1 - bx0
    height = by1 - by0
    dx = max(-bx0, min(1.0 - bx1, float(dx)))
    dy = max(-by0, min(1.0 - by1, float(dy)))
    return [bx0 + dx, by0 + dy, bx0 + dx + width, by0 + dy + height]


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
    win.geometry("780x1010")
    win.minsize(700, 860)

    canvas = tk.Canvas(win, width=640, height=360, highlightthickness=0)
    canvas.pack(padx=8, pady=(8, 0))

    map_frame = ttk.LabelFrame(win, text="框对应屏幕", padding=6)
    map_frame.pack(fill="x", padx=10, pady=(6, 0))
    map_canvas = tk.Canvas(
        map_frame, width=320, height=180, highlightthickness=1,
        bd=0, bg="#101010",
    )
    map_canvas.pack(side="left")
    map_text = tk.StringVar(value="还没有活动区。")
    ttk.Label(
        map_frame, textvariable=map_text, justify="left", wraplength=380
    ).pack(side="left", padx=(10, 0), anchor="n")

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
    track_var = tk.BooleanVar(value=False)
    toggle_button = ttk.Button(bar, text="激活")
    toggle_button.pack(side="left")
    box_button = ttk.Button(bar, text="画框")
    box_button.pack(side="left", padx=6)
    edit_button = ttk.Button(bar, text="调整框")
    edit_button.pack(side="left", padx=(0, 6))
    ttk.Button(bar, text="建议框", command=lambda: suggest()).pack(
        side="left", padx=(0, 6)
    )
    ttk.Checkbutton(bar, text="跟踪框", variable=track_var).pack(
        side="left", padx=(0, 6)
    )
    ttk.Button(bar, text="清除框", command=lambda: clear_box()).pack(
        side="left", padx=(0, 6)
    )
    ttk.Button(bar, text="9 点标定", command=lambda: calibrate()).pack(
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
    ttk.Button(
        bar, text="导出诊断", command=lambda: export_diag()
    ).pack(side="left", padx=(0, 6))
    ttk.Button(bar, text="勘测模式", command=lambda: survey()).pack(
        side="left", padx=(0, 6)
    )
    ttk.Button(bar, text="首次向导", command=lambda: wizard()).pack(
        side="left", padx=(0, 6)
    )
    ttk.Button(bar, text="关闭", command=lambda: close()).pack(side="right")

    def draw_track(stats) -> None:
        canvas.delete("track")
        if not track_var.get():
            return
        bounds = stats.get("hand_bounds")
        if not bounds or img_w <= 0 or img_h <= 0:
            return
        canvas.create_rectangle(
            bounds[0] * img_w,
            bounds[1] * img_h,
            bounds[2] * img_w,
            bounds[3] * img_h,
            outline="#00d4ff",
            width=2,
            dash=(4, 2),
            tags="track",
        )

    photo = None
    last_seq = -1
    img_w = 0
    img_h = 0
    box_mode = False
    drag_start = None
    edit_mode = False
    edit_handle = None
    edit_origin = None
    map_sig = None

    def current_box():
        box = config.get("pointer.box", [])
        if isinstance(box, list) and len(box) == 4:
            try:
                return [float(item) for item in box]
            except (TypeError, ValueError):
                return []
        return []

    def draw_map() -> None:
        """§8.5: show which part of the screen the box covers."""
        from . import inject

        nonlocal map_sig
        map_sig = tuple(current_box())
        map_canvas.delete("all")
        box = current_box()
        if not box:
            map_text.set("还没有活动区。按「建议框」或「画框」生成一个。")
            return
        _left, _top, screen_w, screen_h = inject.screen_rect(
            config.get("pointer.screen", "auto")
        )
        aspect = screen_w / max(1, screen_h)
        width = 320
        height = max(1, int(round(width / aspect)))
        if height > 180:
            height = 180
            width = max(1, int(round(height * aspect)))
        map_canvas.configure(width=width, height=height)
        map_canvas.create_rectangle(
            0, 0, width, height, outline="#3a3a3a", fill="#151515"
        )
        x0, y0, x1, y1 = box
        map_canvas.create_rectangle(
            x0 * width, y0 * height, x1 * width, y1 * height,
            outline="#ffd400", width=2, fill="#2a2410",
        )
        map_text.set(
            f"活动区占屏幕 {abs(x1 - x0) * 100:.0f}% × "
            f"{abs(y1 - y0) * 100:.0f}%。\n"
            f"目标屏 {screen_w}×{screen_h}。\n"
            "活动区越小，绝对模式的手感越灵敏；\n"
            "画得太大，够到边角就要走很远。"
        )

    def hit_handle(x, y):
        """Which part of the box the pointer grabbed: a corner or the middle."""
        box = current_box()
        if not box or img_w <= 0 or img_h <= 0:
            return None
        bx0, by0, bx1, by1 = box
        corners = {
            "nw": (bx0, by0),
            "ne": (bx1, by0),
            "sw": (bx0, by1),
            "se": (bx1, by1),
        }
        reach_x = 14.0 / img_w
        reach_y = 14.0 / img_h
        for name, (cx, cy) in corners.items():
            if abs(x - cx) <= reach_x and abs(y - cy) <= reach_y:
                return name
        if bx0 <= x <= bx1 and by0 <= y <= by1:
            return "move"
        return None

    def apply_edit(x, y) -> None:
        nonlocal edit_origin
        box = current_box()
        if not box or edit_origin is None:
            return
        x = max(0.0, min(1.0, x))
        y = max(0.0, min(1.0, y))
        if edit_handle == "move":
            box = move_box(box, x - edit_origin[0], y - edit_origin[1])
        else:
            box = resize_box(box, edit_handle, x, y)
        config.set("pointer.box", [round(item, 4) for item in box])
        edit_origin = (x, y)
        canvas.delete("box")
        draw_box()
        draw_map()

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

    def export_diag() -> None:
        try:
            target = controller.export_diagnostics()
        except OSError as exc:
            messagebox.showerror(
                "OpticalInputSvc", f"导出失败：{exc}", parent=win
            )
            return
        messagebox.showinfo(
            "OpticalInputSvc", f"已导出最近 30 秒数据：\n{target}", parent=win
        )

    def survey() -> None:
        from .survey import open_survey

        open_survey(win.master, bus, controller, config)

    def wizard() -> None:
        from .wizard import open_wizard

        open_wizard(win.master, bus, controller, config)

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
            if edit_mode:
                from .controller import _tracking_zone

                zx0, zy0, zx1, zy1 = _tracking_zone(box)
                canvas.create_rectangle(
                    zx0 * img_w, zy0 * img_h, zx1 * img_w, zy1 * img_h,
                    outline="#5a5a5a", width=1, dash=(3, 3), tags="box",
                )
                for cx, cy in (
                    (x0, y0), (x1, y0), (x0, y1), (x1, y1)
                ):
                    canvas.create_rectangle(
                        cx * img_w - 6, cy * img_h - 6,
                        cx * img_w + 6, cy * img_h + 6,
                        outline="#ffd400", fill="#ffd400", tags="box",
                    )

    def set_modes(edit: bool, draw: bool) -> None:
        """§8.5: 画框 and 调整框 are mutually exclusive."""
        nonlocal edit_mode, box_mode, drag_start, edit_handle, edit_origin
        edit_mode = edit
        box_mode = draw
        if not edit:
            edit_handle = None
            edit_origin = None
        if not draw:
            drag_start = None
            canvas.delete("temp")
        canvas.configure(
            cursor="crosshair" if draw else ("fleur" if edit else "")
        )
        box_button.configure(text="取消画框" if draw else "画框")
        edit_button.configure(text="完成调整" if edit else "调整框")
        draw_box()
        draw_map()

    def toggle_box() -> None:
        set_modes(False, not box_mode)

    def clear_box() -> None:
        config.set("pointer.box", [])
        config.save()
        draw_box()
        draw_map()

    def suggest() -> None:
        from .autobox import suggest_box

        _frame, stats = bus.latest()
        body = stats.get("body")
        if not body:
            messagebox.showinfo(
                "OpticalInputSvc",
                "还没有识别到人。请站到镜头前、全身入画，等预览里出现骨架后再试。",
                parent=win,
            )
            return
        width = float(stats.get("width", 0) or 0)
        height = float(stats.get("height", 0) or 0)
        frame_aspect = width / height if width > 0 and height > 0 else 16 / 9
        screen_aspect = _target_aspect(config)
        box = suggest_box(
            [(body[0], body[1]), (body[2], body[3])],
            frame_aspect=frame_aspect,
            screen_aspect=screen_aspect,
        )
        if box is None:
            messagebox.showinfo(
                "OpticalInputSvc", "人框太小，请靠近一点再试。", parent=win
            )
            return
        config.set("pointer.box", box)
        config.set("pointer.mode", "absolute")
        config.save()
        draw_box()
        draw_map()
        messagebox.showinfo(
            "OpticalInputSvc",
            "已按人框生成活动区，并切到绝对模式。\n"
            "站好再按一次「建议框」可以重新贴合；也可以手动画框微调。",
            parent=win,
        )

    def toggle_edit() -> None:
        if not edit_mode and not current_box():
            messagebox.showinfo(
                "OpticalInputSvc",
                "还没有活动区。先按「建议框」或「画框」。",
                parent=win,
            )
            return
        set_modes(not edit_mode, False)

    def on_press(event) -> None:
        nonlocal drag_start, edit_handle, edit_origin
        if edit_mode:
            if img_w <= 0 or img_h <= 0:
                return
            handle = hit_handle(event.x / img_w, event.y / img_h)
            if handle is None:
                return
            edit_handle = handle
            edit_origin = (event.x / img_w, event.y / img_h)
            return
        if not box_mode:
            return
        drag_start = (event.x, event.y)
        canvas.delete("temp")

    def on_drag(event) -> None:
        if edit_mode:
            if edit_handle is not None and img_w > 0 and img_h > 0:
                apply_edit(event.x / img_w, event.y / img_h)
            return
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

    def on_hover(event) -> None:
        """Cursor feedback so the handles are discoverable (§8.5)."""
        if not edit_mode or img_w <= 0 or img_h <= 0:
            return
        handle = hit_handle(event.x / img_w, event.y / img_h)
        if handle == "move":
            canvas.configure(cursor="fleur")
        elif handle is not None:
            canvas.configure(cursor="crosshair")
        else:
            canvas.configure(cursor="arrow")

    def on_release(event) -> None:
        nonlocal drag_start, box_mode, edit_handle, edit_origin
        if edit_mode:
            if edit_handle is not None:
                config.save()
            edit_handle = None
            edit_origin = None
            return
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
        draw_map()

    box_button.configure(command=toggle_box)
    edit_button.configure(command=toggle_edit)
    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    canvas.bind("<Motion>", on_hover)

    def close() -> None:
        survey_win = getattr(root, "_survey_window", None)
        alive = False
        if survey_win is not None:
            try:
                alive = bool(survey_win.winfo_exists())
            except tk.TclError:
                alive = False
        if not alive:
            bus.set_enabled(False)
        win.destroy()

    def format_stats(stats: dict) -> str:
        if not stats:
            return "未激活 / 无画面"
        status = "暂停"
        if stats.get("recording"):
            status = "录制中"
        elif stats.get("active"):
            status = "激活"
        record = ""
        path = stats.get("record_path")
        if path:
            record = f"    {Path(path).name}"
        return (
            f"状态：{status}"
            f"{' · 标定中' if stats.get('calibrating') else ''}"
            f"{record}    "
            f"摄像头：{stats.get('width', 0)}x{stats.get('height', 0)}    "
            f"FPS：{stats.get('fps', 0.0):.1f}    "
            f"手腕：{stats.get('hit', 0.0):.0f}%\n"
            f"姿态：{'就绪' if stats.get('pose') else '未就绪'}    "
            f"手部：{'就绪' if stats.get('hands') else '未就绪'}    "
            f"方案：{stats.get('scheme', 'single')}    "
            f"模式：{stats.get('mode', 'relative')}    "
            f"框内：{'是' if stats.get('inside') else '否'}    "
            f"标定：{'已' if stats.get('calibrated') else '无'}\n"
            f"档位：{stats.get('tier', 'C')}"
            f"（距离 {stats.get('tier_band', 'C')} / "
            f"手掌 {stats.get('palm_px', 0.0):.0f}px / "
            f"丢手 {stats.get('drop', 0.0) * 100:.0f}%）    "
            f"手部：{'开' if stats.get('hands_on') else '关'}    "
            f"自动：{'是' if stats.get('tier_applied') else '否'}    "
            f"能力：{stats.get('capability', 0)}\n"
            f"手：{stats.get('hand', 'right')}    "
            f"增益：{stats.get('gain', 0.0):.2f}    "
            f"手腕置信：{stats.get('wrist_conf', 0.0):.2f}    "
            f"捏合比：{stats.get('pinch_ratio', 0.0):.2f}    "
            f"握拳比：{stats.get('fist_ratio', 0.0):.2f}\n"
            f"性能：{stats.get('perf', 'L0 全质量')}    "
            f"CPU：{stats.get('cpu', 0.0):.1f}%    "
            f"内存：{stats.get('rss', 0.0):.0f} MB    "
            f"单帧：{stats.get('proc_ms', 0.0):.0f} ms    "
            f"书写：{'是' if stats.get('writing') else '否'}    "
            f"演示：{'是' if stats.get('presentation') else '否'}"
        )

    def poll() -> None:
        nonlocal photo, last_seq, img_w, img_h, map_sig
        frame, stats = bus.latest()
        if stats:
            stats["active"] = controller.active
        seq = int(stats.get("seq", 0))

        sig = tuple(current_box())
        if sig != map_sig:
            map_sig = sig
            draw_map()

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

        draw_track(stats)

        stats_var.set(format_stats(stats))
        recording = bool(stats.get("recording"))
        toggle_button.configure(
            text="暂停" if controller.active else "激活",
            state="disabled" if recording else "normal",
        )
        try:
            win.after(80, poll)
        except tk.TclError:
            pass

    win.protocol("WM_DELETE_WINDOW", close)
    bus.set_enabled(True)
    draw_map()
    win.after(80, poll)
    win.lift()
