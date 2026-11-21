from __future__ import annotations

import threading
import time
from collections import deque

import cv2

from . import inject
from .capture import Camera
from .gestures import GestureEngine
from .hands import HandTracker
from .pointer import AbsolutePointer, RelativePointer
from .pose import PoseTracker
from .tiers import TierEngine


class Controller:
    def __init__(self, config, log, preview=None):
        self.config = config
        self.log = log
        self.preview = preview
        self._thread = None
        self._stop = threading.Event()
        self._active = threading.Event()
        self._reset = threading.Event()
        self._reload_camera = threading.Event()
        self._lock = threading.Lock()
        self._last_fps_log = 0.0
        self._window_start = 0.0
        self._frames = 0
        self._hits = 0
        self._carry_x = 0.0
        self._carry_y = 0.0
        self._scroll_acc = 0.0
        self._scroll_dir = 1
        self._frame_index = 0
        self._last_mode = ""
        self._inside = False
        self._calibrating = threading.Event()
        self._tier = TierEngine(config, log)
        self._seen = deque(maxlen=60)
        self._last_palm_px = 0.0

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, daemon=True, name="airpoint-controller"
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._active.clear()
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
        inject.release_all()
        self.log("controller stopped")

    @property
    def active(self) -> bool:
        return self._active.is_set()

    def set_active(self, active: bool) -> None:
        if active:
            self._reset.set()
            self._active.set()
            self.log("controller active")
        else:
            self._active.clear()
            inject.release_all()
            self.log("controller paused")

    def reload(self) -> None:
        self._reload_camera.set()
        self._reset.set()

    def set_calibrating(self, value: bool) -> None:
        if value:
            self._calibrating.set()
            self._reset.set()
            inject.release_all()
            self.log("calibration start")
        else:
            self._calibrating.clear()
            self._reset.set()
            self.log("calibration end")

    def _run(self) -> None:
        camera = Camera(self.config, self.log)
        pose = PoseTracker(self.config, self.log)
        hands = HandTracker(self.config, self.log)
        pointer = RelativePointer(self.config)
        absolute = AbsolutePointer(self.config)
        gestures = GestureEngine(self.config, self.log)
        last_hit = 0.0
        last_preview = 0.0
        last_gesture_seen = 0.0
        last_click_result = None
        self._window_start = time.time()

        while not self._stop.is_set():
            if not self._active.is_set():
                if camera.opened:
                    camera.close()
                time.sleep(0.1)
                continue

            if self._reload_camera.is_set():
                self._reload_camera.clear()
                camera.close()

            if self._reset.is_set():
                self._reset.clear()
                pointer.reset()
                absolute.reset()
                gestures.reset()
                hands.reset()
                self._frames = 0
                self._hits = 0
                self._carry_x = 0.0
                self._carry_y = 0.0
                self._scroll_acc = 0.0
                self._inside = False
                self._last_mode = ""
                self._seen.clear()
                self._last_palm_px = 0.0
                last_hit = 0.0
                last_gesture_seen = 0.0
                last_click_result = None
                self._window_start = time.time()

            if not camera.opened:
                if not camera.open():
                    time.sleep(2.0)
                    continue

            frame = camera.read()
            if frame is None:
                time.sleep(0.05)
                continue

            self._frames += 1
            self._frame_index += 1
            height, width = frame.shape[:2]
            if width > 1280:
                scale = 1280.0 / float(width)
                small = cv2.resize(
                    frame,
                    (1280, max(1, int(height * scale))),
                    interpolation=cv2.INTER_AREA,
                )
            else:
                small = frame

            wrist = pose.process(small)
            now = time.time()
            calibrating = self._calibrating.is_set()

            self._seen.append(1 if wrist is not None else 0)
            drop = 1.0 - (sum(self._seen) / len(self._seen))
            shoulder = pose.shoulder_width()
            pose_conf = wrist[2] if wrist else 0.0
            busy = gestures.state != "Idle"
            tier_state = self._tier.update(
                shoulder, self._last_palm_px, drop, pose_conf, busy, now
            )

            moving_hand = str(self.config.get("pointer.hand", "right"))
            if tier_state.applied:
                mode = tier_state.mode
                scheme = tier_state.scheme
                click_name = tier_state.click
                hands_on = tier_state.hands
            else:
                mode = str(self.config.get("pointer.mode", "relative"))
                scheme = str(self.config.get("gesture.scheme", "auto"))
                if scheme == "auto":
                    scheme = "single"
                click_name = str(self.config.get("gesture.click", "auto"))
                if click_name == "auto":
                    click_name = (
                        "pinch" if scheme == "single" else "fist"
                    )
                hands_on = True
            click_hand = "left" if moving_hand == "right" else "right"

            moving_result = None
            click_result = None

            if hands_on and wrist is not None:
                moving_result = hands.process(
                    frame, wrist, pose.elbow(moving_hand), moving_hand
                )
                if last_hit and now - last_hit > 0.4:
                    pointer.reset()
                last_hit = now
                self._hits += 1
                if moving_result and moving_result.get("palm"):
                    self._last_palm_px = (
                        float(moving_result["palm"]) * width
                    )

            if hands_on and scheme == "double":
                click_wrist = pose.wrist(click_hand)
                if click_wrist is not None:
                    if self._frame_index % 2 == 0 or last_click_result is None:
                        click_result = hands.process(
                            frame,
                            click_wrist,
                            pose.elbow(click_hand),
                            click_hand,
                        )
                        last_click_result = click_result
                    else:
                        click_result = last_click_result
                else:
                    click_result = None
                    last_click_result = None

            point = None
            if wrist is not None:
                point = (wrist[0], wrist[1])
                if moving_result and moving_result.get("landmarks"):
                    lm = moving_result["landmarks"]
                    point = (
                        sum(lm[i][0] for i in (0, 5, 9, 13, 17)) / 5.0,
                        sum(lm[i][1] for i in (0, 5, 9, 13, 17)) / 5.0,
                    )

            box = self.config.get("pointer.box", [])
            if not isinstance(box, list) or len(box) != 4:
                box = []
            if mode == "auto":
                mode = "absolute" if box else "relative"
            if mode == "absolute" and not box:
                mode = "relative"

            if mode != self._last_mode:
                self._last_mode = mode
                pointer.reset()
                absolute.reset()
                gestures.reset()
                inject.release_all()
                self._inside = False

            inside = True
            if box:
                bx0, by0, bx1, by1 = [float(item) for item in box]
                inside = (
                    point is not None
                    and bx0 <= point[0] <= bx1
                    and by0 <= point[1] <= by1
                )

            display_result = (
                moving_result if scheme == "single" else click_result
            )
            if display_result is not None:
                last_gesture_seen = now

            kind = "none"
            if inside and not calibrating:
                if scheme == "single" and moving_result is not None:
                    kind = (
                        "down"
                        if moving_result["gesture"] == click_name
                        else "up"
                    )
                elif scheme == "double" and click_result is not None:
                    kind = (
                        "down"
                        if click_result["gesture"] == click_name
                        else "up"
                    )

            screen_w, screen_h = inject.screen_size()

            calibration = self.config.get("pointer.calibration", [])
            calibration_size = self.config.get(
                "pointer.calibration_size", []
            )
            calibrated = bool(
                isinstance(calibration, list)
                and len(calibration) == 9
                and isinstance(calibration_size, list)
                and len(calibration_size) == 2
                and int(calibration_size[0]) == screen_w
                and int(calibration_size[1]) == screen_h
            )
            active_calibration = calibration if calibrated else None

            if point is not None and inside and not calibrating:
                if mode == "absolute":
                    target = absolute.update(
                        point[0],
                        point[1],
                        now,
                        box,
                        screen_w,
                        screen_h,
                        calibration=active_calibration,
                    )
                    if target is not None:
                        inject.set_cursor(target[0], target[1])
                else:
                    if not self._inside:
                        pointer.reset()
                    dx, dy = pointer.update(
                        point[0], point[1], now, screen_w, screen_h
                    )
                    if dx or dy:
                        x, y = inject.get_cursor()
                        target_x = x + dx + self._carry_x
                        target_y = y + dy + self._carry_y
                        next_x = int(round(target_x))
                        next_y = int(round(target_y))
                        inject.set_cursor(next_x, next_y)
                        self._carry_x = max(
                            -1.0, min(1.0, target_x - next_x)
                        )
                        self._carry_y = max(
                            -1.0, min(1.0, target_y - next_y)
                        )

                        edge = max(8, int(screen_h * 0.05))
                        if dy < 0 and next_y <= edge:
                            direction = 1
                            self._scroll_acc += -dy
                        elif dy > 0 and next_y >= screen_h - edge:
                            direction = -1
                            self._scroll_acc += dy
                        else:
                            direction = 0
                            self._scroll_acc = 0.0

                        if direction and direction != self._scroll_dir:
                            self._scroll_acc = 0.0
                        if direction:
                            self._scroll_dir = direction
                            while self._scroll_acc >= 40.0:
                                inject.wheel(120 * self._scroll_dir)
                                self._scroll_acc -= 40.0

            self._inside = inside

            if not calibrating:
                cursor = inject.get_cursor()
                out = gestures.update(kind, now, cursor)
                if out.left_click:
                    inject.button("left", True)
                    inject.button("left", False)
                if out.right_click:
                    inject.button("right", True)
                    inject.button("right", False)
                if out.left_down:
                    inject.button("left", True)
                if out.left_up:
                    inject.button("left", False)

                if (
                    gestures.state != "Idle"
                    and kind == "none"
                    and now - last_gesture_seen > 0.3
                ):
                    cancel = gestures.cancel()
                    if cancel.left_up:
                        inject.button("left", False)

            elapsed = max(0.5, now - self._window_start)
            fps = self._frames / elapsed
            hit = 100.0 * self._hits / max(1, self._frames)

            if (
                self.preview is not None
                and self.preview.enabled
                and now - last_preview >= 0.1
            ):
                last_preview = now
                height0, width0 = frame.shape[:2]
                preview_w = 640
                preview_h = max(1, int(height0 * preview_w / max(1, width0)))
                if preview_h > 360:
                    preview_h = 360
                    preview_w = max(
                        1, int(width0 * preview_h / max(1, height0))
                    )
                preview_frame = cv2.resize(
                    frame,
                    (preview_w, preview_h),
                    interpolation=cv2.INTER_AREA,
                )
                pose.draw(preview_frame)
                hands.draw(preview_frame, moving_result)
                hands.draw(preview_frame, click_result)
                self.preview.publish(
                    preview_frame,
                    {
                        "active": self._active.is_set(),
                        "width": camera.width,
                        "height": camera.height,
                        "fps": fps,
                        "hit": hit,
                        "pose": pose.ready,
                        "hands": hands.ready,
                        "hand": moving_hand,
                        "scheme": scheme,
                        "tier": tier_state.name,
                        "tier_band": tier_state.band,
                        "tier_applied": tier_state.applied,
                        "hands_on": hands_on,
                        "palm_px": self._last_palm_px,
                        "drop": drop,
                        "mode": mode,
                        "inside": inside,
                        "calibrating": calibrating,
                        "calibrated": calibrated,
                        "palm_ok": point is not None,
                        "palm_x": point[0] if point else 0.0,
                        "palm_y": point[1] if point else 0.0,
                        "gain": float(
                            self.config.get("pointer.gain_relative", 1.8)
                        ),
                        "wrist_conf": wrist[2] if wrist else 0.0,
                        "gesture": (
                            display_result["gesture"]
                            if display_result
                            else "none"
                        ),
                        "state": gestures.state,
                        "pinch": bool(
                            display_result and display_result.get("pinch")
                        ),
                        "fist": bool(
                            display_result and display_result.get("fist")
                        ),
                        "pinch_ratio": float(
                            display_result.get("pinch_ratio", 0.0)
                            if display_result
                            else 0.0
                        ),
                        "fist_ratio": float(
                            display_result.get("fist_ratio", 0.0)
                            if display_result
                            else 0.0
                        ),
                    },
                )

            if now - self._last_fps_log >= 5.0:
                self._last_fps_log = now
                self.log(f"fps {fps:.1f} wrist {hit:.0f}%")
                self._frames = 0
                self._hits = 0
                self._window_start = now

        camera.close()
        pose.close()
        hands.close()
        inject.release_all()
