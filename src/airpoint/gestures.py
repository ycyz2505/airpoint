from __future__ import annotations

import math


class GestureOutput:
    def __init__(self):
        self.left_click = False
        self.double_click = False
        self.left_down = False
        self.left_up = False
        self.right_click = False


class TwoHandOpen:
    """Both hands raised and spread for a moment toggles the control bar.

    The tray is unreachable from the back of a classroom, so a deliberate
    two-hand pose is the way to bring the bar back or put it away.
    """

    def __init__(self, config):
        self.config = config
        self._since = 0.0
        self._armed = True

    def reset(self) -> None:
        self._since = 0.0
        self._armed = True

    def update(self, pose, now: float) -> bool:
        if not bool(self.config.get("overlay.two_hand_bar", True)):
            self.reset()
            return False
        left = pose.wrist("left")
        right = pose.wrist("right")
        shoulder = pose.shoulder_width()
        sl = pose.landmark(11)
        sr = pose.landmark(12)
        raised = (
            left is not None
            and right is not None
            and shoulder > 0.0
            and sl is not None
            and sr is not None
            and (left[1] + right[1]) / 2.0 < (sl[1] + sr[1]) / 2.0 - 0.05
            and abs(left[0] - right[0]) > 2.2 * shoulder
        )
        if not raised:
            self.reset()
            return False
        if not self._armed:
            return False
        if self._since <= 0.0:
            self._since = now
            return False
        hold = float(self.config.get("overlay.two_hand_hold_ms", 1500)) / 1000.0
        if hold <= 0.0:
            hold = 1.5
        if now - self._since < hold:
            return False
        self._armed = False
        self._since = 0.0
        return True


class GestureEngine:
    def __init__(self, config, log):
        self.config = config
        self.log = log
        self.state = "Idle"
        self._anchor = None
        self._anchor_time = 0.0
        self._last_click_time = 0.0
        self._last_click_pos = None

    def reset(self) -> None:
        self.state = "Idle"
        self._anchor = None
        self._last_click_time = 0.0
        self._last_click_pos = None

    def cancel(self) -> GestureOutput:
        out = GestureOutput()
        if self.state == "Dragging":
            out.left_up = True
        self.state = "Idle"
        self._anchor = None
        return out

    def hold_progress(self, now: float) -> float:
        """How far a pending press is towards becoming a right click."""
        if self.state != "Pending" or self._anchor is None:
            return 0.0
        hold = float(self.config.get("gesture.right_click_ms", 1000.0)) / 1000.0
        if hold <= 0.0:
            return 0.0
        return max(0.0, min(1.0, (now - self._anchor_time) / hold))

    def _emit_click(self, out: GestureOutput, now: float,
                    cursor: tuple[int, int]) -> None:
        window = float(self.config.get("gesture.double_click_ms", 350)) / 1000.0
        near = float(self.config.get("gesture.drag_threshold_px", 6.0))
        second = (
            window > 0.0
            and self._last_click_pos is not None
            and now - self._last_click_time <= window
            and math.dist(cursor, self._last_click_pos) <= near
        )
        if second:
            out.double_click = True
            self._last_click_time = 0.0
            self._last_click_pos = None
        else:
            out.left_click = True
            self._last_click_time = now
            self._last_click_pos = cursor

    def update(
        self, kind: str, now: float, cursor: tuple[int, int], ref=None
    ) -> GestureOutput:
        """Advance the state machine.

        ``cursor`` is the on-screen pointer. ``ref`` is where the hand maps to,
        which is what drag distance is measured against when the cursor is
        frozen for a pending press; it defaults to ``cursor``.
        """
        out = GestureOutput()
        spot = cursor if ref is None else ref
        threshold = float(self.config.get("gesture.drag_threshold_px", 6.0))
        hold = float(self.config.get("gesture.right_click_ms", 1000.0)) / 1000.0

        if self.state == "Idle":
            if kind == "down":
                self.state = "Pending"
                self._anchor = spot
                self._anchor_time = now
            return out

        if self.state == "Pending":
            moved = (
                self._anchor is not None
                and math.dist(spot, self._anchor) >= threshold
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
                self._emit_click(out, now, cursor)
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
