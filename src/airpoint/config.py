from __future__ import annotations

import copy
import json
from pathlib import Path

from .paths import data_dir

SCHEMA_VERSION = 2

# §13.1 lists config migration as a unit-tested layer. Every rename the
# project has made so far is recorded here, keyed by the schema version the
# file was written at, so an old config.json (or one synced from a classroom
# machine) is carried forward instead of silently falling back to defaults.
# ``(old, new)`` moves a value when the destination is still unset; ``None``
# drops a key that no longer means anything.
RENAMES = {
    1: (
        ("startup.silent", None),
        ("startup.first_run_wizard", "wizard.done"),
        ("update.channel", None),
        ("update.require_signature", None),
        ("overlay.large_cursor", "overlay.big_cursor"),
        ("overlay.presentation_safe_zone", "overlay.presentation"),
        ("recording.manual", None),
        ("recording.prefer_4k", "camera.prefer_4k"),
        ("performance.low_power_fps", None),
        ("logging.level", None),
    ),
}

# `startup.first_run_wizard` meant "show the wizard", `wizard.done` means the
# opposite, so that one value has to be flipped rather than copied.
INVERTED = {("startup.first_run_wizard", "wizard.done")}

DEFAULTS = {
    "schema_version": SCHEMA_VERSION,
    "profile": {
        "current": "",
    },
    "startup": {
        "auto_run": False,
    },
    "wizard": {
        "done": False,
        "seat": "stand",
        "profile": "",
    },
    "security": {
        "password_hash": "",
        "password_salt": "",
        "lockout_seconds": 30,
        "locked_until": 0,
    },
    "update": {
        "enabled": True,
        "check_on_start": True,
        "interval_hours": 6,
        "accelerators": [
            "https://ghfast.top/",
            "https://gh-proxy.com/",
            "https://ghproxy.net/",
            "",
        ],
        "allow_downgrade": False,
        "apply_when": "startup",
    },
    "camera": {
        "device": "auto",
        "backend": "dshow",
        "width": 1920,
        "height": 1080,
        "fps": 30,
        "prefer_4k": True,
    },
    "tier": {
        "mode": "auto",
        "lock": "",
        "apply": True,
        "apply_mode": True,
        "apply_gesture": False,
        "shoulder_a": 0.085,
        "shoulder_b": 0.060,
        "shoulder_c": 0.045,
        "shoulder_d": 0.034,
        "palm_good": 30.0,
        "palm_ok": 20.0,
        "drop_bad": 0.35,
        "downgrade_ms": 1000,
        "upgrade_ms": 8000,
    },
    "pointer": {
        "mode": "auto",
        "hand": "right",
        "gain_relative": 1.8,
        "deadzone_px": 0.5,
        "prediction_ms": 40,
        "max_step_px": 250,
        "mincutoff": 0.8,
        "beta": 0.3,
        "accel_enabled": True,
        "accel_v0": 0.8,
        "accel_max": 2.0,
        "rest_speed": 0.05,
        "active_on_start": False,
        "mouse_pause": True,
        "mouse_pause_sec": 3,
        "write_mode": True,
        "write_smoothing": 0.35,
        "write_min_step_px": 1.5,
        "edge_scroll": True,
        "v_scroll": True,
        "box": [],
        "calibration": [],
        "calibration_size": [],
        "self_calib": True,
        "self_calib_offset": [],
    },
    "pose": {
        "model_complexity": 1,
        "min_confidence": 0.5,
        "multi_lock": True,
    },
    "gesture": {
        "click": "auto",
        "scheme": "auto",
        "right_click_ms": 1000,
        "drag_threshold_px": 6,
        "double_click_ms": 350,
        "pinch_enter": 0.45,
        "pinch_exit": 0.65,
        "fist_enter": 1.30,
        "fist_exit": 1.50,
        "personalized": False,
    },
    "hands": {
        "model_complexity": 0,
        "min_confidence": 0.5,
    },
    "overlay": {
        "status_bar": True,
        "control_bar": False,
        "box": False,
        "hold_ring": True,
        "two_hand_bar": True,
        "two_hand_hold_ms": 1500,
        "tier_toast": True,
        "big_cursor": False,
        "big_cursor_size": 3,
        "magnifier": False,
        "magnifier_size": 220,
        "magnifier_zoom": 2.5,
        "presentation": False,
    },
    "hotkey": {
        "toggle_active": "ctrl+alt+shift+f9",
        "toggle_mode": "ctrl+alt+shift+f10",
        "control_bar": "ctrl+alt+shift+f8",
        "emergency_stop": "f12",
    },
    "recording": {
        "min_free_gb": 20,
    },
    "performance": {
        "mode": "auto",
        "cpu_limit_pct": 20,
        "rss_limit_mb": 600,
        "recover_seconds": 10,
    },
    "logging": {
        "max_mb": 0.5,
    },
}


