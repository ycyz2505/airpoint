from __future__ import annotations

import math


def safe_zone(x: float, y: float, screen_w: int, screen_h: int,
              margin: float = 0.05,
              origin: tuple[float, float] = (0.0, 0.0)) -> bool:
    """True when the point sits in the click-free screen-edge band.

    ``origin`` is the top-left of the target display in virtual-desktop
    coordinates; a cursor on a second monitor is not near this screen's edge
    just because its virtual x is large.
    """
    x = float(x) - float(origin[0])
    y = float(y) - float(origin[1])
    mx = max(1.0, float(screen_w) * float(margin))
    my = max(1.0, float(screen_h) * float(margin))
    return x < mx or y < my or x > screen_w - mx or y > screen_h - my


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
        self._last_raw = None

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

        if (
            self._last is None
            or self._last_t is None
            or self._last_raw is None
        ):
            self._last = (fx, fy)
            self._last_t = t
            self._last_raw = (x, y)
            return 0.0, 0.0

        dt = max(1e-6, t - self._last_t)
        if dt > 0.5:
            self._last = (fx, fy)
            self._last_t = t
            self._last_raw = (x, y)
            return 0.0, 0.0

        raw_vx = (x - self._last_raw[0]) / dt
        raw_vy = (y - self._last_raw[1]) / dt
        raw_speed = math.hypot(raw_vx, raw_vy)

        if raw_speed < float(self.config.get("pointer.rest_speed", 0.05)):
            self._last = (fx, fy)
            self._last_t = t
            self._last_raw = (x, y)
            return 0.0, 0.0

        gain = float(self.config.get("pointer.gain_relative", 1.8))
        prediction = float(self.config.get("pointer.prediction_ms", 40)) / 1000.0
        max_step = float(self.config.get("pointer.max_step_px", 250.0))

        if bool(self.config.get("pointer.accel_enabled", True)):
            v0 = max(1e-6, float(self.config.get("pointer.accel_v0", 0.8)))
            cap = max(1.0, float(self.config.get("pointer.accel_max", 2.0)))
            gain *= min(cap, 1.0 + (raw_speed / v0) ** 2)

        dx = (fx - self._last[0] + raw_vx * prediction) * gain * screen_w
        dy = (fy - self._last[1] + raw_vy * prediction) * gain * screen_h

        dx = max(-max_step, min(max_step, dx))
        dy = max(-max_step, min(max_step, dy))

        deadzone = float(self.config.get("pointer.deadzone_px", 0.5))
        if abs(dx) < deadzone:
            dx = 0.0
        if abs(dy) < deadzone:
            dy = 0.0

        self._last = (fx, fy)
        self._last_t = t
        self._last_raw = (x, y)
        return dx, dy


def clamp_to_screen(
    x: float,
    y: float,
    screen_w: int,
    screen_h: int,
    origin: tuple[float, float] = (0.0, 0.0),
) -> tuple[float, float]:
    """Keep a virtual-desktop point inside the target display's rect (§7.4)."""
    left = float(origin[0])
    top = float(origin[1])
    right = left + max(1.0, float(screen_w)) - 1.0
    bottom = top + max(1.0, float(screen_h)) - 1.0
    return (
        max(left, min(right, float(x))),
        max(top, min(bottom, float(y))),
    )


def map_point(x, y, box, screen_w, screen_h, calibration=None,
              origin: tuple[float, float] = (0.0, 0.0)):
    """Map a normalized frame point inside ``box`` to screen pixels.

    ``origin`` shifts the result into virtual-desktop coordinates, so a
    target display that is not the primary one maps to its own pixels
    instead of the primary's.
    """
    if not box or len(box) != 4:
        return None
    x0, y0, x1, y1 = [float(item) for item in box]
    if x1 <= x0 or y1 <= y0:
        return None
    if not (x0 <= x <= x1 and y0 <= y <= y1):
        return None

    if calibration is not None and len(calibration) == 9:
        h = [float(item) for item in calibration]
        denominator = h[6] * x + h[7] * y + h[8]
        if abs(denominator) < 1e-9:
            return None
        u = (h[0] * x + h[1] * y + h[2]) / denominator
        v = (h[3] * x + h[4] * y + h[5]) / denominator
        # A hand at the box edge can fit slightly outside the display; without
        # this the cursor lands on the neighbouring monitor.
        u = max(0.0, min(float(screen_w), u))
        v = max(0.0, min(float(screen_h), v))
        return u + float(origin[0]), v + float(origin[1])

    u = (x - x0) / (x1 - x0)
    v = (y - y0) / (y1 - y0)
    u = max(0.0, min(1.0, u))
    v = max(0.0, min(1.0, v))
    return (
        float(origin[0]) + u * screen_w,
        float(origin[1]) + v * screen_h,
    )


class AbsolutePointer:
    def __init__(self, config):
        self.config = config
        self.reset()

    def reset(self) -> None:
        mincutoff = float(self.config.get("pointer.mincutoff", 0.8))
        beta = float(self.config.get("pointer.beta", 0.3))
        self._fx = OneEuro(mincutoff=mincutoff, beta=beta)
        self._fy = OneEuro(mincutoff=mincutoff, beta=beta)

    def update(
        self,
        x: float,
        y: float,
        t: float,
        box,
        screen_w: int,
        screen_h: int,
        calibration=None,
        origin: tuple[float, float] = (0.0, 0.0),
    ):
        fx = self._fx(x, t)
        fy = self._fy(y, t)
        return map_point(
            fx, fy, box, screen_w, screen_h, calibration, origin
        )
