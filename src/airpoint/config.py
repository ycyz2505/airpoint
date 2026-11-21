from __future__ import annotations

import copy
import json
from pathlib import Path

from .paths import data_dir

DEFAULTS = {
    "schema_version": 1,
    "startup": {
        "silent": True,
        "first_run_wizard": True,
    },
    "security": {
        "password_hash": "",
        "password_salt": "",
        "lockout_seconds": 30,
    },
    "update": {
        "enabled": True,
        "channel": "stable",
        "check_on_start": True,
        "interval_hours": 6,
        "accelerators": [
            "https://ghfast.top/",
            "https://gh-proxy.com/",
            "https://ghproxy.net/",
            "",
        ],
        "require_signature": True,
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
    },
    "pose": {
        "model_complexity": 1,
        "min_confidence": 0.5,
    },
    "gesture": {
        "click": "auto",
        "scheme": "auto",
        "right_click_ms": 1000,
        "drag_threshold_px": 6,
        "double_click_ms": 350,
        "personalized": False,
        "pinch_enter": 0.45,
        "pinch_exit": 0.65,
        "fist_enter": 1.30,
        "fist_exit": 1.50,
    },
    "hands": {
        "model_complexity": 0,
        "min_confidence": 0.5,
    },
    "overlay": {
        "status_bar": True,
        "control_bar": True,
        "large_cursor": False,
        "presentation_safe_zone": False,
    },
    "hotkey": {
        "toggle_active": "ctrl+alt+shift+f9",
        "toggle_mode": "ctrl+alt+shift+f10",
        "emergency_stop": "f12",
    },
    "recording": {
        "manual": True,
        "prefer_4k": True,
        "min_free_gb": 20,
    },
    "performance": {
        "mode": "auto",
        "cpu_limit_pct": 20,
        "rss_limit_mb": 600,
        "low_power_fps": 15,
    },
    "logging": {
        "level": "info",
        "max_mb": 20,
    },
}


class Config:
    def __init__(self, path: Path | None = None):
        self.path = path or (data_dir() / "config.json")
        self.data = copy.deepcopy(DEFAULTS)
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(raw, dict):
            self._merge(self.data, raw)

    def _merge(self, base: dict, incoming: dict) -> None:
        for key, value in incoming.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                self._merge(base[key], value)
            else:
                base[key] = value

    def save(self) -> None:
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

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
