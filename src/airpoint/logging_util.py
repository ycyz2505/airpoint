from __future__ import annotations

import time

from .paths import log_dir


def make_logger(name: str):
    path = log_dir() / f"{name}.log"

    def log(message: str) -> None:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {message}\n"
        try:
            if path.exists() and path.stat().st_size > 512 * 1024:
                path.unlink()
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
        except OSError:
            pass

    return log