def _pop_dotted(node: dict, dotted: str):
    parts = dotted.split(".")
    current = node
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            return None, False
        current = child
    if parts[-1] in current:
        return current.pop(parts[-1]), True
    return None, False


def _has_dotted(node: dict, dotted: str) -> bool:
    current = node
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    return True


def _set_dotted(node: dict, dotted: str, value) -> None:
    parts = dotted.split(".")
    current = node
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = value


def migrate(raw: dict, version: int) -> list:
    """Carry an older settings file forward in place (§12.5, §13.1).

    Returns the list of ``(old, new)`` pairs actually applied so the caller
    can log them; ``new`` is ``None`` for a key that was dropped.
    """
    applied = []
    for step in range(max(0, version), SCHEMA_VERSION):
        for old, new in RENAMES.get(step, ()):
            value, found = _pop_dotted(raw, old)
            if not found:
                continue
            if new is not None and not _has_dotted(raw, new):
                if (old, new) in INVERTED:
                    value = not bool(value)
                _set_dotted(raw, new, value)
            applied.append((old, new))
    return applied


class Config:
    def __init__(self, path: Path | None = None):
        self.path = path or (data_dir() / "config.json")
        self.data = copy.deepcopy(DEFAULTS)
        self._stamp = self._stat()
        self._watchers = []
        self.migrated = []
        self.load()

    def watch(self, callback) -> None:
        """Run ``callback`` after a successful reload from disk."""
        self._watchers.append(callback)

    def _stat(self):
        try:
            info = self.path.stat()
        except OSError:
            return None
        return info.st_mtime_ns, info.st_size

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        try:
            version = int(raw.get("schema_version", SCHEMA_VERSION))
        except (TypeError, ValueError):
            version = SCHEMA_VERSION
        if version > SCHEMA_VERSION:
            # A newer build wrote this. Its keys may mean something else
            # here, so keep the file for that build and start from defaults
            # rather than merging a shape we cannot interpret (§12.5).
            self._keep_copy()
            return
        self.migrated = migrate(raw, version)
        self._merge(self.data, raw)
        self.data["schema_version"] = SCHEMA_VERSION

    def _keep_copy(self) -> None:
        """Copy the current file to ``config.json.bak`` before overwriting it.

        §12.5: a downgrade or a bad migration must leave the previous
        settings recoverable. A copy, not a move, so the build that wrote the
        file still finds it if this one is never used again.
        """
        try:
            if self.path.exists():
                self.path.with_name("config.json.bak").write_bytes(
                    self.path.read_bytes()
                )
        except OSError:
            pass

    def reload_if_changed(self) -> bool:
        """Pick up edits made outside the app; keep the old data on error."""
        stamp = self._stat()
        if stamp is None or stamp == self._stamp:
            return False
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # A half-written file; leave the stamp alone and retry later.
            return False
        if not isinstance(raw, dict):
            return False
        self._stamp = stamp
        # Build the new tree off to the side and swap it in, so the
        # controller thread never sees a half-merged dict.
        merged = copy.deepcopy(self.data)
        try:
            version = int(raw.get("schema_version", SCHEMA_VERSION))
        except (TypeError, ValueError):
            version = SCHEMA_VERSION
        self.migrated = migrate(raw, version)
        self._merge(merged, raw)
        merged["schema_version"] = SCHEMA_VERSION
        self.data = merged
        for callback in self._watchers:
            try:
                callback()
            except Exception:
                pass
        return True

    def _merge(self, base: dict, incoming: dict) -> None:
        for key, value in incoming.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                self._merge(base[key], value)
            else:
                base[key] = value

    def save(self) -> None:
        self.data["schema_version"] = SCHEMA_VERSION
        self._keep_copy()
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self._stamp = self._stat()

    def get(self, dotted: str, default=None):
        node = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted: str, value) -> None:
        parts = dotted.split(".")
        node = self.data
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        node[parts[-1]] = value
