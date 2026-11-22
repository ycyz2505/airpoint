from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from .paths import exe_path, updates_dir
from .version import __version__

REPO = "ycyz2505/airpoint"
MANIFEST_URL = (
    f"https://github.com/{REPO}/releases/download/build-latest/manifest.json"
)
ACCELERATORS = (
    "https://ghfast.top/",
    "https://gh-proxy.com/",
    "https://ghproxy.net/",
    "",
)
USER_AGENT = "OpticalInputSvc-updater/1.0"
MANIFEST_TIMEOUT = 8
ASSET_TIMEOUT = 600
MANIFEST_MAX = 1024 * 1024
ASSET_MAX = 256 * 1024 * 1024


def _proxy_handler() -> urllib.request.ProxyHandler:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if not enabled:
                return urllib.request.ProxyHandler({})
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
    except Exception:
        return urllib.request.ProxyHandler({})

    if not server:
        return urllib.request.ProxyHandler({})
    if "=" in server:
        parts = dict(
            item.split("=", 1) for item in server.split(";") if "=" in item
        )
        http = parts.get("http") or parts.get("https") or ""
        https = parts.get("https") or parts.get("http") or ""
        return urllib.request.ProxyHandler({"http": http, "https": https})
    return urllib.request.ProxyHandler({"http": server, "https": server})


def _fetch(url: str, timeout: int, max_bytes: int) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"},
    )
    opener = urllib.request.build_opener(_proxy_handler())
    with opener.open(request, timeout=timeout) as response:
        status = getattr(response, "status", 200)
        if status < 200 or status >= 300:
            raise OSError(f"HTTP {status}")
        chunks = []
        total = 0
        while True:
            chunk = response.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise OSError("response too large")
            chunks.append(chunk)
    return b"".join(chunks)


def _sources(url: str, accelerators=None):
    for prefix in accelerators or ACCELERATORS:
        yield prefix + url


def _fetch_manifest(log, accelerators=None):
    errors = []
    for candidate in _sources(MANIFEST_URL, accelerators):
        try:
            raw = _fetch(candidate, MANIFEST_TIMEOUT, MANIFEST_MAX)
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data.get("airpoint"), dict):
                raise ValueError("manifest missing 'airpoint'")
            log(f"manifest ok: {candidate}")
            return data
        except Exception as exc:
            errors.append(f"{candidate}: {exc}")
            log(f"manifest fail: {candidate}: {exc}")
    raise OSError("; ".join(errors[-3:]))


def _version_tuple(value: str):
    parts = [int(item) for item in re.findall(r"\d+", value)[:4]]
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts)


def _is_newer(remote: str, current: str) -> bool:
    return _version_tuple(remote) > _version_tuple(current)


def _fetch_to_file(
    url: str, timeout: int, max_bytes: int, path: Path
) -> tuple[str, int]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"},
    )
    opener = urllib.request.build_opener(_proxy_handler())
    digest = hashlib.sha256()
    total = 0
    with opener.open(request, timeout=timeout) as response, path.open(
        "wb"
    ) as handle:
        status = getattr(response, "status", 200)
        if status < 200 or status >= 300:
            raise OSError(f"HTTP {status}")
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise OSError("response too large")
            digest.update(chunk)
            handle.write(chunk)
    return digest.hexdigest(), total


def _download(entry: dict, log, target: Path, accelerators=None) -> Path:
    url = str(entry.get("url") or "")
    if not url:
        raise ValueError("manifest missing url")
    expected = str(entry.get("sha256") or "").lower()
    expected_size = entry.get("size")
    errors = []
    part = target.with_name(target.stem + ".part")
    for candidate in _sources(url, accelerators):
        try:
            actual, size = _fetch_to_file(
                candidate, ASSET_TIMEOUT, ASSET_MAX, part
            )
            if expected and actual != expected:
                raise ValueError(f"sha256 mismatch ({actual})")
            if isinstance(expected_size, int) and expected_size:
                if size != expected_size:
                    raise ValueError("size mismatch")
            log(f"asset ok: {candidate}")
            return part
        except Exception as exc:
            errors.append(f"{candidate}: {exc}")
            log(f"asset fail: {candidate}: {exc}")
            try:
                part.unlink()
            except OSError:
                pass
    raise OSError("; ".join(errors[-3:]))


