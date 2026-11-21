from __future__ import annotations

import statistics
import time
import tkinter as tk
from tkinter import ttk

from .tiers import SPECS, suggest_tier

TEST_SECONDS = 30.0
SAMPLE_MS = 100


class _Collector:
    def __init__(self):
        self.palm = []
        self.drop = []
        self.conf = []
        self.shoulder = []
        self.started = 0.0

    def reset(self) -> None:
        self.palm.clear()
        self.drop.clear()
        self.conf.clear()
        self.shoulder.clear()
        self.started = time.time()

    def add(self, stats: dict) -> None:
        if stats.get("palm_ok") and stats.get("palm_px", 0) > 0:
            self.palm.append(float(stats["palm_px"]))
        self.drop.append(float(stats.get("drop", 0.0)))
        if stats.get("wrist_conf", 0) > 0:
            self.conf.append(float(stats["wrist_conf"]))
        if stats.get("shoulder", 0) > 0:
            self.shoulder.append(float(stats["shoulder"]))

    def elapsed(self) -> float:
        return time.time() - self.started

    def summary(self) -> dict:
        return {
            "palm": statistics.fmean(self.palm) if self.palm else 0.0,
            "drop": statistics.fmean(self.drop) if self.drop else 0.0,
            "conf": statistics.fmean(self.conf) if self.conf else 0.0,
            "shoulder": (
                statistics.fmean(self.shoulder) if self.shoulder else 0.0
            ),
            "samples": len(self.drop),
        }


