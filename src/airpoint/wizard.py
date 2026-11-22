from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from .autobox import suggest_box
from .survey import _Collector
from .tiers import SPECS, suggest_tier

MEASURE_SECONDS = 10.0
TICK_MS = 100

STEPS = (
    "摄像头",
    "手部可见",
    "坐 / 站",
    "活动区",
    "标定",
    "手势",
    "保存",
)


def open_wizard(root, bus, controller, config, on_done=None) -> None:
    """First-run setup: camera, visibility, seat, box, calibration, gestures."""
    import cv2
    from PIL import Image, ImageTk

    from .calibrate import run_calibration
    from .profiles import ProfileStore, suggest_name

    existing = getattr(root, "_wizard_window", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.lift()
                return
        except tk.TclError:
            pass

    win = tk.Toplevel(root)
    root._wizard_window = win
    win.title("OpticalInputSvc 首次向导")
    win.configure(bg="#0d0d0d")
    win.geometry("1180x760")
    win.minsize(960, 640)

    body = tk.Frame(win, bg="#0d0d0d")
    body.pack(fill="both", expand=True)

    canvas = tk.Canvas(body, bg="#0d0d0d", highlightthickness=0)
    canvas.pack(side="left", fill="both", expand=True, padx=12, pady=12)

    side = tk.Frame(body, bg="#0d0d0d", width=420)
    side.pack(side="right", fill="y", padx=(0, 16), pady=16)
    side.pack_propagate(False)

    step_var = tk.StringVar(value="")
    tk.Label(
        side, textvariable=step_var, bg="#0d0d0d", fg="#f0f0f0",
        font=("Microsoft YaHei UI", 20, "bold"), anchor="w",
    ).pack(fill="x", pady=(0, 4))

    hint_var = tk.StringVar(value="")
    tk.Label(
        side, textvariable=hint_var, bg="#0d0d0d", fg="#9a9a9a",
        font=("Microsoft YaHei UI", 11), wraplength=380,
        justify="left", anchor="w",
    ).pack(fill="x", pady=(0, 10))

    stats_var = tk.StringVar(value="")
    tk.Label(
        side, textvariable=stats_var, bg="#0d0d0d", fg="#00d4ff",
        font=("Consolas", 13), justify="left", anchor="nw",
    ).pack(fill="x")

    result_var = tk.StringVar(value="")
    tk.Label(
        side, textvariable=result_var, bg="#0d0d0d", fg="#ffd400",
        font=("Microsoft YaHei UI", 13, "bold"), wraplength=380,
        justify="left", anchor="w",
    ).pack(fill="x", pady=(10, 8))

    seat = tk.StringVar(value=str(config.get("wizard.seat", "stand")) or "stand")
    seat_box = tk.Frame(side, bg="#0d0d0d")
    for label, value in (("站着上课", "stand"), ("坐着上课", "sit")):
        ttk.Radiobutton(
            seat_box, text=label, value=value, variable=seat,
        ).pack(side="left", padx=(0, 12))

    gesture_var = tk.StringVar(value="")
    tk.Label(
        side, textvariable=gesture_var, bg="#0d0d0d", fg="#7fe38a",
        font=("Microsoft YaHei UI", 12), justify="left", anchor="w",
    ).pack(fill="x", pady=(6, 0))

    buttons = tk.Frame(side, bg="#0d0d0d")
    buttons.pack(fill="x", side="bottom")

    photo = None
    last_seq = -1
    state = {
        "step": 0,
        "measuring": False,
        "measured": False,
        "suggestion": "",
        "seen": {},
        "clicks": 0,
        "last_state": "Idle",
    }
    collector = _Collector()

    def refresh_controls() -> None:
        step = state["step"]
        step_var.set(f"{step + 1}/{len(STEPS)}  {STEPS[step]}")
        hint_var.set({
            0: "先确认摄像头画面正常。看不到画面就去设置里换一个摄像头。",
            1: "走到你平时上课的位置，站好，挥手 10 秒，让系统量一量手有多大。",
            2: "你平时是站着还是坐着上课？这会影响默认档位。",
            3: "系统会按你的人框生成活动区，也可以到预览窗口里手动画框。",
            4: "按 9 点标定，把手放到每个十字下方停稳，直到自动采到。",
            5: "做几次捏合和握拳，看看系统能不能认出来。",
            6: "给这套设置起个名字存下来，下次启动会自动载入。",
        }[step])
        seat_box.pack_forget()
        if step == 2:
            seat_box.pack(fill="x", pady=(4, 0))
        next_button.configure(text="完成" if step == len(STEPS) - 1 else "下一步")
        prev_button.configure(state="normal" if step > 0 else "disabled")
        if step == 1:
            measure_button.pack(fill="x", pady=(0, 6))
        else:
            measure_button.pack_forget()
        if step == 3:
            box_button.pack(fill="x", pady=(0, 6))
        else:
            box_button.pack_forget()
        if step == 4:
            calib_button.pack(fill="x", pady=(0, 6))
        else:
            calib_button.pack_forget()
        if step == 6:
            save_button.pack(fill="x", pady=(0, 6))
        else:
            save_button.pack_forget()
        gesture_var.set(
            "" if step != 5 else gesture_text()
        )

    def gesture_text() -> str:
        names = ("open", "pinch", "fist", "point")
        parts = []
        for name in names:
            count = state["seen"].get(name, 0)
            mark = "✓" if count >= 2 else "·"
            parts.append(f"{mark} {name} {count}")
        return "   ".join(parts) + f"\n点击识别：{state['clicks']}"

    def measure() -> None:
        if state["measuring"]:
            return
        if not controller.active:
            controller.set_active(True)
        collector.reset()
        state["measuring"] = True
        state["measured"] = False
        measure_button.configure(text="测量中…", state="disabled")
        result_var.set("")

    def finish_measure() -> None:
        state["measuring"] = False
        state["measured"] = True
        measure_button.configure(text="重新测量", state="normal")
        summary = collector.summary()
        if summary["samples"] < 20:
            result_var.set("没有采到画面，请确认摄像头和激活状态。")
            return
        tier, reason = suggest_tier(
            summary["shoulder"], summary["palm"], summary["drop"],
            summary["conf"], config,
        )
        state["suggestion"] = tier
        config.set("tier.mode", "manual")
        config.set("tier.lock", tier)
        config.save()
        controller.reload()
        mode, scheme, click, hands = SPECS[tier]
        result_var.set(
            f"建议档位：{tier}\n{reason}\n"
            f"映射：{mode}   点击：{click}   双手：{'是' if hands else '否'}\n"
            f"已先按此档位锁定，后面可以在设置里改。"
        )

    def make_box() -> None:
        _frame, stats = bus.latest()
        body_rect = stats.get("body")
        if not body_rect:
            result_var.set("还没识别到人，请站到镜头前再试。")
            return
        width = float(stats.get("width", 0) or 0)
        height = float(stats.get("height", 0) or 0)
        frame_aspect = width / height if width > 0 and height > 0 else 16 / 9
        screen_aspect = win.winfo_screenwidth() / max(
            1, win.winfo_screenheight()
        )
        box = suggest_box(
            [(body_rect[0], body_rect[1]), (body_rect[2], body_rect[3])],
            frame_aspect=frame_aspect,
            screen_aspect=screen_aspect,
        )
        if box is None:
            result_var.set("人框太小，请靠近一点再试。")
            return
        config.set("pointer.box", box)
        config.set("pointer.mode", "absolute")
        config.save()
        controller.reload()
        result_var.set("活动区已生成并切到绝对模式。")

    def calibrate() -> None:
        if not config.get("pointer.box"):
            result_var.set("请先生成活动区。")
            return
        if not controller.active:
            controller.set_active(True)
        win.attributes("-topmost", False)
        run_calibration(win, bus, controller, config)
        win.attributes("-topmost", True)
        result_var.set("标定已保存。")

    def save_profile() -> None:
        name = str(config.get("wizard.profile", "") or "").strip()
        if not name:
            name = suggest_name(config)
        config.set("wizard.seat", seat.get())
        config.set("wizard.done", True)
        config.set("wizard.profile", name)
        config.save()
        try:
            ProfileStore(config).save(name)
        except OSError as exc:
            result_var.set(f"保存档案失败：{exc}")
            return
        result_var.set(f"已保存档案「{name}」。向导完成。")
        if on_done is not None:
            on_done()

    def go(delta: int) -> None:
        step = state["step"] + delta
        if step < 0:
            return
        if step >= len(STEPS):
            close()
            return
        if delta > 0 and state["step"] == 1 and not state["measured"]:
            if not messagebox.askyesno(
                "OpticalInputSvc",
                "还没有测量手部可见性。继续的话档位只能保持当前值。继续吗？",
                parent=win,
            ):
                return
        state["step"] = step
        refresh_controls()

    def close() -> None:
        preview = getattr(root, "_preview_window", None)
        alive = False
        if preview is not None:
            try:
                alive = bool(preview.winfo_exists())
            except tk.TclError:
                alive = False
        if not alive:
            bus.set_enabled(False)
        win.destroy()

    prev_button = ttk.Button(buttons, text="上一步", command=lambda: go(-1))
    prev_button.pack(fill="x", pady=(0, 6))
    next_button = ttk.Button(buttons, text="下一步", command=lambda: go(1))
    next_button.pack(fill="x", pady=(0, 6))
    ttk.Button(buttons, text="关闭", command=close).pack(fill="x")

    measure_button = ttk.Button(
        side, text="开始测量（10 秒）", command=measure
    )
    box_button = ttk.Button(side, text="生成活动区", command=make_box)
    calib_button = ttk.Button(side, text="开始 9 点标定", command=calibrate)
    save_button = ttk.Button(side, text="保存档案", command=save_profile)

    def poll() -> None:
        nonlocal photo, last_seq
        frame, stats = bus.latest()
        seq = int(stats.get("seq", 0))

        if frame is not None and seq != last_seq:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = Image.fromarray(rgb)
            avail_w = max(240, canvas.winfo_width())
            avail_h = max(180, canvas.winfo_height())
            scale = min(avail_w / image.width, avail_h / image.height)
            image = image.resize(
                (max(1, int(image.width * scale)),
                 max(1, int(image.height * scale))),
                Image.BILINEAR,
            )
            photo = ImageTk.PhotoImage(image=image)
            canvas.delete("all")
            canvas.create_image(
                avail_w // 2, avail_h // 2, anchor="center", image=photo
            )
            last_seq = seq

        if stats:
            step = state["step"]
            if step == 0:
                stats_var.set(
                    f"画面：{stats.get('width', 0)}x{stats.get('height', 0)}\n"
                    f"帧率：{stats.get('fps', 0.0):.1f}\n"
                    f"姿态：{'就绪' if stats.get('pose') else '未就绪'}\n"
                    f"手部：{'就绪' if stats.get('hands') else '未就绪'}"
                )
            elif step == 1:
                if state["measuring"]:
                    collector.add(stats)
                    remaining = MEASURE_SECONDS - collector.elapsed()
                    if remaining <= 0:
                        finish_measure()
                    else:
                        summary = collector.summary()
                        stats_var.set(
                            f"剩余 {remaining:4.1f} s\n"
                            f"肩宽：{summary['shoulder']:.3f}   "
                            f"手掌：{summary['palm']:.0f}px\n"
                            f"丢手：{summary['drop'] * 100:.0f}%   "
                            f"置信：{summary['conf']:.2f}"
                        )
                else:
                    stats_var.set(
                        f"肩宽：{stats.get('shoulder', 0.0):.3f}   "
                        f"手掌：{stats.get('palm_px', 0.0):.0f}px\n"
                        f"丢手：{stats.get('drop', 0.0) * 100:.0f}%"
                    )
            elif step == 3:
                rect = stats.get("body")
                if rect:
                    stats_var.set(
                        f"人框：x {rect[0]:.2f}–{rect[2]:.2f}   "
                        f"y {rect[1]:.2f}–{rect[3]:.2f}\n"
                        f"当前活动区：{config.get('pointer.box') or '无'}"
                    )
                else:
                    stats_var.set(f"还没识别到人\n"
                                  f"当前活动区：{config.get('pointer.box') or '无'}")
            elif step == 5:
                gesture = str(stats.get("gesture", "none"))
                if gesture and gesture != "none":
                    state["seen"][gesture] = state["seen"].get(gesture, 0) + 1
                current = str(stats.get("state", "Idle"))
                if state["last_state"] in ("Pending", "Dragging") and current == "Idle":
                    state["clicks"] += 1
                state["last_state"] = current
                gesture_var.set(gesture_text())
                stats_var.set(
                    f"手势：{gesture}   状态：{current}\n"
                    f"捏合比：{stats.get('pinch_ratio', 0.0):.2f}   "
                    f"握拳比：{stats.get('fist_ratio', 0.0):.2f}"
                )
            else:
                stats_var.set(
                    f"档位：{stats.get('tier', '-')}   "
                    f"模式：{stats.get('mode', '')}\n"
                    f"标定：{'已' if stats.get('calibrated') else '无'}   "
                    f"活动区：{'有' if config.get('pointer.box') else '无'}"
                )

        try:
            win.after(TICK_MS, poll)
        except tk.TclError:
            pass

    win.protocol("WM_DELETE_WINDOW", close)
    refresh_controls()
    bus.set_enabled(True)
    if not controller.active:
        controller.set_active(True)
    win.after(TICK_MS, poll)
    win.lift()
