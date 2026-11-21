from __future__ import annotations

import time
import tkinter as tk
from tkinter import messagebox, ttk

import cv2
import numpy as np

TARGETS = (
    (0.15, 0.15),
    (0.85, 0.15),
    (0.15, 0.85),
    (0.85, 0.85),
    (0.50, 0.50),
)
HOLD_SECONDS = 1.5
MOVE_LIMIT = 0.004


def run_calibration(parent, bus, controller, config, screen_w, screen_h) -> None:
    win = tk.Toplevel(parent)
    win.overrideredirect(True)
    win.geometry(f"{screen_w}x{screen_h}+0+0")
    win.attributes("-topmost", True)
    win.configure(bg="#101010")
    win.focus_force()

    canvas = tk.Canvas(
        win,
        width=screen_w,
        height=screen_h,
        bg="#101010",
        highlightthickness=0,
    )
    canvas.pack(fill="both", expand=True)

    state = {
        "index": 0,
        "stable_since": None,
        "last": None,
        "samples": [],
        "done": False,
    }
    controller.set_calibrating(True)

    cancel_button = ttk.Button(win, text="取消", command=lambda: finish(False))
    cancel_button.place(relx=0.02, rely=0.96, anchor="sw")
    win.bind("<Escape>", lambda event: finish(False))

    try:
        win.grab_set()
    except tk.TclError:
        pass

    def finish(save: bool) -> None:
        if state["done"]:
            return
        state["done"] = True
        controller.set_calibrating(False)
        try:
            win.grab_release()
        except tk.TclError:
            pass
        win.destroy()
        if save:
            messagebox.showinfo(
                "OpticalInputSvc", "5 点标定已保存。", parent=parent
            )

    def fit() -> bool:
        samples = state["samples"]
        if len(samples) != len(TARGETS):
            return False
        src = np.array(
            [[sample[0], sample[1]] for sample in samples],
            dtype=np.float64,
        )
        dst = np.array(
            [[sample[2], sample[3]] for sample in samples],
            dtype=np.float64,
        )
        matrix, _ = cv2.findHomography(src, dst, 0)
        if matrix is None:
            affine, _ = cv2.estimateAffine2D(src, dst)
            if affine is None:
                return False
            matrix = np.vstack([affine, [0.0, 0.0, 1.0]])
        config.set(
            "pointer.calibration",
            [float(value) for value in matrix.reshape(-1)],
        )
        config.set(
            "pointer.calibration_size", [int(screen_w), int(screen_h)]
        )
        config.save()
        return True

    def record(index: int, x: float, y: float) -> None:
        tx, ty = TARGETS[index]
        state["samples"].append(
            (x, y, tx * screen_w, ty * screen_h)
        )
        state["index"] += 1
        state["stable_since"] = None
        state["last"] = None
        if state["index"] >= len(TARGETS):
            if fit():
                finish(True)
            else:
                messagebox.showerror(
                    "OpticalInputSvc", "标定拟合失败，请重试。", parent=parent
                )
                finish(False)

    def poll() -> None:
        if state["done"]:
            return

        frame, stats = bus.latest()
        now = time.time()
        index = state["index"]
        if index >= len(TARGETS):
            return

        tx, ty = TARGETS[index]
        cx, cy = tx * screen_w, ty * screen_h
        palm_ok = bool(stats.get("palm_ok"))
        x = float(stats.get("palm_x", 0.0))
        y = float(stats.get("palm_y", 0.0))

        progress = 0.0
        if palm_ok:
            last = state["last"]
            if last is None:
                state["last"] = (x, y)
                state["stable_since"] = now
            else:
                moved = abs(x - last[0]) + abs(y - last[1])
                if moved > MOVE_LIMIT:
                    state["stable_since"] = now
                state["last"] = (x, y)
            if state["stable_since"] is not None:
                progress = min(
                    1.0, (now - state["stable_since"]) / HOLD_SECONDS
                )
                if progress >= 1.0:
                    record(index, x, y)
                    if not state["done"]:
                        win.after(50, poll)
                    return
        else:
            state["stable_since"] = None
            state["last"] = None

        canvas.delete("all")
        canvas.create_text(
            screen_w / 2,
            60,
            text=(
                "把手放到你自然指向目标的位置，保持不动"
                f"  ({index + 1}/5)"
            ),
            fill="#f0f0f0",
            font=("Segoe UI", 26),
        )
        canvas.create_text(
            screen_w / 2,
            108,
            text="画框要足够大；丢手会重置进度；按 Esc 取消",
            fill="#a0a0a0",
            font=("Segoe UI", 16),
        )

        radius = 90
        canvas.create_oval(
            cx - radius,
            cy - radius,
            cx + radius,
            cy + radius,
            outline="#404040",
            width=4,
        )
        canvas.create_line(cx - 30, cy, cx + 30, cy, fill="#ffd400", width=3)
        canvas.create_line(cx, cy - 30, cx, cy + 30, fill="#ffd400", width=3)
        if progress > 0:
            canvas.create_arc(
                cx - radius,
                cy - radius,
                cx + radius,
                cy + radius,
                start=90,
                extent=-360 * progress,
                style="arc",
                outline="#00c8ff",
                width=8,
            )
        canvas.create_text(
            cx,
            cy + radius + 40,
            text=f"{int(progress * 100)}%",
            fill="#00c8ff",
            font=("Segoe UI", 20),
        )

        try:
            win.after(50, poll)
        except tk.TclError:
            pass

    win.after(100, poll)
