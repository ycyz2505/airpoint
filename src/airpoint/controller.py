from __future__ import annotations

import threading
import time

import cv2

from . import inject
from .capture import Camera
from .pointer import RelativePointer
from .pose import PoseTracker


class Controller:
    def __init__(self, config, log):
        self.config = config
        self.log = log
        self._thread = None
        self._stop = threading.Event()
        self._active = threading.Event()
        self._reset = threading.Event()
        self._reload_camera = threading.Event()
        self._lock = threading.Lock()
        self._last_fps_log = 0.0
        self._frames = 0
        self._hits = 0

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
                last_hit = 0.0

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
                    inject.set_cursor(x + dx, y + dy)

            if now - self._last_fps_log >= 5.0:
                self._last_fps_log = now
                fps = self._frames / 5.0
                hit = 100.0 * self._hits / max(1, self._frames)
                self.log(f"fps {fps:.1f} wrist {hit:.0f}%")
                self._frames = 0
                self._hits = 0

        camera.close()
        pose.close()
        inject.release_all()