def open_survey(root, bus, controller, config) -> None:
    import cv2
    from PIL import Image, ImageTk

    existing = getattr(root, "_survey_window", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.lift()
                return
        except tk.TclError:
            pass

    win = tk.Toplevel(root)
    root._survey_window = win
    win.title("OpticalInputSvc 勘测")
    win.attributes("-topmost", True)
    win.configure(bg="#0d0d0d")
    try:
        win.state("zoomed")
    except tk.TclError:
        win.geometry("1280x800")

    canvas = tk.Canvas(win, bg="#0d0d0d", highlightthickness=0)
    canvas.pack(side="left", fill="both", expand=True, padx=12, pady=12)

    side = tk.Frame(win, bg="#0d0d0d", width=420)
    side.pack(side="right", fill="y", padx=(0, 16), pady=16)
    side.pack_propagate(False)

    title = tk.Label(
        side, text="勘测", bg="#0d0d0d", fg="#f0f0f0",
        font=("Microsoft YaHei UI", 22, "bold"), anchor="w",
    )
    title.pack(fill="x", pady=(0, 4))

    hint = tk.Label(
        side,
        text="走到你平时上课的位置，站好，像平时一样挥手 30 秒。",
        bg="#0d0d0d", fg="#9a9a9a", font=("Microsoft YaHei UI", 11),
        wraplength=380, justify="left", anchor="w",
    )
    hint.pack(fill="x", pady=(0, 12))

    stats_var = tk.StringVar(value="等待开始")
    stats_label = tk.Label(
        side, textvariable=stats_var, bg="#0d0d0d", fg="#00d4ff",
        font=("Consolas", 14), justify="left", anchor="nw",
    )
    stats_label.pack(fill="x")

    result_var = tk.StringVar(value="")
    result_label = tk.Label(
        side, textvariable=result_var, bg="#0d0d0d", fg="#ffd400",
        font=("Microsoft YaHei UI", 15, "bold"), wraplength=380,
        justify="left", anchor="w",
    )
    result_label.pack(fill="x", pady=(12, 8))

    buttons = tk.Frame(side, bg="#0d0d0d")
    buttons.pack(fill="x", side="bottom")

    collector = _Collector()
    photo = None
    last_seq = -1
    state = {"running": False, "suggestion": "", "done": False}

    def start_test() -> None:
        if state["running"]:
            return
        if not controller.active:
            controller.set_active(True)
        state["running"] = True
        state["done"] = False
        state["suggestion"] = ""
        result_var.set("")
        collector.reset()
        start_button.configure(text="测试中…", state="disabled")
        apply_button.configure(state="disabled")

    def finish_test() -> None:
        state["running"] = False
        state["done"] = True
        start_button.configure(text="重新测试", state="normal")
        summary = collector.summary()
        if summary["samples"] < 30:
            result_var.set("没有采到画面，请确认摄像头与激活状态。")
            return
        tier, reason = suggest_tier(
            summary["shoulder"], summary["palm"], summary["drop"],
            summary["conf"], config,
        )
        state["suggestion"] = tier
        mode, scheme, click, hands = SPECS[tier]
        result_var.set(
            f"建议档位：{tier}\n{reason}\n"
            f"映射：{mode}   手方案：{scheme}   点击：{click}   "
            f"双手：{'是' if hands else '否'}"
        )
        apply_button.configure(state="normal")

    def apply_suggestion() -> None:
        tier = state["suggestion"]
        if not tier:
            return
        config.set("tier.mode", "manual")
        config.set("tier.lock", tier)
        config.save()
        controller.reload()
        result_var.set(
            f"已锁定 {tier} 档。\n在设置 → 档位里可改回自动。"
        )
        apply_button.configure(state="disabled")

    start_button = ttk.Button(buttons, text="开始测试（30 秒）",
                              command=start_test)
    start_button.pack(fill="x", pady=(0, 6))
    apply_button = ttk.Button(buttons, text="锁定建议档位",
                              command=apply_suggestion, state="disabled")
    apply_button.pack(fill="x", pady=(0, 6))
    ttk.Button(buttons, text="关闭", command=lambda: close()).pack(fill="x")

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

    def poll() -> None:
        nonlocal photo, last_seq
        frame, stats = bus.latest()
        seq = int(stats.get("seq", 0))

        if frame is not None and seq != last_seq:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = Image.fromarray(rgb)
            avail_w = max(200, canvas.winfo_width())
            avail_h = max(150, canvas.winfo_height())
            scale = min(avail_w / image.width, avail_h / image.height)
            target = (
                max(1, int(image.width * scale)),
                max(1, int(image.height * scale)),
            )
            image = image.resize(target, Image.BILINEAR)
            photo = ImageTk.PhotoImage(image=image)
            canvas.delete("all")
            canvas.create_image(
                avail_w // 2, avail_h // 2, anchor="center", image=photo
            )
            last_seq = seq

        if stats:
            if state["running"]:
                collector.add(stats)
                remaining = TEST_SECONDS - collector.elapsed()
                if remaining <= 0:
                    finish_test()
                else:
                    summary = collector.summary()
                    stats_var.set(
                        f"剩余 {remaining:4.1f} s\n"
                        f"姿态：{'就绪' if stats.get('pose') else '未就绪'}"
                        f"   手部：{'就绪' if stats.get('hands') else '未就绪'}\n"
                        f"肩宽：{summary['shoulder']:.3f}   "
                        f"手掌：{summary['palm']:.0f}px\n"
                        f"丢手：{summary['drop'] * 100:.0f}%   "
                        f"手腕置信：{summary['conf']:.2f}\n"
                        f"当前档位：{stats.get('tier', '-')}   "
                        f"模式：{stats.get('mode', '')}"
                    )
            elif not state["done"]:
                stats_var.set(
                    f"肩宽：{stats.get('shoulder', 0.0):.3f}   "
                    f"手掌：{stats.get('palm_px', 0.0):.0f}px\n"
                    f"丢手：{stats.get('drop', 0.0) * 100:.0f}%   "
                    f"手腕置信：{stats.get('wrist_conf', 0.0):.2f}\n"
                    f"当前档位：{stats.get('tier', '-')}   "
                    f"模式：{stats.get('mode', '')}"
                )

        try:
            win.after(SAMPLE_MS, poll)
        except tk.TclError:
            pass

    win.protocol("WM_DELETE_WINDOW", close)
    bus.set_enabled(True)
    win.after(SAMPLE_MS, poll)
    win.lift()
