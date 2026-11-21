from __future__ import annotations

import time

import cv2


def targets_for(config) -> list[tuple[int, int]]:
    """Resolutions to try, best first."""
    try:
        wanted_w = int(config.get("camera.width", 0) or 0)
        wanted_h = int(config.get("camera.height", 0) or 0)
    except (TypeError, ValueError):
        wanted_w = wanted_h = 0
    targets = []
    explicit = (
        wanted_w > 0
        and wanted_h > 0
        and (wanted_w, wanted_h) != (1920, 1080)
    )
    if explicit:
        targets.append((wanted_w, wanted_h))
    if bool(config.get("camera.prefer_4k", True)):
        targets.append((3840, 2160))
    targets.append((1920, 1080))
    return targets


class Camera:
    def __init__(self, config, log):
        self.config = config
        self.log = log
        self.cap = None
        self.width = 0
        self.height = 0
        self.fps = 0
        self._last_error = 0.0

    @property
    def opened(self) -> bool:
        return self.cap is not None

    def open(self) -> bool:
        if self.cap is not None:
            return True

        device = str(self.config.get("camera.device", "auto"))
        if device.lower() == "auto":
            indices = [0, 1, 2, 3]
        else:
            try:
                indices = [int(device)]
            except ValueError:
                indices = [0]

        targets = targets_for(self.config)
        fps = int(self.config.get("camera.fps", 30))

        order = {
            "dshow": (cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY),
            "msmf": (cv2.CAP_MSMF, cv2.CAP_DSHOW, cv2.CAP_ANY),
            "any": (cv2.CAP_ANY, cv2.CAP_DSHOW, cv2.CAP_MSMF),
        }.get(
            str(self.config.get("camera.backend", "dshow")).lower(),
            (cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY),
        )

        for index in indices:
            for backend in order:
                cap = cv2.VideoCapture(index, backend)
                if not cap.isOpened():
                    cap.release()
                    continue
                for width, height in targets:
                    cap.set(
                        cv2.CAP_PROP_FOURCC,
                        cv2.VideoWriter_fourcc(*"MJPG"),
                    )
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                    cap.set(cv2.CAP_PROP_FPS, fps)
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        continue
                    actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                    actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    self.cap = cap
                    self.width = actual_w or frame.shape[1]
                    self.height = actual_h or frame.shape[0]
                    self.fps = int(cap.get(cv2.CAP_PROP_FPS) or fps)
                    self.log(
                        f"camera {index} backend {backend} "
                        f"{self.width}x{self.height} @{self.fps}"
                    )
                    return True
                cap.release()

        now = time.time()
        if now - self._last_error > 10.0:
            self._last_error = now
            self.log("camera open failed")
        return False

    def read(self):
        if self.cap is None:
            return None
        ok, frame = self.cap.read()
        if not ok or frame is None:
            self.close()
            return None
        return frame

    def close(self) -> None:
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None