def _write_batch(target: Path, new: Path, pid: int, marker: Path) -> Path:
    path = updates_dir() / f"update-{pid}.bat"
    script = f"""@echo off
setlocal
:wait
tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul
if not errorlevel 1 (
  ping -n 2 127.0.0.1 >nul
  goto wait
)
if exist "{target}.bak" del /f /q "{target}.bak" >nul 2>nul
set n=0
:mv
set /a n+=1
if %n% gtr 60 exit /b 1
move /y "{target}" "{target}.bak" >nul 2>nul
if errorlevel 1 (
  ping -n 2 127.0.0.1 >nul
  goto mv
)
move /y "{new}" "{target}" >nul 2>nul
if errorlevel 1 (
  move /y "{target}.bak" "{target}" >nul 2>nul
  exit /b 1
)
start "" "{target}"
ping -n 31 127.0.0.1 >nul
if exist "{marker}" (
  del /f /q "{target}.bak" >nul 2>nul
) else (
  taskkill /IM "{target.name}" /F >nul 2>nul
  move /y "{target}.bak" "{target}" >nul 2>nul
  start "" "{target}"
)
del "%~f0"
"""
    # cmd.exe reads a .bat in the console ANSI codepage, not UTF-8, so a
    # non-ASCII install path would be mangled by an utf-8 write.
    try:
        path.write_text(script, encoding="mbcs")
    except (LookupError, UnicodeEncodeError):
        path.write_text(script, encoding="utf-8")
    return path


def _leave_cleanly() -> None:
    """Drop any held button before the process vanishes mid-press."""
    try:
        from . import inject

        inject.release_all()
    except Exception:
        pass
    os._exit(0)


def staged_path() -> Path:
    target = exe_path()
    return target.with_name(target.stem + "-new.exe")


def cleanup_leftovers(log=None) -> None:
    """Drop an unverified download left behind by an interrupted update.

    §12.5 step 6: a power cut mid-download leaves a ``.part`` that was never
    checked against the manifest, so it can never be trusted. The ``.bak`` is
    deliberately kept — it is the previous build, which is what rollback
    needs — and so is any ``update-*.bat``, since a batch file that is still
    running is read line by line from disk.
    """
    target = exe_path()
    if target.suffix.lower() != ".exe":
        # In a source checkout exe_path() is a .py file; the only thing this
        # would ever delete is the code itself.
        return
    part = target.with_name(target.stem + ".part")
    try:
        if part.exists():
            part.unlink()
            if log is not None:
                log(f"removed leftover {part.name}")
    except OSError:
        pass
    # The batch deletes the backup once it sees the started marker; if it was
    # killed first, the marker still proves this build runs, so the old copy
    # has done its job.
    backup = target.with_name(target.name + ".bak")
    if backup.exists() and (updates_dir() / f"started-{__version__}.ok").exists():
        try:
            backup.unlink()
            if log is not None:
                log(f"removed old build {backup.name}")
        except OSError:
            pass


def apply_staged(log) -> bool:
    """Apply a build that was downloaded but not installed last time."""
    if not getattr(sys, "frozen", False):
        return False
    new = staged_path()
    if not new.exists():
        return False
    target = exe_path()
    try:
        if new.stat().st_size < 1024 * 1024:
            new.unlink()
            return False
    except OSError:
        return False
    marker = updates_dir() / "started-pending.ok"
    try:
        if marker.exists():
            marker.unlink()
    except OSError:
        pass
    try:
        batch = _write_batch(target, new, os.getpid(), marker)
    except OSError as exc:
        log(f"staged apply failed: {exc}")
        return False
    log("applying staged build")
    subprocess.Popen(
        ["cmd.exe", "/C", str(batch)],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    time.sleep(0.5)
    _leave_cleanly()


def check_and_update(
    log, on_ready=None, delay: float = 3.0, apply_now: bool = True, config=None
) -> None:
    if not getattr(sys, "frozen", False):
        log("not frozen; update disabled")
        return
    if os.environ.get("OPTICALINPUT_NO_UPDATE"):
        log("update disabled by OPTICALINPUT_NO_UPDATE")
        return
    accelerators = None
    allow_downgrade = False
    if config is not None:
        raw = config.get("update.accelerators", None)
        if isinstance(raw, list) and raw:
            accelerators = [str(item) for item in raw]
        allow_downgrade = bool(config.get("update.allow_downgrade", False))
    if delay:
        time.sleep(delay)
    log(f"checking; current {__version__}")
    try:
        manifest = _fetch_manifest(log, accelerators)
        entry = manifest["airpoint"]
        remote = str(entry.get("version") or "")
        if not remote:
            log("manifest has no version")
            return
        newer = _is_newer(remote, __version__)
        if not newer and (remote == __version__ or not allow_downgrade):
            log(f"up to date ({remote})")
            return
        target = exe_path()
        part = _download(entry, log, target, accelerators)
        new = staged_path()
        part.replace(new)
        if not apply_now:
            log(f"staged {remote}; will apply on next start")
            return
        marker = updates_dir() / f"started-{remote}.ok"
        batch = _write_batch(target, new, os.getpid(), marker)
        log(f"staged {remote}; restarting")
        if on_ready is not None:
            on_ready()
        subprocess.Popen(
            ["cmd.exe", "/C", str(batch)],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        time.sleep(0.5)
        _leave_cleanly()
    except Exception as exc:
        log(f"update failed: {exc}")
