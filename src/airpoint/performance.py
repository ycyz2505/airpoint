from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


class FILETIME(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", wintypes.DWORD),
        ("dwHighDateTime", wintypes.DWORD),
    ]


def _filetime_value(item: FILETIME) -> int:
    return (item.dwHighDateTime << 32) | item.dwLowDateTime


class Governor:
    """Watches this process and walks a six-step degradation ladder.

    L0 full quality, L1 lower capture resolution, L2 halved analysis rate,
    L3 pose only (no hand model), L4 quarter rate wrist only, L5 pause.
    """

    LEVELS = (
        "L0 全质量",
        "L1 1080p",
        "L2 降帧",
        "L3 仅姿态",
        "L4 仅手腕",
        "L5 暂停",
    )

    SAMPLE_SECONDS = 1.0
    WINDOW_SECONDS = 5.0

    def __init__(self, config, log):
        self.config = config
        self.log = log
        self.level = 0
        self.cpu = 0.0
        self.rss_mb = 0.0
        self._last_sample = 0.0
        self._last_cpu = None
        self._low_since = None
        self._over_since = None
        self._last_change = 0.0
        self._samples: list[tuple[float, float, float]] = []
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._psapi = ctypes.WinDLL("psapi", use_last_error=True)
        self._kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        self._kernel32.GetProcessTimes.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
        ]
        self._psapi.GetProcessMemoryInfo.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
            wintypes.DWORD,
        ]
        self._cpus = max(1, os.cpu_count() or 1)
        self._process = self._kernel32.GetCurrentProcess()

    @property
    def name(self) -> str:
        return self.LEVELS[self.level]

    def _sample(self, now: float) -> None:
        creation = FILETIME()
        exit_time = FILETIME()
        kernel = FILETIME()
        user = FILETIME()
        if self._kernel32.GetProcessTimes(
            self._process,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            total = _filetime_value(kernel) + _filetime_value(user)
            if self._last_cpu is not None and self._last_sample:
                elapsed = max(1e-6, now - self._last_sample)
                delta = max(0, total - self._last_cpu) / 1e7
                self.cpu = 100.0 * delta / elapsed / self._cpus
            self._last_cpu = total
        self._last_sample = now

        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
        if self._psapi.GetProcessMemoryInfo(
            self._process, ctypes.byref(counters), counters.cb
        ):
            self.rss_mb = counters.WorkingSetSize / (1024 * 1024)

    def update(self, now: float) -> int:
        mode = str(self.config.get("performance.mode", "auto")).lower()
        if mode == "high":
            self.level = 0
            return self.level
        if mode == "low" and self.level < 3:
            self.level = 3
            return self.level

        if now - self._last_sample < self.SAMPLE_SECONDS:
            return self.level
        self._sample(now)

        self._samples.append((now, self.cpu, self.rss_mb))
        while self._samples and now - self._samples[0][0] > self.WINDOW_SECONDS:
            self._samples.pop(0)
        count = max(1, len(self._samples))
        cpu_avg = sum(item[1] for item in self._samples) / count
        rss_avg = sum(item[2] for item in self._samples) / count

        cpu_limit = float(self.config.get("performance.cpu_limit_pct", 20))
        rss_limit = float(self.config.get("performance.rss_limit_mb", 600))

        # §5.6: the condition has to hold for a full 5 s window before the
        # ladder moves, so a single spike does not cost a rung, and a rung
        # never moves twice inside one window. The 5 s averages above are
        # only what gets logged and shown.
        overloaded = self.cpu > cpu_limit or self.rss_mb > rss_limit
        if overloaded and self.level < len(self.LEVELS) - 1:
            if self._over_since is None:
                self._over_since = now
            if (
                now - self._over_since >= self.WINDOW_SECONDS
                and now - self._last_change >= self.WINDOW_SECONDS
            ):
                self.level += 1
                self._over_since = None
                self._low_since = None
                self._last_change = now
                self.log(
                    f"perf {self.name} cpu {cpu_avg:.0f}% rss {rss_avg:.0f}MB"
                )
            return self.level
        self._over_since = None

        comfortable = (
            self.cpu < cpu_limit * 0.6 and self.rss_mb < rss_limit * 0.7
        )
        if comfortable and self.level > 0:
            if self._low_since is None:
                self._low_since = now
            elif (
                now - self._low_since >= float(
                    self.config.get("performance.recover_seconds", 10)
                )
                and now - self._last_change >= self.WINDOW_SECONDS
            ):
                self.level -= 1
                self._low_since = None
                self._last_change = now
                self.log(f"perf {self.name}")
        else:
            self._low_since = None
        return self.level

    def frame_divisor(self) -> int:
        if self.level >= 4:
            return 3
        if self.level >= 2:
            return 2
        return 1

    def hands_allowed(self) -> bool:
        return self.level < 3
