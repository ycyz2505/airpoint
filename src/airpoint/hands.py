from __future__ import annotations

import math

import cv2

HAND_CONNECTIONS = (
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20),
    (0, 17),
)

# At 4K the wrist crop can reach 860 px a side, but the model works on 224
# internally, so everything past this is wasted decode. Landmarks are
# normalised within the crop, so shrinking it does not move them.
CROP_MAX = 640


def crop_scale(width: int, height: int) -> float:
    """Factor that keeps the crop's longer side within CROP_MAX."""
    longest = max(int(width), int(height))
    if longest <= CROP_MAX:
        return 1.0
    return CROP_MAX / float(longest)


class HandTracker:
    def __init__(self, config, log):
        self.config = config
        self.log = log
        self._hands = None
        self._failed = False
        self._state = {
            "left": {
                "pinch": False,
                "fist": False,
                "raw": "open",
                "count": 0,
                "stable": "open",
            },
            "right": {
                "pinch": False,
                "fist": False,
                "raw": "open",
                "count": 0,
                "stable": "open",
            },
        }
        self.last_landmarks = {"left": None, "right": None}
        self.last_box = {"left": None, "right": None}

    @property
    def ready(self) -> bool:
        return self._hands is not None

    def _ensure(self) -> bool:
        if self._hands is not None:
            return True
        if self._failed:
            return False
        try:
            import mediapipe as mp

            complexity = int(self.config.get("hands.model_complexity", 0))
            confidence = float(self.config.get("hands.min_confidence", 0.5))
            self._hands = mp.solutions.hands.Hands(
                static_image_mode=False,
                max_num_hands=1,
                model_complexity=max(0, min(1, complexity)),
                min_detection_confidence=confidence,
                min_tracking_confidence=confidence,
            )
            self.log(f"hands ready complexity={complexity}")
            return True
        except Exception as exc:
            self._failed = True
            self.log(f"hands init failed: {exc}")
            return False

    def _crop(self, frame, wrist, elbow):
        height, width = frame.shape[:2]
        if elbow is not None and elbow[2] > 0.4:
            dx = wrist[0] - elbow[0]
            dy = wrist[1] - elbow[1]
            length = math.hypot(dx, dy)
            if length > 0.01:
                cx = wrist[0] + dx / length * 0.35 * length
                cy = wrist[1] + dy / length * 0.35 * length
                size = max(0.05, min(0.40, 2.0 * length))
            else:
                cx, cy, size = wrist[0], wrist[1], 0.08
        else:
            cx, cy, size = wrist[0], wrist[1], 0.08

        half = size / 2.0
        x0 = int(max(0, (cx - half) * width))
        x1 = int(min(width, (cx + half) * width))
        y0 = int(max(0, (cy - half) * height))
        y1 = int(min(height, (cy + half) * height))
        if x1 - x0 < 32 or y1 - y0 < 32:
            return None
        return x0, y0, x1, y1

    def process(self, frame, wrist, elbow, hand: str):
        if wrist is None or not self._ensure():
            return None
        box = self._crop(frame, wrist, elbow)
        if box is None:
            return None
        x0, y0, x1, y1 = box
        crop = frame[y0:y1, x0:x1]
        if crop.size == 0:
            return None
        scale = crop_scale(x1 - x0, y1 - y0)
        if scale < 1.0:
            crop = cv2.resize(
                crop,
                (
                    max(32, int((x1 - x0) * scale)),
                    max(32, int((y1 - y0) * scale)),
                ),
                interpolation=cv2.INTER_AREA,
            )

        rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        rgb.flags.writeable = False
        try:
            result = self._hands.process(rgb)
        except Exception as exc:
            self.log(f"hands failed: {exc}")
            return None

        state = self._state.get(hand, self._state["right"])
        if not result.multi_hand_landmarks:
            self.last_landmarks[hand] = None
            self.last_box[hand] = box
            return {
                "hand": hand,
                "gesture": "none",
                "raw": "none",
                "pinch": False,
                "fist": False,
                "pinch_ratio": 0.0,
                "fist_ratio": 0.0,
                "palm": 0.0,
                "landmarks": None,
                "confidence": 0.0,
                "box": box,
            }

        landmarks = result.multi_hand_landmarks[0].landmark
        height, width = frame.shape[:2]
        full = [
            (
                (x0 + item.x * (x1 - x0)) / width,
                (y0 + item.y * (y1 - y0)) / height,
            )
            for item in landmarks
        ]

        confidence = 0.0
        try:
            confidence = float(
                result.multi_handedness[0].classification[0].score
            )
        except Exception:
            pass

        palm = math.dist(full[0], full[9])
        if palm < 1e-6:
            palm = 1e-6
        pinch_ratio = math.dist(full[4], full[8]) / palm
        extensions = [
            math.dist(full[i], full[0]) / palm for i in (8, 12, 16, 20)
        ]
        fist_ratio = sum(extensions) / len(extensions)

        enter_pinch = float(self.config.get("gesture.pinch_enter", 0.45))
        exit_pinch = float(self.config.get("gesture.pinch_exit", 0.65))
        enter_fist = float(self.config.get("gesture.fist_enter", 1.30))
        exit_fist = float(self.config.get("gesture.fist_exit", 1.50))

        state["pinch"] = pinch_ratio < (
            exit_pinch if state["pinch"] else enter_pinch
        )
        state["fist"] = fist_ratio < (
            exit_fist if state["fist"] else enter_fist
        )

        if state["pinch"]:
            raw = "pinch"
        elif state["fist"]:
            raw = "fist"
        else:
            raw = "open"

        if raw == state["raw"]:
            state["count"] += 1
        else:
            state["raw"] = raw
            state["count"] = 1
        if state["count"] >= 2:
            state["stable"] = raw

        self.last_landmarks[hand] = full
        self.last_box[hand] = box
        return {
            "hand": hand,
            "gesture": state["stable"],
            "raw": raw,
            "pinch": state["pinch"],
            "fist": state["fist"],
            "pinch_ratio": pinch_ratio,
            "fist_ratio": fist_ratio,
            "palm": palm,
            "landmarks": full,
            "confidence": confidence,
            "box": box,
        }

    def reset(self) -> None:
        for state in self._state.values():
            state["pinch"] = False
            state["fist"] = False
            state["raw"] = "open"
            state["count"] = 0
            state["stable"] = "open"
        self.last_landmarks = {"left": None, "right": None}
        self.last_box = {"left": None, "right": None}

    def draw(self, frame, result) -> None:
        if not result or not result.get("landmarks"):
            return
        height, width = frame.shape[:2]
        points = [
            (int(x * width), int(y * height))
            for x, y in result["landmarks"]
        ]
        for a, b in HAND_CONNECTIONS:
            cv2.line(frame, points[a], points[b], (0, 255, 0), 1)
        for point in points:
            cv2.circle(frame, point, 2, (0, 255, 0), -1)
        cv2.circle(frame, points[4], 5, (0, 0, 255), 1)
        cv2.circle(frame, points[8], 5, (0, 0, 255), 1)

    def close(self) -> None:
        if self._hands is not None:
            try:
                self._hands.close()
            except Exception:
                pass
            self._hands = None
