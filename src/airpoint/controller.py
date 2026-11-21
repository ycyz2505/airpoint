from __future__ import annotations

import threading
import time
from collections import deque
from pathlib import Path

import cv2

from . import inject
from .capture import Camera
from .diagnostics import Diagnostics
from .gestures import GestureEngine
from .hands import HandTracker
from .paths import log_dir
from .pointer import AbsolutePointer, RelativePointer, map_point, safe_zone
from .performance import Governor
from .pose import PoseTracker
from .recording import Recorder
from .selfcal import SelfCalibrator
from .tiers import TierEngine
from .writing import StrokeSmoother


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
        self._recording = threading.Event()
        self.recorder = Recorder(config, log)
        self._record_path = None
        self.governor = Governor(config, log)
        self._stroke = StrokeSmoother(config)
        self._selfcal = SelfCalibrator(config.get("pointer.self_calib_offset", []))
        self._settle = deque(maxlen=8)
        self._last_target = None
        self._offset_dirty = False
        self._held_since = 0.0
        self._paused_notice = False
        self._writing = False
        self._diag = Diagnostics(1800)

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
        self._recording.clear()
        self._stop.set()
        self._active.clear()
        self._writing = False
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
        if self.recorder.active:
            self.recorder.stop()
        inject.release_all()
        if self._offset_dirty:
            try:
                self.config.save()
                self._offset_dirty = False
            except OSError as exc:
                self.log(f"self-calibration offset not saved: {exc}")
        self.log("controller stopped")

    @property
    def active(self) -> bool:
        return self._active.is_set()

    @property
    def writing(self) -> bool:
        return self._writing

    def set_active(self, active: bool) -> None:
        if active:
            self._reset.set()
            self._active.set()
            self.log("controller active")
        else:
            self._active.clear()
            self._writing = False
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

    @property
    def recording(self) -> bool:
        return self._recording.is_set()

    def start_recording(self) -> None:
        if self._recording.is_set() or self._calibrating.is_set():
            return
        self._active.clear()
        inject.release_all()
        self._reset.set()
        self._recording.set()
        self.log("recording requested")

    def stop_recording(self) -> None:
        if not self._recording.is_set():
            return
        self._recording.clear()
        self.log("recording stop requested")

    def export_diagnostics(self, path: Path | None = None) -> Path:
        """Write the last 30 s of per-frame features as CSV."""
        target = self._diag.export(path, folder=log_dir())
        self.log(f"diagnostics written: {target} ({len(self._diag)} rows)")
        return target

    def _learn_offset(self, mode, box, calibration, screen_w, screen_h,
                      cursor) -> None:
        if mode != "absolute" or not bool(
            self.config.get("pointer.self_calib", True)
        ):
            return
        if len(self._settle) < 4:
            return
        sx = sum(item[0] for item in self._settle) / len(self._settle)
        sy = sum(item[1] for item in self._settle) / len(self._settle)
        settled = map_point(sx, sy, box, screen_w, screen_h, calibration)
        if settled is None:
            return
        before = self._selfcal.offset
        self._selfcal.add(self._last_target or cursor, settled)
        if self._selfcal.offset != before:
            self.config.set("pointer.self_calib_offset", self._selfcal.offset)
            self._offset_dirty = True

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
            if not self._recording.is_set() and self.recorder.active:
                self._record_path = self.recorder.stop()
            if not self._active.is_set() and not self._recording.is_set():
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
                self._stroke.reset()
                self._settle.clear()
                self._last_target = None
                self._held_since = 0.0
                self._paused_notice = False
                self._window_start = time.time()

            if not camera.opened:
                if not camera.open():
                    time.sleep(2.0)
                    continue

            frame = camera.read()
            if frame is None:
                time.sleep(0.05)
                continue

            if self._recording.is_set():
                if not self.recorder.active:
                    if not self.recorder.start(frame, camera.fps or 30):
                        self._recording.clear()
                        self.log("recording failed")
                        continue
                self.recorder.write(frame)
                self._frames += 1
                now = time.time()
                elapsed = max(0.5, now - self._window_start)
                fps = self._frames / elapsed

                if (
                    self.preview is not None
                    and self.preview.enabled
                    and now - last_preview >= 0.1
                ):
                    last_preview = now
                    height0, width0 = frame.shape[:2]
                    preview_w = 640
                    preview_h = max(
                        1, int(height0 * preview_w / max(1, width0))
                    )
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
                    self.preview.publish(
                        preview_frame,
                        {
                            "active": False,
                            "recording": True,
                            "record_path": str(self.recorder.path or ""),
                            "width": camera.width,
                            "height": camera.height,
                            "fps": fps,
                            "hit": 0.0,
                            "pose": False,
                            "hands": False,
                            "hand": str(
                                self.config.get("pointer.hand", "right")
                            ),
                            "scheme": "record",
                            "tier": "-",
                            "tier_band": "-",
                            "tier_applied": False,
                            "hands_on": False,
                            "palm_px": 0.0,
                            "drop": 0.0,
                            "mode": "recording",
                            "inside": False,
                            "calibrating": False,
                            "calibrated": False,
                            "palm_ok": False,
                            "palm_x": 0.0,
                            "palm_y": 0.0,
                            "gain": 0.0,
                            "wrist_conf": 0.0,
                            "gesture": "recording",
                            "state": "Recording",
                            "pinch": False,
                            "fist": False,
                            "pinch_ratio": 0.0,
                            "fist_ratio": 0.0,
                        },
                    )

                if now - self._last_fps_log >= 5.0:
                    self._last_fps_log = now
                    self.log(f"record fps {fps:.1f}")
                    self._frames = 0
                    self._window_start = now
                continue

            self._frames += 1
            self._frame_index += 1

            level = self.governor.update(time.time())
            if level >= 5:
                if not self._paused_notice:
                    self._paused_notice = True
                    self.log("perf paused: resource limit")
                inject.release_all()
                self._active.clear()
                self._writing = False
                continue
            if level >= 1 and self._frame_index % self.governor.frame_divisor():
                continue

            height, width = frame.shape[:2]
            analysis_width = 1280 if level == 0 else 960
            if width > analysis_width:
                scale = analysis_width / float(width)
                small = cv2.resize(
                    frame,
                    (analysis_width, max(1, int(height * scale))),
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

            if hands_on and wrist is not None and self.governor.hands_allowed():
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

            if hands_on and scheme == "double" and self.governor.hands_allowed():
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

            if kind != "none" and bool(
                self.config.get("overlay.presentation", False)
            ):
                cx, cy = inject.get_cursor()
                if safe_zone(cx, cy, screen_w, screen_h):
                    kind = "none"

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

            writing = bool(self.config.get("pointer.write_mode", True)) and (
                gestures.state in ("Pending", "Dragging")
            )
            self._writing = writing

            if point is not None and inside and not calibrating:
                frozen = gestures.state == "Pending"
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
                        self._last_target = target
                        if not frozen:
                            cx, cy = self._selfcal.apply(target)
                            if writing:
                                cx, cy = self._stroke.update(cx, cy, True)
                            else:
                                self._stroke.reset()
                            inject.set_cursor(cx, cy)
                else:
                    if not self._inside:
                        pointer.reset()
                    dx, dy = pointer.update(
                        point[0], point[1], now, screen_w, screen_h
                    )
                    if frozen:
                        dx = dy = 0.0
                    if dx or dy:
                        x, y = inject.get_cursor()
                        target_x = x + dx + self._carry_x
                        target_y = y + dy + self._carry_y
                        next_x = int(round(target_x))
                        next_y = int(round(target_y))
                        if writing:
                            next_x, next_y = self._stroke.update(
                                next_x, next_y, True
                            )
                            inject.set_cursor(next_x, next_y)
                            self._carry_x = 0.0
                            self._carry_y = 0.0
                        else:
                            self._stroke.reset()
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

            if point is not None and inside and not calibrating:
                self._settle.append((point[0], point[1]))
            else:
                self._settle.clear()

            if not calibrating:
                cursor = inject.get_cursor()
                ref = cursor
                if (
                    mode == "absolute"
                    and self._last_target is not None
                    and point is not None
                ):
                    shifted = self._selfcal.apply(self._last_target)
                    ref = (int(round(shifted[0])), int(round(shifted[1])))
                out = gestures.update(kind, now, cursor, ref)
                if out.left_click:
                    inject.button("left", True)
                    inject.button("left", False)
                    self._learn_offset(mode, box, active_calibration, screen_w,
                                       screen_h, cursor)
                if out.double_click:
                    inject.button("left", True)
                    inject.button("left", False)
                    inject.button("left", True)
                    inject.button("left", False)
                if out.right_click:
                    inject.button("right", True)
                    inject.button("right", False)
                if out.left_down:
                    inject.button("left", True)
                    self._held_since = now
                if out.left_up:
                    inject.button("left", False)
                    self._held_since = 0.0

                if self._held_since and now - self._held_since > 5.0:
                    inject.release_all()
                    gestures.cancel()
                    gestures.reset()
                    self._held_since = 0.0
                    self._writing = False
                    self.log("watchdog released a button held over 5 s")

                if (
                    gestures.state != "Idle"
                    and kind == "none"
                    and now - last_gesture_seen > 0.3
                ):
                    cancel = gestures.cancel()
                    if cancel.left_up:
                        inject.button("left", False)
                        self._held_since = 0.0

            elapsed = max(0.5, now - self._window_start)
            fps = self._frames / elapsed
            hit = 100.0 * self._hits / max(1, self._frames)

            cursor_x, cursor_y = inject.get_cursor()
            display_gesture = (
                display_result["gesture"] if display_result else "none"
            )
            self._diag.add((
                f"{now:.3f}",
                self._frame_index,
                tier_state.name,
                tier_state.band,
                f"{self._last_palm_px:.1f}",
                f"{drop:.3f}",
                f"{wrist[2] if wrist else 0.0:.3f}",
                display_gesture,
                gestures.state,
                mode,
                int(inside),
                cursor_x,
                cursor_y,
                f"{fps:.1f}",
                f"{self.governor.cpu:.1f}",
                f"{self.governor.rss_mb:.0f}",
            ))

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
                if bool(self.config.get("overlay.status_bar", True)):
                    from .overlay import draw_status

                    draw_status(preview_frame, {
                        "active": self._active.is_set(),
                        "recording": False,
                        "mode": mode,
                        "tier": tier_state.name,
                        "fps": fps,
                        "perf": self.governor.name,
                        "presentation": bool(
                            self.config.get("overlay.presentation", False)
                        ),
                    })
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
                        "perf": self.governor.name,
                        "cpu": self.governor.cpu,
                        "rss": self.governor.rss_mb,
                        "writing": writing,
                        "presentation": bool(
                            self.config.get("overlay.presentation", False)
                        ),
                        "drop": drop,
                        "shoulder": shoulder,
                        "body": pose.bounds(),
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
