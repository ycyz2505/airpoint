from __future__ import annotations

import time

import cv2


def limit_for_level(level: int):
    """Capture width cap for a governor level.

    L1 is the rung that exists to cut 4K decode cost, and decode cost only
    drops when the device is reopened at a smaller mode.
    """
    return 1920 if level >= 1 else None


# MJPG first: at 4K it is the only format most classroom cameras stream at
# 30 fps over USB 2. YUY2 is the fallback when MJPG is refused.
FORMATS = ("MJPG", "YUY2")

# DirectShow wants log2(seconds), so 1/100 s is about -6.6. Candidates go
# from short to shortest because a dark classroom is the more common failure.
EXPOSURES = (-6.0, -5.0, -7.0)

AUTO_EXPOSURE_MANUAL = 0.25
AUTO_EXPOSURE_AUTO = 0.75


def apply_exposure(cap, log=None) -> bool:
    """Try a short manual exposure; fall back to automatic.

    Manual exposure keeps the hand from smearing when the projector dims the
    room, but the property is unreliable across drivers, so it is only kept
    when it reads back.
    """
    try:
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, AUTO_EXPOSURE_MANUAL)
        for value in EXPOSURES:
            cap.set(cv2.CAP_PROP_EXPOSURE, value)
            got = cap.get(cv2.CAP_PROP_EXPOSURE)
            if abs(float(got) - value) < 0.5:
                if log is not None:
                    log(f"camera exposure manual {value:g}")
                return True
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, AUTO_EXPOSURE_AUTO)
    except Exception:
        pass
    if log is not None:
        log("camera exposure auto")
    return False


def backend_order(config) -> tuple[int, ...]:
    """OpenCV backends to try, best first, for the configured one."""
    return {
        "dshow": (cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY),
        "msmf": (cv2.CAP_MSMF, cv2.CAP_DSHOW, cv2.CAP_ANY),
        "any": (cv2.CAP_ANY, cv2.CAP_DSHOW, cv2.CAP_MSMF),
    }.get(
        str(config.get("camera.backend", "dshow")).lower(),
        (cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY),
    )


def targets_for(config, max_width=None) -> list[tuple[int, int]]:
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
    if max_width:
        allowed = [item for item in targets if item[0] <= int(max_width)]
        targets = allowed or [min(targets)]
    return targets


class Camera:
    def __init__(self, config, log, max_width=None):
        self.config = config
        self.log = log
        self.max_width = max_width
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

        targets = targets_for(self.config, self.max_width)
        fps = int(self.config.get("camera.fps", 30))

        order = backend_order(self.config)

        for index in indices:
            for backend in order:
                cap = cv2.VideoCapture(index, backend)
                if not cap.isOpened():
                    cap.release()
                    continue
                if self._negotiate(cap, index, backend, targets, fps):
                    return True
                cap.release()

        now = time.time()
        if now - self._last_error > 10.0:
            self._last_error = now
            self.log("camera open failed")
        return False

    def _negotiate(self, cap, index, backend, targets, fps) -> bool:
        """Walk formats then sizes until a frame actually arrives."""
        for fourcc in FORMATS:
            for width, height in targets:
                cap.set(
                    cv2.CAP_PROP_FOURCC,
                    cv2.VideoWriter_fourcc(*fourcc),
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
                apply_exposure(cap, self.log)
                self.log(
                    f"camera {index} backend {backend} {fourcc} "
                    f"{self.width}x{self.height} @{self.fps}"
                )
                return True
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
