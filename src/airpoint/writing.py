from __future__ import annotations


class StrokeSmoother:
    """Extra smoothing applied while the pen is down.

    Whiteboard software renders every cursor move as a stroke point, so raw
    tracking jitter becomes visible wobble in the handwriting. While the left
    button is held the cursor is filtered harder, which trades a little
    latency for strokes that actually look drawn by hand.
    """

    def __init__(self, config):
        self.config = config
        self._x = None
        self._y = None
        self._last = None

    def reset(self) -> None:
        self._x = None
        self._y = None
        self._last = None

    def update(self, x: float, y: float, active: bool):
        if not active:
            self.reset()
            return x, y

        alpha = float(self.config.get("pointer.write_smoothing", 0.35))
        alpha = max(0.02, min(1.0, alpha))
        gate = max(0.0, float(self.config.get("pointer.write_min_step_px", 1.5)))

        if self._x is None or self._y is None:
            self._x = float(x)
            self._y = float(y)
            self._last = (round(x), round(y))
            return self._last

        self._x = alpha * x + (1.0 - alpha) * self._x
        self._y = alpha * y + (1.0 - alpha) * self._y

        # Hold the pen still until the smoothed point has really moved, so
        # sub-pixel jitter does not become a wobbly stroke. The offset is kept
        # in the filter, so the pen catches up once the hand does move.
        target = (round(self._x), round(self._y))
        if self._last is not None:
            dx = target[0] - self._last[0]
            dy = target[1] - self._last[1]
            if (dx * dx + dy * dy) ** 0.5 < gate:
                return self._last
        self._last = target
        return target
