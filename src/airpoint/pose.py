from __future__ import annotations

import cv2


class PoseTracker:
    LEFT_WRIST = 15
    RIGHT_WRIST = 16

    def __init__(self, config, log):
        self.config = config
        self.log = log
        self._pose = None
        self._failed = False
        self.last_landmarks = None

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

        if not result.pose_landmarks:
            self.last_landmarks = None
            return None

        self.last_landmarks = result.pose_landmarks.landmark
        hand = str(self.config.get("pointer.hand", "right")).lower()
        index = self.LEFT_WRIST if hand == "left" else self.RIGHT_WRIST
        landmark = result.pose_landmarks.landmark[index]
        confidence = float(self.config.get("pose.min_confidence", 0.5))
        if landmark.visibility < confidence:
            return None
        return float(landmark.x), float(landmark.y), float(landmark.visibility)

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
