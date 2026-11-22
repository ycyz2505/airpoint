from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
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


WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY = 4
WINHTTP_ACCESS_TYPE_DEFAULT_PROXY = 0
WINHTTP_FLAG_SECURE = 0x00800000
WINHTTP_OPTION_REDIRECT_POLICY = 110
WINHTTP_OPTION_REDIRECT_POLICY_ALWAYS = 0
WINHTTP_QUERY_STATUS_CODE = 19
WINHTTP_QUERY_FLAG_NUMBER = 0x20000000
WINHTTP_ADDREQ_FLAG_ADD = 0x20000000
WINHTTP_HEADERS_NULL_TERMINATED = -1


def _winhttp_stream(url: str, timeout: int, max_bytes: int, sink) -> int:
    """GET ``url`` through WinHTTP, handing body chunks to ``sink``.

    §12.7 prefers WinHTTP because it honours the system proxy and follows
    the cross-domain redirects GitHub releases use, neither of which plain
    urllib does reliably on a classroom machine. Chunks are streamed so a
    200 MB exe never sits in memory.
    """
    import ctypes
    from ctypes import wintypes

    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise OSError(f"unsupported url {url}")
    secure = parts.scheme == "https"
    port = parts.port or (443 if secure else 80)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query

    dll = ctypes.WinDLL("winhttp", use_last_error=True)
    dll.WinHttpOpen.restype = ctypes.c_void_p
    dll.WinHttpOpen.argtypes = [
        ctypes.c_wchar_p,
        wintypes.DWORD,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        wintypes.DWORD,
    ]
    dll.WinHttpConnect.restype = ctypes.c_void_p
    dll.WinHttpConnect.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_ushort,
        wintypes.DWORD,
    ]
    dll.WinHttpOpenRequest.restype = ctypes.c_void_p
    dll.WinHttpOpenRequest.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    dll.WinHttpSetTimeouts.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
    ]
    dll.WinHttpSetOption.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    dll.WinHttpAddRequestHeaders.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    dll.WinHttpSendRequest.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_size_t,
    ]
    dll.WinHttpReceiveResponse.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    dll.WinHttpQueryHeaders.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_wchar_p,
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    dll.WinHttpReadData.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    dll.WinHttpCloseHandle.argtypes = [ctypes.c_void_p]

    def fail(what: str) -> OSError:
        return OSError(f"winhttp {what} ({ctypes.get_last_error()})")

    session = dll.WinHttpOpen(
        USER_AGENT,
        WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY,
        None,
        None,
        0,
    )
    if not session:
        session = dll.WinHttpOpen(
            USER_AGENT,
            WINHTTP_ACCESS_TYPE_DEFAULT_PROXY,
            None,
            None,
            0,
        )
    if not session:
        raise fail("open")
    request = None
    try:
        # Resolve/connect/send/receive, all per operation and in ms. A stall
        # during the download is what the receive value bounds, not the
        # whole transfer.
        dll.WinHttpSetTimeouts(session, 5000, 5000, timeout * 1000, timeout * 1000)
        connect = dll.WinHttpConnect(session, parts.hostname, port, 0)
        if not connect:
            raise fail("connect")
        try:
            request = dll.WinHttpOpenRequest(
                connect,
                "GET",
                path,
                None,
                None,
                None,
                WINHTTP_FLAG_SECURE if secure else 0,
            )
            if not request:
                raise fail("request")
            policy = wintypes.DWORD(WINHTTP_OPTION_REDIRECT_POLICY_ALWAYS)
            dll.WinHttpSetOption(
                request,
                WINHTTP_OPTION_REDIRECT_POLICY,
                ctypes.byref(policy),
                ctypes.sizeof(policy),
            )
            headers = (
                "Cache-Control: no-cache\r\n"
                f"User-Agent: {USER_AGENT}\r\n"
            )
            if not dll.WinHttpAddRequestHeaders(
                request,
                headers,
                WINHTTP_HEADERS_NULL_TERMINATED,
                WINHTTP_ADDREQ_FLAG_ADD,
            ):
                raise fail("headers")
            if not dll.WinHttpSendRequest(request, None, 0, None, 0, 0, 0):
                raise fail("send")
            if not dll.WinHttpReceiveResponse(request, None):
                raise fail("receive")
            status = wintypes.DWORD(0)
            size = wintypes.DWORD(ctypes.sizeof(status))
            if not dll.WinHttpQueryHeaders(
                request,
                WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                None,
                ctypes.byref(status),
                ctypes.byref(size),
                None,
            ):
                raise fail("status")
            if status.value < 200 or status.value >= 300:
                raise OSError(f"HTTP {status.value}")
            buffer = ctypes.create_string_buffer(65536)
            read = wintypes.DWORD(0)
            total = 0
            while True:
                if not dll.WinHttpReadData(
                    request, buffer, len(buffer), ctypes.byref(read)
                ):
                    raise fail("read")
                if read.value == 0:
                    break
                total += read.value
                if total > max_bytes:
                    raise OSError("response too large")
                sink(buffer.raw[: read.value])
            return total
        finally:
            if request:
                dll.WinHttpCloseHandle(request)
    finally:
        dll.WinHttpCloseHandle(session)


