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

    def reset(self) -> None:
        self._x = None
        self._y = None

    def update(self, x: float, y: float, active: bool):
        if not active:
            self.reset()
            return x, y

        alpha = float(self.config.get("pointer.write_smoothing", 0.35))
        alpha = max(0.02, min(1.0, alpha))
        gate = float(self.config.get("pointer.write_min_step_px", 1.5))

        if self._x is None or self._y is None:
            self._x = float(x)
            self._y = float(y)
            return round(x), round(y)

        self._x = alpha * x + (1.0 - alpha) * self._x
        self._y = alpha * y + (1.0 - alpha) * self._y

        dx = self._x - round(self._x)
        dy = self._y - round(self._y)
        if (dx * dx + dy * dy) ** 0.5 * 10.0 < gate:
            return round(self._x), round(self._y)
        return round(self._x), round(self._y)
