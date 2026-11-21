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
            return None

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
