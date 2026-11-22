from __future__ import annotations

import math
import threading
import time
import tkinter as tk
from tkinter import ttk

from . import inject

# §10.5 v2: a separate writing board. The hand draws big on a 4x enlarged
# board, and on commit the strokes are shrunk and replayed into whatever app
# was underneath. Drawing big is what makes the stroke smooth at six metres;
# shrinking it back is what makes it the right size in the whiteboard.


def board_rect(
    screen_left: float,
    screen_top: float,
    screen_w: int,
    screen_h: int,
    frac: float = 0.8,
) -> tuple[int, int, int, int]:
    """A centred square board over the target display, in screen pixels."""
    frac = max(0.3, min(0.95, float(frac)))
    side = max(160, int(round(min(int(screen_w), int(screen_h)) * frac)))
    left = int(screen_left) + (int(screen_w) - side) // 2
    top = int(screen_top) + (int(screen_h) - side) // 2
    return left, top, side, side


def source_rect(
    rect: tuple[int, int, int, int], gain: float
) -> tuple[int, int, int]:
    """The small app region the board stands in for, at the board's centre."""
    left, top, w, h = rect
    gain = max(1.0, float(gain))
    side = max(8, int(round(min(w, h) / gain)))
    cx = left + w / 2.0
    cy = top + h / 2.0
    return int(round(cx - side / 2.0)), int(round(cy - side / 2.0)), side


def to_screen(points, rect: tuple[int, int, int, int], gain: float):
    """Board-space points, shrunk by ``gain`` around the board's centre."""
    gain = max(1.0, float(gain))
    left, top, w, h = rect
    cx = left + w / 2.0
    cy = top + h / 2.0
    return [
        (cx + (float(x) - cx) / gain, cy + (float(y) - cy) / gain)
        for x, y in points
    ]


def resample(points, step: float = 2.0):
    """Fill a polyline so consecutive points are at most ``step`` apart.

    A synthesized drag only draws the segments the app is told to draw, so
    the shrunk stroke has to be dense or the whiteboard's pen will cut corners
    on a fast flick.
    """
    step = max(0.5, float(step))
    if not points:
        return []
    out = [(float(points[0][0]), float(points[0][1]))]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        dist = math.hypot(x1 - x0, y1 - y0)
        n = max(1, int(math.ceil(dist / step)))
        for i in range(1, n + 1):
            t = i / n
            out.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
    return out


def total_length(points) -> float:
    return sum(
        math.hypot(x1 - x0, y1 - y0)
        for (x0, y0), (x1, y1) in zip(points, points[1:])
    )


