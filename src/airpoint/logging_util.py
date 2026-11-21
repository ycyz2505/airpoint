from __future__ import annotations

import time

from .paths import log_dir


def make_logger(name: str, config=None):
    path = log_dir() / f"{name}.log"
    limit = 512 * 1024
    if config is not None:
        try:
            limit = int(float(config.get("logging.max_mb", 0.5)) * 1024 * 1024)
        except (TypeError, ValueError):
            limit = 512 * 1024
    limit = max(64 * 1024, limit)

    def log(message: str) -> None:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {message}\n"
        try:
            if path.exists() and path.stat().st_size > limit:
                path.unlink()
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
        except OSError:
            pass

    return log
