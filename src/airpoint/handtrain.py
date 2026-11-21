from __future__ import annotations

import time
import tkinter as tk
from tkinter import messagebox, ttk

from .personalize import LABELS, Personalizer, features

STEPS = (
    ("open", "张开五指，手掌正对镜头"),
    ("pinch", "拇指和食指捏在一起"),
    ("fist", "握拳"),
    ("point", "只伸出食指"),
)
STEP_SECONDS = 40.0
SAMPLE_EVERY = 0.2
PREFERRED = 30


def open_trainer(parent, bus, controller) -> None:
    win = tk.Toplevel(parent)
    win.title("OpticalInputSvc 手势样本")
    win.geometry("520x300")
    win.attributes("-topmost", True)

    label_var = tk.StringVar(value="")
    hint_var = tk.StringVar(value="点开始后，按提示做手势。")
    count_var = tk.StringVar(value="")
    bar = ttk.Progressbar(win, maximum=100.0, length=460)
    bar.pack(padx=20, pady=(16, 6))
    ttk.Label(
        win, textvariable=label_var, font=("Microsoft YaHei UI", 18, "bold")
    ).pack()
    ttk.Label(win, textvariable=hint_var, justify="left", wraplength=460).pack(
        padx=20, pady=(4, 4)
    )
    ttk.Label(win, textvariable=count_var).pack()

    state = {"step": -1, "until": 0.0, "last": 0.0, "done": False}

    def refresh_counts() -> None:
        tally = controller.personalizer.counts()
        parts = [f"{name} {tally.get(name, 0)}" for name in LABELS]
        count_var.set("已存样本：" + "   ".join(parts))

    def stop() -> None:
        state["done"] = True
        bar["value"] = 0.0
        label_var.set("")
        controller.personalizer.save()
        refresh_counts()
        hint_var.set("样本已保存。到设置里打开“个性化手势”即可使用。")

    def start() -> None:
        state["done"] = False
        state["step"] = 0
        state["until"] = time.monotonic() + STEP_SECONDS
        state["last"] = 0.0
        controller.set_active(True)
        poll()

    def advance() -> None:
        state["step"] += 1
        if state["step"] >= len(STEPS):
            stop()
            return
        state["until"] = time.monotonic() + STEP_SECONDS
        state["last"] = 0.0

    def poll() -> None:
        if state["done"] or state["step"] < 0:
            return
        now = time.monotonic()
        remaining = state["until"] - now
        bar["value"] = max(0.0, min(100.0, 100.0 * (1 - remaining / STEP_SECONDS)))
        if remaining <= 0:
            advance()
            if state["done"]:
                return
        name, hint = STEPS[state["step"]]
        label_var.set(f"第 {state['step'] + 1}/{len(STEPS)} 步：{name}")
        hint_var.set(hint)

        if now - state["last"] >= SAMPLE_EVERY:
            state["last"] = now
            _frame, stats = bus.latest()
            if stats.get("palm_ok"):
                vector = features(stats.get("landmarks"))
                if vector and controller.personalizer.add(name, vector):
                    refresh_counts()
        try:
            win.after(100, poll)
        except tk.TclError:
            pass

    buttons = ttk.Frame(win)
    buttons.pack(pady=10)
    ttk.Button(buttons, text="开始（约 3 分钟）", command=start).pack(
        side="left", padx=4
    )
    ttk.Button(buttons, text="提前结束", command=stop).pack(
        side="left", padx=4
    )

    def clear() -> None:
        if not messagebox.askyesno(
            "OpticalInputSvc", "清空所有手势样本？", parent=win
        ):
            return
        controller.personalizer.clear()
        refresh_counts()

    ttk.Button(buttons, text="清空样本", command=clear).pack(
        side="left", padx=4
    )
    ttk.Button(buttons, text="关闭", command=win.destroy).pack(
        side="left", padx=4
    )

    refresh_counts()
    was_enabled = bus.enabled
    bus.set_enabled(True)

    def close() -> None:
        stop()
        if not was_enabled:
            bus.set_enabled(False)
        win.destroy()

    win.protocol("WM_DELETE_WINDOW", close)