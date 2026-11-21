from __future__ import annotations

import threading
import time

import cv2

from . import inject
from .capture import Camera
from .pointer import RelativePointer
from .pose import PoseTracker


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

    def _run(self) -> None:
        camera = Camera(self.config, self.log)
        pose = PoseTracker(self.config, self.log)
        pointer = RelativePointer(self.config)
        last_hit = 0.0
        last_preview = 0.0
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
                self._frames = 0
                self._hits = 0
                self._carry_x = 0.0
                self._carry_y = 0.0
                last_hit = 0.0
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
            if wrist is not None:
                if last_hit and now - last_hit > 0.4:
                    pointer.reset()
                last_hit = now
                self._hits += 1
                screen_w, screen_h = inject.screen_size()
                dx, dy = pointer.update(
                    wrist[0], wrist[1], now, screen_w, screen_h
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
                self.preview.publish(
                    preview_frame,
                    {
                        "active": self._active.is_set(),
                        "width": camera.width,
                        "height": camera.height,
                        "fps": fps,
                        "hit": hit,
                        "pose": pose.ready,
                        "hand": str(
                            self.config.get("pointer.hand", "right")
                        ),
                        "gain": float(
                            self.config.get("pointer.gain_relative", 1.8)
                        ),
                        "wrist_conf": wrist[2] if wrist else 0.0,
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
        inject.release_all()
