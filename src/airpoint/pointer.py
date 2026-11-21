from __future__ import annotations

import math


class _LowPass:
    def __init__(self):
        self.value = None


class OneEuro:
    def __init__(self, mincutoff: float = 0.8, beta: float = 0.3, dcutoff: float = 1.0):
        self.mincutoff = mincutoff
        self.beta = beta
        self.dcutoff = dcutoff
        self._x = _LowPass()
        self._dx = _LowPass()
        self._dx.value = 0.0
        self._last = None
        self._last_t = None

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, value: float, t: float) -> float:
        if self._last is None or self._last_t is None:
            self._last = value
            self._last_t = t
            self._x.value = value
            return value

        dt = max(1e-6, t - self._last_t)
        derivative = (value - self._last) / dt

        alpha_d = self._alpha(self.dcutoff, dt)
        self._dx.value = alpha_d * derivative + (1.0 - alpha_d) * self._dx.value

        cutoff = self.mincutoff + self.beta * abs(self._dx.value)
        alpha = self._alpha(cutoff, dt)
        self._x.value = alpha * value + (1.0 - alpha) * self._x.value

        self._last = value
        self._last_t = t
        return self._x.value


class RelativePointer:
    def __init__(self, config):
        self.config = config
        self.reset()

    def reset(self) -> None:
        mincutoff = float(self.config.get("pointer.mincutoff", 0.8))
        beta = float(self.config.get("pointer.beta", 0.3))
        self._fx = OneEuro(mincutoff=mincutoff, beta=beta)
        self._fy = OneEuro(mincutoff=mincutoff, beta=beta)
        self._last = None
        self._last_t = None

    def update(
        self,
        x: float,
        y: float,
        t: float,
        screen_w: int,
        screen_h: int,
    ) -> tuple[float, float]:
        fx = self._fx(x, t)
        fy = self._fy(y, t)

        if self._last is None or self._last_t is None:
            self._last = (fx, fy)
            self._last_t = t
            return 0.0, 0.0

        dt = max(1e-6, t - self._last_t)
        if dt > 0.5:
            self._last = (fx, fy)
            self._last_t = t
            return 0.0, 0.0

        gain = float(self.config.get("pointer.gain_relative", 1.8))
        prediction = float(self.config.get("pointer.prediction_ms", 40)) / 1000.0
        max_step = float(self.config.get("pointer.max_step_px", 250.0))

        vx = (fx - self._last[0]) / dt
        vy = (fy - self._last[1]) / dt

        dx = (fx - self._last[0] + vx * prediction) * gain * screen_w
        dy = (fy - self._last[1] + vy * prediction) * gain * screen_h

        dx = max(-max_step, min(max_step, dx))
        dy = max(-max_step, min(max_step, dy))

        deadzone = float(self.config.get("pointer.deadzone_px", 0.5))
        if abs(dx) < deadzone:
            dx = 0.0
        if abs(dy) < deadzone:
            dy = 0.0

        self._last = (fx, fy)
        self._last_t = t
        return dx, dy
