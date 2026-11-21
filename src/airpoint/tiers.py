from __future__ import annotations

from dataclasses import dataclass

ORDER = ("A", "B", "C", "D", "E")

SPECS = {
    "A": ("absolute", "single", "pinch", True),
    "B": ("absolute", "single", "pinch", True),
    "C": ("relative", "double", "fist", True),
    "D": ("relative", "double", "fist", True),
    "E": ("relative", "single", "fist", False),
}


@dataclass(frozen=True)
class TierState:
    name: str
    mode: str
    scheme: str
    click: str
    hands: bool
    band: str
    applied: bool


class TierEngine:
    def __init__(self, config, log):
        self.config = config
        self.log = log
        self.current = "C"
        self.band = "C"
        self.target = "C"
        self.target_since = 0.0
        self.shoulder = 0.0
        self.palm_px = 0.0
        self.drop = 0.0

    def _band(self) -> str:
        shoulder = self.shoulder
        if shoulder <= 0:
            return self.band or "C"
        a = float(self.config.get("tier.shoulder_a", 0.085))
        b = float(self.config.get("tier.shoulder_b", 0.060))
        c = float(self.config.get("tier.shoulder_c", 0.045))
        d = float(self.config.get("tier.shoulder_d", 0.034))
        if shoulder >= a:
            return "A"
        if shoulder >= b:
            return "B"
        if shoulder >= c:
            return "C"
        if shoulder >= d:
            return "D"
        return "E"

    @staticmethod
    def _lower(name: str, steps: int) -> str:
        index = min(len(ORDER) - 1, ORDER.index(name) + steps)
        return ORDER[index]

    def update(
        self,
        shoulder: float,
        palm_px: float,
        drop: float,
        pose_conf: float,
        busy: bool,
        now: float,
    ) -> TierState:
        if shoulder and shoulder > 0:
            self.shoulder = (
                shoulder
                if self.shoulder <= 0
                else self.shoulder * 0.9 + shoulder * 0.1
            )
        if palm_px and palm_px > 0:
            self.palm_px = (
                palm_px
                if self.palm_px <= 0
                else self.palm_px * 0.85 + palm_px * 0.15
            )
        self.drop = drop
        self.band = self._band()

        mode = str(self.config.get("tier.mode", "auto"))
        lock = str(self.config.get("tier.lock", "")).upper()
        apply = bool(self.config.get("tier.apply", True))

        if mode == "manual" and lock in SPECS:
            self.current = lock
            self.target = lock
        else:
            target = self.band
            palm_ok = float(self.config.get("tier.palm_ok", 20.0))
            palm_good = float(self.config.get("tier.palm_good", 30.0))
            if self.palm_px > 0:
                if self.palm_px < palm_ok:
                    target = self._lower(target, 1)
                elif (
                    self.palm_px < palm_good
                    and ORDER.index(target) <= 1
                ):
                    target = self._lower(target, 1)
            if self.drop > float(self.config.get("tier.drop_bad", 0.35)):
                target = self._lower(target, 1)
            if pose_conf and pose_conf < 0.4:
                target = self._lower(target, 1)

            if target != self.target:
                self.target = target
                self.target_since = now
            if self.target != self.current and not busy:
                worse = ORDER.index(self.target) > ORDER.index(self.current)
                delay = float(
                    self.config.get(
                        "tier.downgrade_ms"
                        if worse
                        else "tier.upgrade_ms",
                        1000 if worse else 8000,
                    )
                ) / 1000.0
                if now - self.target_since >= delay:
                    self.current = self.target
                    self.log(f"tier {self.current}")

        spec = SPECS[self.current]
        return TierState(
            name=self.current,
            mode=spec[0],
            scheme=spec[1],
            click=spec[2],
            hands=spec[3],
            band=self.band,
            applied=apply,
        )
