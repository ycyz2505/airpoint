from __future__ import annotations

LEARNING_RATE = 0.02
MAX_STEP_PX = 2.0
MAX_OFFSET_PX = 60.0


class SelfCalibrator:
    """Slowly learns a screen-space offset from clicks in absolute mode.

    Each click gives one sample: where the mapping put the cursor versus where
    the user actually clicked. The correction is bounded per sample and in
    total so a few stray clicks cannot throw the mapping off.
    """

    def __init__(self, offset=None):
        self.x = 0.0
        self.y = 0.0
        if offset and len(offset) == 2:
            self.x = float(offset[0])
            self.y = float(offset[1])
        self.samples = 0

    def reset(self) -> None:
        self.x = 0.0
        self.y = 0.0
        self.samples = 0

    @property
    def offset(self):
        return [round(self.x, 2), round(self.y, 2)]

    def add(self, mapped, settled) -> None:
        """Learn from one click.

        ``mapped`` is where the filter put the cursor; ``settled`` is where the
        hand actually sits once the user holds still to click. The difference is
        the smoothing lag and steady bias, which is what we correct.
        """
        if mapped is None or settled is None:
            return
        self.samples += 1
        for axis, mapped_value, settled_value in (
            ("x", float(mapped[0]), float(settled[0])),
            ("y", float(mapped[1]), float(settled[1])),
        ):
            error = settled_value - mapped_value
            step = error * LEARNING_RATE
            step = max(-MAX_STEP_PX, min(MAX_STEP_PX, step))
            current = getattr(self, axis) + step
            setattr(self, axis, max(-MAX_OFFSET_PX, min(MAX_OFFSET_PX, current)))

    def apply(self, target):
        if target is None:
            return None
        return target[0] + self.x, target[1] + self.y
