from __future__ import annotations

import math
import threading
import time
from collections import deque
from pathlib import Path

import cv2

from . import inject
from .capture import Camera, limit_for_level
from .diagnostics import Diagnostics
from .fallback import HandFallback
from .gestures import GestureEngine, TwoHandOpen
from .hands import HandTracker
from .paths import log_dir
from .personalize import Personalizer, features
from .pointer import (
    AbsolutePointer,
    RelativePointer,
    clamp_to_screen,
    map_point,
    safe_zone,
)
from .performance import Governor
from .pose import PoseTracker
from .recording import Recorder
from .selfcal import SelfCalibrator
from .tiers import TierEngine, capability, palm_px_1080
from .writing import StrokeSmoother


def _tracking_zone(box, margin: float = 0.2):
    """The mapping box grown by ``margin`` of its own size, clamped to frame.

    §8.5 keeps tracking alive a little past the box so stepping out and back
    in does not cost a re-detection, but stops the hand model entirely once
    the hand is well clear of it.
    """
    x0, y0, x1, y1 = [float(item) for item in box]
    dx = (x1 - x0) * margin
    dy = (y1 - y0) * margin
    return (
        max(0.0, x0 - dx),
        max(0.0, y0 - dy),
        min(1.0, x1 + dx),
        min(1.0, y1 + dy),
    )


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
        self._scroll_time = 0.0
        self._v_since = 0.0
        self._proc_ms = 0.0
        self._frame_index = 0
        self._cap_latched = False
        self._cap_trips = 0
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
        self._hold_progress = 0.0
        self._paused_notice = False
        self._writing = False
        self._two_hand = TwoHandOpen(config)
        self._fallback = HandFallback()
        self._hand_lost = False
        self._bar_request = 0
        self._notices = []
        self._inject_blocked = False
        self._blocked_log = 0.0
        self._mouse_takeover = False
        self._tier_name = ""
        self._blend_from = None
        self._blend_t0 = 0.0
        self._blend_ms = 150.0
        self._diag = Diagnostics(1800)
        self._crash_backoff = 0.5
        self._watchdog = None
        self._pressed: dict[str, float] = {}
        self.personalizer = Personalizer()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._supervise, daemon=True, name="airpoint-controller"
            )
            self._thread.start()
            self._watchdog = threading.Thread(
                target=self._watch, daemon=True, name="airpoint-watchdog"
            )
            self._watchdog.start()

    def _watch(self) -> None:
        """§9.5: a 20 Hz guard that does not depend on the frame loop.

        The frame loop already releases a held button after 5 s, but if the
        camera stalls, a model throws or the loop is wedged, no frame runs and
        a synthetic button would stay down. This thread only reads the clock
        and the press timestamp, so it keeps working when nothing else does.
        """
        while not self._stop.wait(0.05):
            if not self._pressed:
                continue
            now = time.time()
            with self._lock:
                stuck = [
                    name for name, since in self._pressed.items()
                    if now - since > 5.0
                ]
            if not stuck:
                continue
            self._release_all()
            try:
                self.log(
                    f"watchdog released {', '.join(stuck)} held over 5 s"
                )
            except Exception:
                pass

    def _supervise(self) -> None:
        """Keep the frame loop alive; a bad frame must not kill the service."""
        crashes = 0
        while not self._stop.is_set():
            try:
                self._run()
                return
            except Exception as exc:
                self._release_all()
                crashes += 1
                try:
                    self.log(f"frame loop crashed ({crashes}): {exc!r}")
                except Exception:
                    pass
                if crashes >= 5:
                    self._active.clear()
                    try:
                        self.log("frame loop gave up after 5 crashes")
                    except Exception:
                        pass
                    return
                if self._stop.wait(min(5.0, self._crash_backoff * crashes)):
                    return

    def stop(self) -> None:
        self._recording.clear()
        self._stop.set()
        self._active.clear()
        self._writing = False
        with self._lock:
            thread = self._thread
            watchdog = self._watchdog
            self._watchdog = None
        if thread is not None:
            thread.join(timeout=2.0)
        if watchdog is not None:
            watchdog.join(timeout=1.0)
        if self.recorder.active:
            self.recorder.stop()
        self._release_all()
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

    @property
    def hold_progress(self) -> float:
        return self._hold_progress

    @property
    def inject_blocked(self) -> bool:
        return self._inject_blocked

    @property
    def mouse_takeover(self) -> bool:
        """True while a real mouse has paused the air cursor (§9.6)."""
        return self._mouse_takeover

    def set_mouse_takeover(self, value: bool) -> None:
        self._mouse_takeover = bool(value)

    @property
    def hand_lost(self) -> bool:
        """True while §5.4 has frozen the cursor for want of a hand."""
        return self._hand_lost

    @property
    def tier(self) -> str:
        return self._tier.current

    def describe(self) -> str:
        """One read-only line for the tray menu."""
        mode = str(self.config.get("pointer.mode", "auto"))
        name = {"absolute": "绝对", "relative": "相对"}.get(mode, "自动")
        palm = int(self._last_palm_px or 0)
        state = "运行中" if self._active.is_set() else "已暂停"
        if self._recording.is_set():
            state = "录制中"
        elif self._mouse_takeover:
            state = "物理鼠标"
        elif self._hand_lost:
            state = "未看到手"
        score = capability(
            self._last_palm_px, self._tier.drop, self.config
        )
        return (
            f"{self._tier.current} 档 · {name} · {state} · "
            f"手掌 {palm}px · 能力 {score}"
        )

    def take_bar_request(self) -> bool:
        """True once per two-hand request to toggle the control bar."""
        with self._lock:
            if not self._bar_request:
                return False
            self._bar_request = 0
            return True

    def notice(self, text: str) -> None:
        """Queue one tray balloon for the UI thread to show (§5.6, §10.4)."""
        with self._lock:
            self._notices.append(text)
            del self._notices[:-4]

    def take_notice(self) -> str | None:
        with self._lock:
            return self._notices.pop(0) if self._notices else None

    def set_active(self, active: bool) -> None:
        if active:
            self._mouse_takeover = False
            self._reset.set()
            self._active.set()
            self.log("controller active")
        else:
            self._active.clear()
            self._writing = False
            self._release_all()
            self.log("controller paused")

    def reload(self) -> None:
        self._reload_camera.set()
        self._reset.set()

    def set_calibrating(self, value: bool) -> None:
        if value:
            self._calibrating.set()
            self._reset.set()
            self._release_all()
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
        self._release_all()
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

    def _personalize(self, result) -> None:
        """Let recorded samples break ties the fixed thresholds cannot."""
        if not result or not result.get("landmarks"):
            return
        if not bool(self.config.get("gesture.personalized", False)):
            return
        label, score = self.personalizer.best(
            features(result.get("landmarks"))
        )
        if not label or score < 0.7:
            return
        name = "open" if label == "point" else label
        result["personal"] = name
        if name != result.get("gesture"):
            result["gesture"] = name
            result["pinch"] = name == "pinch"
            result["fist"] = name == "fist"

    def _blend(self, x: float, y: float, now: float) -> tuple[float, float]:
        """Ease from the pre-tier-change cursor to the new mapping (§7.6)."""
        if self._blend_from is None:
            return x, y
        duration = self._blend_ms / 1000.0
        t = (now - self._blend_t0) / duration
        if t >= 1.0:
            self._blend_from = None
            return x, y
        t = max(0.0, t)
        ease = t * t * (3.0 - 2.0 * t)
        fx, fy = self._blend_from
        return fx + (x - fx) * ease, fy + (y - fy) * ease

    def _edge_scroll(self, y: float, dy: float, screen_h: int, now: float,
                     origin_y: float = 0.0) -> None:
        """Scroll while the hand keeps pushing into the top or bottom band."""
        if not bool(self.config.get("pointer.edge_scroll", True)):
            self._scroll_acc = 0.0
            return
        y = float(y) - float(origin_y)
        edge = max(8, int(screen_h * 0.05))
        if dy < 0 and y <= edge:
            direction = 1
            self._scroll_acc += -dy
        elif dy > 0 and y >= screen_h - edge:
            direction = -1
            self._scroll_acc += dy
        else:
            direction = 0
            self._scroll_acc = 0.0
        if direction and direction != self._scroll_dir:
            self._scroll_acc = 0.0
        if not direction:
            return
        if now - self._scroll_time < 0.3:
            return
        self._scroll_dir = direction
        if self._scroll_acc < 40.0:
            return
        self._scroll_acc = 0.0
        self._scroll_time = now
        inject.wheel(120 * self._scroll_dir)

    def _v_scroll(self, tier_name: str, landmarks, now: float) -> bool:
        """Near tiers can scroll with a V sign: index and middle extended."""
        if not bool(self.config.get("pointer.v_scroll", True)):
            return False
        if tier_name not in ("A", "B"):
            return False
        if not landmarks or len(landmarks) < 21:
            return False
        palm = max(1e-6, math.dist(landmarks[0], landmarks[9]))
        index = math.dist(landmarks[8], landmarks[0]) / palm
        middle = math.dist(landmarks[12], landmarks[0]) / palm
        ring = math.dist(landmarks[16], landmarks[0]) / palm
        pinky = math.dist(landmarks[20], landmarks[0]) / palm
        spread = math.dist(landmarks[8], landmarks[12]) / palm
        sign = (
            index > 1.8
            and middle > 1.8
            and ring < 1.5
            and pinky < 1.5
            and spread > 0.6
        )
        if not sign:
            self._v_since = 0.0
            return False
        if not self._v_since:
            self._v_since = now
            return False
        if now - self._v_since < 0.6 or now - self._scroll_time < 0.3:
            return False
        self._v_since = now
        self._scroll_time = now
        inject.wheel(-120 if landmarks[8][1] < landmarks[12][1] else 120)
        return True

    def _release_all(self) -> None:
        inject.release_all()
        with self._lock:
            self._pressed.clear()

    def _button(self, name: str, down: bool, now: float) -> None:
        """Send a button event, noting when Windows refused it.

        An elevated window blocks injected input under UIPI; SendInput then
        reports zero events, which is the only signal available.
        """
        if inject.button(name, down):
            self._inject_blocked = False
            with self._lock:
                if down:
                    self._pressed[name] = time.time()
                else:
                    self._pressed.pop(name, None)
            return
        self._inject_blocked = True
        if now - self._blocked_log > 10.0:
            self._blocked_log = now
            self.log("injection blocked (elevated window?)")

    def _learn_offset(self, mode, box, calibration, screen_w, screen_h,
                      cursor, origin=(0.0, 0.0)) -> None:
        if mode != "absolute" or not bool(
            self.config.get("pointer.self_calib", True)
        ):
            return
        if len(self._settle) < 4:
            return
        sx = sum(item[0] for item in self._settle) / len(self._settle)
        sy = sum(item[1] for item in self._settle) / len(self._settle)
        settled = map_point(
            sx, sy, box, screen_w, screen_h, calibration, origin
        )
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
            if self.config.reload_if_changed():
                self.log("config reloaded from disk")
                camera.close()
                self._reset.set()
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
                self._cap_latched = False
                self._cap_trips = 0
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
                self._hold_progress = 0.0
                self._paused_notice = False
                self._fallback.reset()
                self._hand_lost = False
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
                    self.notice("性能不足，已暂停")
                self._release_all()
                self._active.clear()
                self._writing = False
                continue
            if level >= 1 and self._frame_index % self.governor.frame_divisor():
                continue

            cap_width = limit_for_level(level)
            if cap_width and not self._cap_latched and camera.opened:
                if camera.width > cap_width:
                    # Reopening at the smaller mode is the only way decode
                    # cost actually drops; resizing after the read still pays
                    # for 4K.
                    self._cap_latched = True
                    self._cap_trips += 1
                    self.log(
                        f"capture capped to {cap_width}px "
                        f"({self.governor.name})"
                    )
                    camera.max_width = cap_width
                    camera.close()
                    continue
            elif level == 0 and self._cap_latched and self._cap_trips < 2:
                # Back to full quality once the ladder has climbed all the
                # way down. Two trips means 4K really is too heavy here, so
                # the cap stays for the rest of the session instead of
                # reopening the camera every few minutes.
                self._cap_latched = False
                camera.max_width = None
                camera.close()
                continue

            height, width = frame.shape[:2]
            frame_time = time.time()
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
            self._proc_ms = self._proc_ms * 0.9 + (now - frame_time) * 100.0
            calibrating = self._calibrating.is_set()

            # §6.2: a held-over result keeps the cursor alive for a second, but
            # it is not a fresh detection, so the drop rate must not count it.
            # A frame showing somebody else is not the user's miss either.
            if not pose.ignored:
                self._seen.append(
                    1 if wrist is not None and not pose.stale else 0
                )
            drop = 1.0 - (sum(self._seen) / len(self._seen))
            shoulder = pose.shoulder_width()
            pose_conf = wrist[2] if wrist else 0.0
            busy = gestures.state != "Idle"
            if self._active.is_set() and not calibrating:
                if self._two_hand.update(pose, now):
                    with self._lock:
                        self._bar_request = 1
                    self.log("two-hand pose: toggle control bar")
            else:
                self._two_hand.reset()
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
            if level >= 3:
                # L3/L4 drop the hand model, so the only point left is the
                # wrist; feeding that into an absolute map would jump the
                # cursor around. Fall back to relative wrist control.
                mode = "relative"
                hands_on = False
            click_hand = "left" if moving_hand == "right" else "right"

            box = self.config.get("pointer.box", [])
            if not isinstance(box, list) or len(box) != 4:
                box = []
            box_gate = _tracking_zone(box) if box else None

            moving_result = None
            click_result = None

            # §8.5: the hand model only runs while the hand is near the box,
            # so an arm resting far outside it costs nothing.
            near_box = True
            if box_gate is not None and wrist is not None:
                near_box = (
                    box_gate[0] <= wrist[0] <= box_gate[2]
                    and box_gate[1] <= wrist[1] <= box_gate[3]
                )

            if (
                hands_on
                and wrist is not None
                and near_box
                and self.governor.hands_allowed()
            ):
                moving_result = hands.process(
                    frame, wrist, pose.elbow(moving_hand), moving_hand,
                    bounds=box_gate,
                )
                if last_hit and now - last_hit > 0.4:
                    pointer.reset()
                last_hit = now
                self._hits += 1
                if moving_result and moving_result.get("palm"):
                    self._last_palm_px = palm_px_1080(
                        float(moving_result["palm"])
                    )
                self._personalize(moving_result)

            if hands_on and scheme == "double" and self.governor.hands_allowed():
                click_wrist = pose.wrist(click_hand)
                if click_wrist is not None and box_gate is not None:
                    if not (
                        box_gate[0] <= click_wrist[0] <= box_gate[2]
                        and box_gate[1] <= click_wrist[1] <= box_gate[3]
                    ):
                        click_wrist = None
                if click_wrist is not None:
                    if self._frame_index % 2 == 0 or last_click_result is None:
                        click_result = hands.process(
                            frame,
                            click_wrist,
                            pose.elbow(click_hand),
                            click_hand,
                            bounds=box_gate,
                        )
                        last_click_result = click_result
                    else:
                        click_result = last_click_result
                else:
                    click_result = None
                    last_click_result = None

            point = None
            palm = None
            if moving_result and moving_result.get("landmarks"):
                lm = moving_result["landmarks"]
                palm = (
                    sum(lm[i][0] for i in (0, 5, 9, 13, 17)) / 5.0,
                    sum(lm[i][1] for i in (0, 5, 9, 13, 17)) / 5.0,
                )
            # §5.4: palm -> held palm -> wrist -> frozen -> paused. A stale
            # pose hold-over is not a fresh wrist, so it is not offered here;
            # a wrist well outside the tracking zone is not offered either,
            # since §8.5 stops tracking past it.
            point, lost = self._fallback.update(
                palm, None if (pose.stale or not near_box) else wrist, now
            )
            if lost != self._hand_lost:
                self._hand_lost = lost
                if lost:
                    self.log("hand lost: cursor frozen, pausing")
                    self._release_all()
                    cancel = gestures.cancel()
                    if cancel.left_up:
                        inject.button("left", False)
                    self._held_since = 0.0
                    self._hold_progress = 0.0
                    self._writing = False
                else:
                    self.log("hand back: cursor resumes")

            if (
                self._active.is_set()
                and not calibrating
                and gestures.state == "Idle"
                and moving_result
            ):
                self._v_scroll(tier_state.name, moving_result.get("landmarks"), now)
            else:
                self._v_since = 0.0

            if mode == "auto":
                mode = "absolute" if box else "relative"
            if mode == "absolute" and not box:
                mode = "relative"

            if mode != self._last_mode:
                self._last_mode = mode
                pointer.reset()
                absolute.reset()
                gestures.reset()
                self._release_all()
                self._inside = False
                self._blend_from = None
                self._fallback.reset()
                self._hand_lost = False
                self._tier_name = tier_state.name
            elif tier_state.name != self._tier_name:
                if self._tier_name and mode == "absolute":
                    # §7.6: a tier change must not teleport the cursor; slide
                    # from where it is to the new mapping point.
                    self._blend_from = inject.get_cursor()
                    self._blend_t0 = now
                self._tier_name = tier_state.name

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

            screen_left, screen_top, screen_w, screen_h = inject.screen_rect(
                self.config.get("pointer.screen", "auto")
            )
            origin = (float(screen_left), float(screen_top))

            if kind != "none" and bool(
                self.config.get("overlay.presentation", False)
            ):
                cx, cy = inject.get_cursor()
                if safe_zone(cx, cy, screen_w, screen_h, origin=origin):
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
            # The homography is fitted in the display's own 0..w pixels, so
            # map_point() adds the origin in both branches.
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
                        origin=origin,
                    )
                    if target is not None:
                        previous = self._last_target
                        self._last_target = target
                        if not frozen:
                            cx, cy = self._selfcal.apply(target)
                            cx, cy = self._blend(cx, cy, now)
                            if writing:
                                cx, cy = self._stroke.update(cx, cy, True)
                            else:
                                self._stroke.reset()
                            inject.set_cursor(cx, cy)
                            # The cursor stops at the screen edge, so the
                            # scroll has to follow the hand's own push.
                            push = target[1] - previous[1] if previous else 0.0
                            self._edge_scroll(
                                cy, push * screen_h, screen_h, now,
                                origin[1],
                            )
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
                        target_x, target_y = clamp_to_screen(
                            target_x, target_y, screen_w, screen_h, origin
                        )
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

                        self._edge_scroll(next_y, dy, screen_h, now, origin[1])

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
                self._hold_progress = gestures.hold_progress(now)
                if out.left_click:
                    self._button("left", True, now)
                    self._button("left", False, now)
                    self._learn_offset(mode, box, active_calibration, screen_w,
                                       screen_h, cursor, origin)
                if out.double_click:
                    self._button("left", True, now)
                    self._button("left", False, now)
                    self._button("left", True, now)
                    self._button("left", False, now)
                if out.right_click:
                    self._button("right", True, now)
                    self._button("right", False, now)
                if out.left_down:
                    self._button("left", True, now)
                    self._held_since = now
                if out.left_up:
                    self._button("left", False, now)
                    self._held_since = 0.0

                if self._held_since and now - self._held_since > 5.0:
                    self._release_all()
                    gestures.cancel()
                    gestures.reset()
                    self._held_since = 0.0
                    self._hold_progress = 0.0
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
                f"{self._proc_ms:.1f}",
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
                        "proc_ms": self._proc_ms,
                        "palm_px": self._last_palm_px,
                        "drop": drop,
                        "takeover": self._mouse_takeover,
                        "hand_lost": self._hand_lost,
                        "presentation": bool(
                            self.config.get("overlay.presentation", False)
                        ),
                        "blocked": self._inject_blocked,
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
                        "capability": capability(
                            self._last_palm_px, drop, self.config
                        ),
                        "perf": self.governor.name,
                        "cpu": self.governor.cpu,
                        "rss": self.governor.rss_mb,
                        "writing": writing,
                        "presentation": bool(
                            self.config.get("overlay.presentation", False)
                        ),
                        "drop": drop,
                        "shoulder": shoulder,
                        "proc_ms": self._proc_ms,
                        "hand_bounds": (
                            (
                                min(p[0] for p in moving_result["landmarks"]),
                                min(p[1] for p in moving_result["landmarks"]),
                                max(p[0] for p in moving_result["landmarks"]),
                                max(p[1] for p in moving_result["landmarks"]),
                            )
                            if moving_result
                            and moving_result.get("landmarks")
                            else None
                        ),
                        "body": pose.bounds(),
                        "landmarks": (
                            moving_result.get("landmarks")
                            if moving_result
                            else None
                        ),
                        "personal": bool(
                            moving_result and moving_result.get("personal")
                        ),
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
        self._release_all()
