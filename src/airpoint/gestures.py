from __future__ import annotations

import math


class GestureOutput:
    def __init__(self):
        self.left_click = False
        self.left_down = False
        self.left_up = False
        self.right_click = False


class GestureEngine:
    def __init__(self, config, log):
        self.config = config
        self.log = log
        self.state = "Idle"
        self._anchor = None
        self._anchor_time = 0.0

    def reset(self) -> None:
        self.state = "Idle"
        self._anchor = None

    def cancel(self) -> GestureOutput:
        out = GestureOutput()
        if self.state == "Dragging":
            out.left_up = True
        self.state = "Idle"
        self._anchor = None
        return out

    def update(
        self, kind: str, now: float, cursor: tuple[int, int]
    ) -> GestureOutput:
        out = GestureOutput()
        threshold = float(self.config.get("gesture.drag_threshold_px", 6.0))
        hold = float(self.config.get("gesture.right_click_ms", 1000.0)) / 1000.0

        if self.state == "Idle":
            if kind == "down":
                self.state = "Pending"
                self._anchor = cursor
                self._anchor_time = now
            return out

        if self.state == "Pending":
            moved = (
                self._anchor is not None
                and math.dist(cursor, self._anchor) >= threshold
            )
            if moved:
                self.state = "Dragging"
                out.left_down = True
                return out
            if now - self._anchor_time >= hold:
                out.right_click = True
                self.state = "Consumed"
                return out
            if kind == "up":
                out.left_click = True
                self.state = "Idle"
                self._anchor = None
            return out

        if self.state == "Dragging":
            if kind == "up":
                out.left_up = True
                self.state = "Idle"
                self._anchor = None
            return out

        if self.state == "Consumed":
            if kind == "up":
                self.state = "Idle"
                self._anchor = None
            return out

        return out
