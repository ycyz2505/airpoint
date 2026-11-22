from __future__ import annotations


class HandFallback:
    """§5.4: hand landmarks -> wrist -> frozen cursor -> paused.

    The hand model loses its landmarks far more often than pose loses the
    body, so a frame without a palm should not snap the cursor to the wrist:
    the wrist sits a few centimetres from the palm centre, which is a visible
    hop at six metres. The last palm is therefore held for a few frames, and
    only then does the wrist take over.

    When there is no fresh wrist either, the point is withheld, so the frame
    loop's motion gate keeps the cursor where it is -- frozen, not zeroed.
    After ``FREEZE_SECONDS`` the caller is told the hand is gone, so it can
    release buttons and say so; the loop keeps running, and one fresh frame
    brings everything back without a re-activation.

    A pose result held over from a lost body (``pose.stale``) is deliberately
    not passed in here: §6.2 keeps it for a second to steady the measurements,
    but a cursor should not follow a wrist nobody has seen for a second.
    """

    HOLD_FRAMES = 5
    FREEZE_SECONDS = 0.3

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._palm = None
        self._missing = 0
        self._last_good = 0.0
        self._ever = False

    def update(self, palm, wrist, now: float):
        """Return ``(point, lost)`` for this frame."""
        if palm is not None:
            self._palm = (float(palm[0]), float(palm[1]))
            self._missing = 0
        elif wrist is not None:
            self._missing += 1

        if palm is not None:
            point = self._palm
        elif wrist is not None:
            if self._palm is not None and self._missing <= self.HOLD_FRAMES:
                point = self._palm
            else:
                point = (float(wrist[0]), float(wrist[1]))
        else:
            point = None

        if point is not None:
            self._ever = True
            self._last_good = now
            return point, False
        if not self._ever:
            # Nothing has been seen since start or reset; not a loss.
            return None, False
        return None, now - self._last_good > self.FREEZE_SECONDS
