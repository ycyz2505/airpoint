from __future__ import annotations

import csv
import threading
import time
from collections import deque
from pathlib import Path

COLUMNS = (
    "t", "frame", "tier", "band", "palm_px", "drop", "wrist_conf",
    "gesture", "state", "mode", "inside", "cursor_x", "cursor_y",
    "fps", "cpu", "rss_mb", "proc_ms",
)


class Diagnostics:
    """Ring buffer of per-frame features, exportable as one CSV file."""

    def __init__(self, capacity: int = 1800):
        self._rows = deque(maxlen=int(capacity))
        self._lock = threading.Lock()

    def add(self, row) -> None:
        with self._lock:
            self._rows.append(tuple(row))

    def __len__(self) -> int:
        with self._lock:
            return len(self._rows)

    def export(self, path: Path | None = None,
               folder: Path | None = None) -> Path:
        with self._lock:
            rows = list(self._rows)
        if path is None:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            base = folder or Path.cwd()
            path = Path(base) / f"diag-{stamp}.csv"
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(COLUMNS)
            writer.writerows(rows)
        return target
