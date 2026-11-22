from __future__ import annotations

import math
import time

import cv2


class BodyLock:
    """§6.2: keep one body when the model's answer switches person.

    MediaPipe's Pose solution reports a single body, so a second person walking
    into frame arrives as the same body jumping somewhere else. The lock keeps
    the body accepted first and ignores a different one until it has held its
    new place for ``HOLD_SECONDS`` and outscores the locked body by ``MARGIN``;
    without that the cursor would follow whoever the model picked that frame.
    """

    RADIUS = 0.22
    HOLD_SECONDS = 2.0
    MARGIN = 0.15
    ACTIVITY_SECONDS = 0.5
    NEAR_SECONDS = 0.5

    def __init__(self) -> None:
        self.anchor: tuple[float, float] | None = None
        self.point: tuple[float, float] | None = None
        self.seen = 0.0
        self._challenger: tuple[float, float] | None = None
        self._challenger_since = 0.0

    def reset(self) -> None:
        self.anchor = None
        self.point = None
        self.seen = 0.0
        self._challenger = None
        self._challenger_since = 0.0

    @staticmethod
    def score(
        center: tuple[float, float],
        cursor: tuple[float, float] | None,
        box: list | tuple | None,
        now: float,
        seen: float,
    ) -> float:
        """§6.2: in the box, recently active, near the last cursor."""
        inside = 0.0
        if box and len(box) == 4:
            bx0, by0, bx1, by1 = [float(item) for item in box]
            if bx0 <= center[0] <= bx1 and by0 <= center[1] <= by1:
                inside = 1.0
        activity = max(0.0, 1.0 - max(0.0, now - seen) / BodyLock.ACTIVITY_SECONDS)
        near = 0.0
        if cursor is not None:
            distance = math.hypot(center[0] - cursor[0], center[1] - cursor[1])
            near = max(0.0, 1.0 - distance / BodyLock.NEAR_SECONDS)
        return 0.5 * inside + 0.25 * activity + 0.25 * near

    @staticmethod
    def _blend(
        anchor: tuple[float, float], center: tuple[float, float], weight: float
    ) -> tuple[float, float]:
        return (
            anchor[0] + weight * (center[0] - anchor[0]),
            anchor[1] + weight * (center[1] - anchor[1]),
        )

    def update(
        self,
        center: tuple[float, float],
        wrist: tuple[float, float] | None,
        now: float,
        box: list | tuple | None = None,
    ) -> bool:
        """Accept or ignore this frame's body. True means the cursor may move."""
        if self.anchor is None:
            self.anchor = center
            if wrist is not None:
                self.point = wrist
            self.seen = now
            self._challenger = None
            return True

        distance = math.hypot(
            center[0] - self.anchor[0], center[1] - self.anchor[1]
        )
        if distance <= self.RADIUS:
            self.anchor = self._blend(self.anchor, center, 0.4)
            if wrist is not None:
                self.point = wrist
            self.seen = now
            self._challenger = None
            self._challenger_since = 0.0
            return True

        if self._challenger is None:
            self._challenger = center
            self._challenger_since = now
            return False

        moved = math.hypot(
            center[0] - self._challenger[0], center[1] - self._challenger[1]
        )
        if moved > self.RADIUS:
            self._challenger = center
            self._challenger_since = now
            return False
        self._challenger = self._blend(self._challenger, center, 0.4)

        if now - self._challenger_since < self.HOLD_SECONDS:
            return False

        locked = self.score(self.anchor, self.point, box, now, self.seen)
        challenger = self.score(center, wrist, box, now, now)
        if challenger < locked + self.MARGIN:
            return False

        self.anchor = center
        self.point = wrist
        self.seen = now
        self._challenger = None
        self._challenger_since = 0.0
        return True


