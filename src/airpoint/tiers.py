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


def band_for(shoulder: float, config) -> str:
    """Distance band from normalised shoulder width."""
    a = float(config.get("tier.shoulder_a", 0.085))
    b = float(config.get("tier.shoulder_b", 0.060))
    c = float(config.get("tier.shoulder_c", 0.045))
    d = float(config.get("tier.shoulder_d", 0.034))
    if shoulder >= a:
        return "A"
    if shoulder >= b:
        return "B"
    if shoulder >= c:
        return "C"
    if shoulder >= d:
        return "D"
    return "E"


def lower(name: str, steps: int) -> str:
    index = min(len(ORDER) - 1, ORDER.index(name) + steps)
    return ORDER[index]


def suggest_tier(
    shoulder: float,
    palm_px: float,
    drop: float,
    pose_conf: float,
    config,
) -> tuple[str, str]:
    """Pick a tier for measured conditions; returns (tier, reason)."""
    band = band_for(shoulder, config)
    target = band
    reasons = []
    if palm_px > 0:
        if palm_px < float(config.get("tier.palm_ok", 20.0)):
            target = lower(target, 1)
            reasons.append("手掌偏小")
        elif (
            palm_px < float(config.get("tier.palm_good", 30.0))
            and ORDER.index(target) <= 1
        ):
            target = lower(target, 1)
            reasons.append("手掌一般")
    if drop > float(config.get("tier.drop_bad", 0.35)):
        target = lower(target, 1)
        reasons.append("丢手偏多")
    if pose_conf and pose_conf < 0.4:
        target = lower(target, 1)
        reasons.append("姿态置信偏低")
    reason = "、".join(reasons) if reasons else "距离与手掌均合适"
    return target, f"{band} 档（{reason}）"


def palm_px_1080(palm: float) -> float:
    """Palm size in pixels at 1080p, whatever the capture width is.

    ``palm`` is a distance normalised to frame width, so raw pixels double at
    4K. The tier thresholds (``tier.palm_ok`` 20 px, ``tier.palm_good`` 30 px)
    are the 1080p numbers from the acceptance matrix; without this the same
    hand at the same seat would score a whole tier better on a 4K camera.
    """
    return max(0.0, float(palm)) * 1920.0


def capability(palm_px: float, drop: float, config) -> int:
    """0–100 capability score for the tray menu (§5.1, §10.4).

    The tier only says which band was chosen; the score says how comfortably
    the measurements sit inside it. Palm size carries half of it, the drop
    rate the other half, and each is clamped so one bad number cannot zero the
    whole reading.
    """
    palm = float(palm_px or 0.0)
    good = max(1.0, float(config.get("tier.palm_good", 30.0)))
    palm_part = max(0.0, min(1.0, palm / good)) * 50.0
    drop = max(0.0, min(1.0, float(drop or 0.0)))
    drop_part = (1.0 - drop) * 50.0
    return int(round(max(0.0, min(100.0, palm_part + drop_part))))


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
        self._adopted = False

    def _band(self) -> str:
        if self.shoulder <= 0:
            return self.band or "C"
        return band_for(self.shoulder, self.config)

    @staticmethod
    def _lower(name: str, steps: int) -> str:
        return lower(name, steps)

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
            if not self._adopted and self.shoulder > 0:
                self._adopted = True
                self.current = self.target
                self.target_since = now
                self.log(f"tier {self.current}")
            elif self.target != self.current and not busy:
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
        mode = spec[0]
        scheme = spec[1]
        click = spec[2]
        hands = spec[3]
        if apply:
            if not bool(self.config.get("tier.apply_mode", True)):
                mode = str(self.config.get("pointer.mode", mode))
                if mode == "auto":
                    mode = "absolute" if self.config.get("pointer.box") else "relative"
            if not bool(self.config.get("tier.apply_gesture", False)):
                scheme = str(self.config.get("gesture.scheme", scheme))
                click = str(self.config.get("gesture.click", click))
                if scheme == "auto":
                    scheme = "single"
                if click == "auto":
                    click = "pinch" if scheme == "single" else "fist"
            # ``hands`` stays the tier's own call: it is what decides whether
            # the hand model runs at all, and E has to keep it off no matter
            # which gesture preference the user picked.
        return TierState(
            name=self.current,
            mode=mode,
            scheme=scheme,
            click=click,
            hands=hands,
            band=self.band,
            applied=apply,
        )
