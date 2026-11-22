from __future__ import annotations

import shutil
import time

import cv2

from .paths import data_dir, recordings_dir


class Recorder:
    def __init__(self, config, log):
        self.config = config
        self.log = log
        self.writer = None
        self.path = None
        self._size = None
        self._size_warned = False

    @property
    def active(self) -> bool:
        return self.writer is not None

    def start(self, frame, fps: float) -> bool:
        if self.writer is not None:
            return True
        minimum = float(self.config.get("recording.min_free_gb", 20.0))
        try:
            free_gb = shutil.disk_usage(data_dir()).free / (1024 ** 3)
        except OSError:
            free_gb = 0.0
        if free_gb < minimum:
            self.log(
                f"record refused: free {free_gb:.1f} GB < {minimum:.1f} GB"
            )
            return False

        height, width = frame.shape[:2]
        path = recordings_dir() / time.strftime("rec-%Y%m%d-%H%M%S.avi")
        fourcc = cv2.VideoWriter_fourcc(*"MJPG")
        writer = cv2.VideoWriter(
            str(path), fourcc, max(1.0, float(fps)), (width, height)
        )
        if not writer.isOpened():
            self.log(f"record writer failed: {path}")
            return False
        self.writer = writer
        self.path = path
        self._size = (width, height)
        self._size_warned = False
        self.log(f"record start: {path} {width}x{height} @{fps}")
        return True

    def write(self, frame) -> None:
        if self.writer is None:
            return
        height, width = frame.shape[:2]
        if self._size is not None and (width, height) != self._size:
            # Editing config.json mid-recording reopens the camera at the new
            # size; a mismatched frame would abort the writer, so drop it.
            if not self._size_warned:
                self._size_warned = True
                self.log("record: dropping frames at a different size")
            return
        self.writer.write(frame)

    def stop(self):
        path = self.path
        if self.writer is not None:
            self.writer.release()
            self.writer = None
            self.log(f"record stop: {path}")
        self.path = None
        self._size = None
        self._size_warned = False
        return path