class PoseTracker:
    LEFT_WRIST = 15
    RIGHT_WRIST = 16
    LEFT_SHOULDER = 11
    RIGHT_SHOULDER = 12
    LEFT_HIP = 23
    RIGHT_HIP = 24
    STALE_SECONDS = 1.0

    def __init__(self, config, log):
        self.config = config
        self.log = log
        self._pose = None
        self._failed = False
        self.last_landmarks = None
        self.lock = BodyLock()
        self.stale = False
        self.ignored = False
        self._last_ok = 0.0
        self._last_wrist = None

    @property
    def ready(self) -> bool:
        return self._pose is not None

    def _ensure(self) -> bool:
        if self._pose is not None:
            return True
        if self._failed:
            return False
        try:
            import mediapipe as mp

            complexity = int(self.config.get("pose.model_complexity", 1))
            confidence = float(self.config.get("pose.min_confidence", 0.5))
            self._pose = mp.solutions.pose.Pose(
                static_image_mode=False,
                model_complexity=max(0, min(2, complexity)),
                smooth_landmarks=True,
                min_detection_confidence=confidence,
                min_tracking_confidence=confidence,
            )
            self.log(f"pose ready complexity={complexity}")
            return True
        except Exception as exc:
            self._failed = True
            self.log(f"pose init failed: {exc}")
            return False

    def _center(self, landmarks):
        """Body centre for the lock: shoulder midpoint, hips as a fallback."""
        for left, right in (
            (self.LEFT_SHOULDER, self.RIGHT_SHOULDER),
            (self.LEFT_HIP, self.RIGHT_HIP),
        ):
            a = landmarks[left]
            b = landmarks[right]
            if a.visibility >= 0.3 and b.visibility >= 0.3:
                return (
                    (float(a.x) + float(b.x)) / 2.0,
                    (float(a.y) + float(b.y)) / 2.0,
                )
        return None

    def _wrist(self, landmarks):
        hand = str(self.config.get("pointer.hand", "right")).lower()
        index = self.LEFT_WRIST if hand == "left" else self.RIGHT_WRIST
        landmark = landmarks[index]
        confidence = float(self.config.get("pose.min_confidence", 0.5))
        if landmark.visibility < confidence:
            return None
        return float(landmark.x), float(landmark.y), float(landmark.visibility)

    def process(self, frame):
        if not self._ensure():
            return None
        try:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            rgb.flags.writeable = False
            result = self._pose.process(rgb)
        except Exception as exc:
            self.log(f"pose failed: {exc}")
            return None

        now = time.time()
        if not result.pose_landmarks:
            self.ignored = False
            return self._lost(now)

        landmarks = result.pose_landmarks.landmark
        center = self._center(landmarks)
        wrist = self._wrist(landmarks)
        box = self.config.get("pointer.box", [])
        if not isinstance(box, list) or len(box) != 4:
            box = []
        locked = bool(self.config.get("pose.multi_lock", True))
        if locked and center is not None and not self.lock.update(
            center, wrist, now, box
        ):
            # §6.2: this is somebody else. The locked body's last landmarks
            # stay, so shoulder width and the skeleton keep their meaning for
            # the 1 s hold, then the lock lets go.
            self.ignored = True
            return self._lost(now)

        self.last_landmarks = landmarks
        self._last_ok = now
        self._last_wrist = wrist
        self.stale = False
        self.ignored = False
        return wrist

    def _lost(self, now: float):
        """§6.2: hold the last result for 1 s, marked stale, then give up."""
        if self._last_wrist is not None and now - self._last_ok <= self.STALE_SECONDS:
            self.stale = True
            return self._last_wrist
        self.stale = False
        self._last_wrist = None
        self.last_landmarks = None
        # Keep the anchor through a short absence so the 2 s hold still
        # applies when the model switches person; a long one lets go, so the
        # next person to sit down is not ignored.
        if self.lock.anchor is not None and now - self._last_ok > 5.0:
            self.lock.reset()
        return None

    def close(self) -> None:
        if self._pose is not None:
            try:
                self._pose.close()
            except Exception:
                pass
            self._pose = None

    def landmark(self, index: int, min_visibility: float = 0.3):
        if not self.last_landmarks:
            return None
        item = self.last_landmarks[index]
        if item.visibility < min_visibility:
            return None
        return float(item.x), float(item.y), float(item.visibility)

    def wrist(self, hand: str = "right"):
        return self.landmark(15 if hand == "left" else 16)

    def elbow(self, hand: str = "right"):
        return self.landmark(13 if hand == "left" else 14)

    def bounds(self, min_visibility: float = 0.3):
        if not self.last_landmarks:
            return None
        xs = []
        ys = []
        for item in self.last_landmarks:
            if item.visibility < min_visibility:
                continue
            xs.append(float(item.x))
            ys.append(float(item.y))
        if len(xs) < 4:
            return None
        return min(xs), min(ys), max(xs), max(ys)

    def shoulder_width(self) -> float:
        if not self.last_landmarks:
            return 0.0
        left = self.last_landmarks[11]
        right = self.last_landmarks[12]
        if left.visibility < 0.3 or right.visibility < 0.3:
            return 0.0
        return abs(float(left.x) - float(right.x))

    def draw(self, frame) -> None:
        landmarks = self.last_landmarks
        if not landmarks:
            return
        height, width = frame.shape[:2]

        def point(index: int) -> tuple[int, int]:
            item = landmarks[index]
            return int(item.x * width), int(item.y * height)

        for a, b in ((11, 13), (13, 15), (12, 14), (14, 16), (11, 12)):
            cv2.line(frame, point(a), point(b), (0, 200, 255), 2)
        for index in (11, 12, 13, 14, 15, 16):
            cv2.circle(frame, point(index), 4, (0, 255, 0), -1)

        hand = str(self.config.get("pointer.hand", "right")).lower()
        wrist = self.LEFT_WRIST if hand == "left" else self.RIGHT_WRIST
        cv2.circle(frame, point(wrist), 10, (0, 0, 255), 2)