class WritingBoard:
    """An always-on-top board the air cursor draws on, then shrinks back."""

    def __init__(self, root, controller, config, on_done=None):
        self.root = root
        self.controller = controller
        self.config = config
        self.on_done = on_done
        self.win = None
        self._canvas = None
        self._photo = None
        self._rect = None
        self._strokes: list[list[tuple[float, float]]] = []
        self._current = None
        self._status = None
        self._busy = False

    def _gain(self) -> float:
        try:
            return max(1.5, min(8.0, float(self.config.get(
                "pointer.write_board_gain", 4.0
            ))))
        except (TypeError, ValueError):
            return 4.0

    def open(self) -> None:
        if self._busy:
            return
        if self.win is not None:
            try:
                self.win.deiconify()
                self.win.lift()
                return
            except tk.TclError:
                self.win = None
        self._build()

    def close(self) -> None:
        if self.win is None:
            return
        self._set_board_open(False)
        try:
            self.win.destroy()
        except tk.TclError:
            pass
        self.win = None
        self._canvas = None
        self._photo = None

    def _set_board_open(self, flag: bool) -> None:
        try:
            self.controller.board_open = flag
        except Exception:
            pass

    def _build(self) -> None:
        left, top, w, h = inject.screen_rect(
            self.config.get("pointer.screen", "auto")
        )
        if w <= 0 or h <= 0:
            return
        self._busy = False
        self._strokes = []
        self._current = None
        win_left, win_top, side, _side = board_rect(left, top, w, h)
        self._rect = (win_left, win_top, side, side)
        # Grab the backdrop before the board covers it, or the snapshot would
        # just be the board's own white canvas.
        backdrop = self._backdrop_image()

        win = tk.Toplevel(self.root)
        self.win = win
        win.title("OpticalInputSvc 书写板")
        win.geometry(f"{side}x{side}+{win_left}+{win_top}")
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg="#f7f7f7")

        # The canvas is the whole window, so board pixels and screen pixels
        # are the same scale and the shrink centre is the visible centre.
        canvas = tk.Canvas(
            win, bg="#ffffff", highlightthickness=0, cursor="crosshair"
        )
        canvas.pack(fill="both", expand=True)
        self._canvas = canvas

        bar = tk.Frame(win, bg="#1b1b1b", padx=10, pady=8)
        bar.place(x=10, y=10)
        tk.Label(
            bar,
            text=f"书写板 · {self._gain():.1f} 倍",
            font=("Microsoft YaHei UI", 15, "bold"),
            bg="#1b1b1b",
            fg="#ffffff",
        ).pack(side="left")
        ttk.Button(
            bar, text="提交", command=self._commit
        ).pack(side="left", padx=(16, 0), ipady=10)
        ttk.Button(
            bar, text="清空", command=self._clear
        ).pack(side="left", padx=(8, 0), ipady=10)
        ttk.Button(
            bar, text="取消", command=self.close
        ).pack(side="left", padx=(8, 0), ipady=10)

        self._status = tk.Label(
            win,
            text="按住左键在板上写大字，提交后缩小写回原来的窗口。",
            font=("Microsoft YaHei UI", 12),
            bg="#e8e8e8",
            fg="#333333",
            padx=12,
            pady=6,
        )
        self._status.place(relx=0.5, rely=1.0, y=-12, anchor="s")

        canvas.bind("<Button-1>", self._down)
        canvas.bind("<B1-Motion>", self._move)
        canvas.bind("<ButtonRelease-1>", self._up)
        win.bind("<Return>", lambda _e: self._commit())
        win.bind("<Escape>", lambda _e: self.close())
        win.protocol("WM_DELETE_WINDOW", self.close)

        self._apply_backdrop(backdrop)
        self._set_board_open(True)

    def _backdrop_image(self):
        from .magnifier import grab

        _left, _top, w, h = self._rect
        src_x, src_y, side = source_rect(self._rect, self._gain())
        return grab(src_x, src_y, side, w, h)

    def _apply_backdrop(self, image) -> None:
        if self._canvas is None:
            return
        if image is None:
            self._canvas.configure(bg="#ffffff")
            return
        try:
            from PIL import ImageTk

            self._canvas.update_idletasks()
            size = (self._canvas.winfo_width(), self._canvas.winfo_height())
            if size[0] > 1 and size[1] > 1 and image.size != size:
                image = image.resize(size)
            self._photo = ImageTk.PhotoImage(image=image)
        except Exception:
            self._canvas.configure(bg="#ffffff")
            return
        self._canvas.create_image(0, 0, image=self._photo, anchor="nw")

    def _down(self, event) -> None:
        self._current = [(float(event.x_root), float(event.y_root))]
        self._strokes.append(self._current)
        self._redraw()

    def _move(self, event) -> None:
        if self._current is None:
            return
        self._current.append((float(event.x_root), float(event.y_root)))
        self._redraw()

    def _up(self, event) -> None:
        if self._current is None:
            return
        self._current.append((float(event.x_root), float(event.y_root)))
        if len(self._current) < 2:
            self._strokes.pop()
        self._current = None
        self._redraw()

    def _clear(self) -> None:
        self._strokes = []
        self._current = None
        self._redraw()

    def _redraw(self) -> None:
        if self._canvas is None:
            return
        self._canvas.delete("stroke")
        try:
            ox = self._canvas.winfo_rootx()
            oy = self._canvas.winfo_rooty()
        except tk.TclError:
            return
        for stroke in self._strokes:
            if len(stroke) < 2:
                continue
            flat = []
            for x, y in stroke:
                flat.extend((x - ox, y - oy))
            self._canvas.create_line(
                *flat, fill="#c81e1e", width=4, capstyle="round",
                joinstyle="round", tags="stroke",
            )
        if self._status is not None:
            written = int(sum(total_length(s) for s in self._strokes))
            self._status.configure(
                text=f"已写 {len(self._strokes)} 笔、约 {written} 像素。"
                f"提交后缩小 {self._gain():.1f} 倍写回原来的窗口。"
            )

    def _commit(self) -> None:
        if self._busy:
            return
        strokes = [s for s in self._strokes if len(s) >= 2]
        if not strokes:
            self.close()
            return
        self._busy = True
        rect = self._rect
        gain = self._gain()
        controller = self.controller
        on_done = self.on_done
        self.close()

        def work() -> None:
            was_active = bool(getattr(controller, "active", False))
            if was_active:
                controller.set_active(False)
            try:
                # The board has just been withdrawn, so Windows hands the
                # foreground back to the app that was underneath it.
                time.sleep(0.3)
                for stroke in strokes:
                    points = resample(to_screen(stroke, rect, gain), 2.0)
                    if len(points) < 2:
                        continue
                    inject.set_cursor(*points[0])
                    inject.button("left", True)
                    time.sleep(0.04)
                    for x, y in points[1:]:
                        inject.set_cursor(x, y)
                        time.sleep(0.004)
                    inject.button("left", False)
                    time.sleep(0.06)
            finally:
                inject.release_all()
                if was_active:
                    controller.set_active(True)
                self._busy = False
                if on_done is not None:
                    try:
                        on_done(len(strokes))
                    except Exception:
                        pass

        threading.Thread(
            target=work, daemon=True, name="airpoint-write-board"
        ).start()


def open_board(root, controller, config, on_done=None) -> WritingBoard:
    board = WritingBoard(root, controller, config, on_done=on_done)
    board.open()
    return board