def _winhttp_fetch(url: str, timeout: int, max_bytes: int) -> bytes:
    chunks = []
    _winhttp_stream(url, timeout, max_bytes, chunks.append)
    return b"".join(chunks)


def _winhttp_to_file(
    url: str, timeout: int, max_bytes: int, path: Path
) -> tuple[str, int]:
    digest = hashlib.sha256()
    with path.open("wb") as handle:

        def sink(chunk: bytes) -> None:
            digest.update(chunk)
            handle.write(chunk)

        total = _winhttp_stream(url, timeout, max_bytes, sink)
    return digest.hexdigest(), total


def _fetch(url: str, timeout: int, max_bytes: int) -> bytes:
    try:
        return _winhttp_fetch(url, timeout, max_bytes)
    except Exception:
        pass
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
    try:
        return _winhttp_to_file(url, timeout, max_bytes, path)
    except Exception:
        pass
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


def state_path() -> Path:
    return updates_dir() / "update-state.json"


def read_state() -> dict:
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(**changes) -> None:
    data = read_state()
    data.update(changes)
    try:
        state_path().write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def take_pending_notice() -> str:
    """Version that was just installed, reported once after the restart."""
    data = read_state()
    version = str(data.get("pending_notice") or "")
    if version:
        write_state(pending_notice="")
    return version


def install_writable() -> bool:
    """§12.7: an exe in a read-only folder cannot be replaced, so don't try."""
    if not getattr(sys, "frozen", False):
        return False
    folder = exe_path().parent
    probe = folder / ".airpoint-write-test"
    try:
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


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
    if not install_writable():
        log("install folder is read-only; update disabled")
        write_state(
            last_check=time.time(),
            last_result="只读目录，已跳过更新",
        )
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
    write_state(last_check=time.time())
    try:
        manifest = _fetch_manifest(log, accelerators)
        entry = manifest["airpoint"]
        remote = str(entry.get("version") or "")
        if not remote:
            log("manifest has no version")
            write_state(last_result="清单里没有版本号")
            return
        newer = _is_newer(remote, __version__)
        if not newer and (remote == __version__ or not allow_downgrade):
            log(f"up to date ({remote})")
            write_state(last_result=f"已是最新（{remote}）")
            return
        target = exe_path()
        part = _download(entry, log, target, accelerators)
        new = staged_path()
        part.replace(new)
        if not apply_now:
            log(f"staged {remote}; will apply on next start")
            write_state(last_result=f"已下载 {remote}，下次启动安装")
            return
        marker = updates_dir() / f"started-{remote}.ok"
        batch = _write_batch(target, new, os.getpid(), marker)
        write_state(
            last_result=f"正在安装 {remote}",
            pending_notice=remote,
        )
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
        write_state(last_result=f"检查失败：{exc}")
