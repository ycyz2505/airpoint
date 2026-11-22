from __future__ import annotations

import copy
import json
import time
from pathlib import Path

from .paths import data_dir

SECTIONS = ("camera", "pointer", "pose", "hands", "gesture", "tier")


def _safe(name: str) -> str:
    cleaned = "".join(
        ch for ch in name if ch not in '\\/:*?"<>|'
    ).strip()
    return cleaned or "profile"


def _camera_label(config) -> str:
    device = str(config.get("camera.device", "auto")).strip()
    if not device or device.lower() == "auto":
        return "自动"
    return f"摄像头{device}"


def _resolution_label(config) -> str:
    try:
        width = int(config.get("camera.width", 0) or 0)
        height = int(config.get("camera.height", 0) or 0)
    except (TypeError, ValueError):
        return "auto"
    if width > 0 and height > 0:
        return f"{width}x{height}"
    return "auto"


def suggest_name(config) -> str:
    """Auto name for a new profile: seat, camera and resolution.

    §8.4 keys a seat profile on ``坐/站 + 摄像头设备名 + 分辨率``; the name is
    only a starting point and can be renamed afterwards.
    """
    seat = str(config.get("wizard.seat", "stand"))
    seat_label = "坐" if seat == "sit" else "站"
    return f"{seat_label}_{_camera_label(config)}_{_resolution_label(config)}"


def signature(config) -> dict:
    """What a seat profile was made for, so a moved panel can be noticed."""
    seat = str(config.get("wizard.seat", "stand"))
    return {
        "seat": "坐" if seat == "sit" else "站",
        "camera": str(config.get("camera.device", "auto")),
        "resolution": _resolution_label(config),
    }


def _stamp(sig: dict) -> str:
    return "|".join(
        str(sig.get(key, "")) for key in ("seat", "camera", "resolution")
    )


class ProfileStore:
    def __init__(self, config):
        self.config = config
        self.dir = data_dir() / "profiles"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        return self.dir / f"{_safe(name)}.json"

    def list(self):
        names = []
        for path in sorted(self.dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                names.append(payload.get("name") or path.stem)
            except (OSError, ValueError):
                continue
        return names

    def entries(self):
        """(name, created, used) for the profile page, newest use first."""
        found = []
        for path in self.dir.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            name = payload.get("name") or path.stem
            created = float(payload.get("created", 0) or 0)
            used = float(payload.get("used", 0) or 0)
            found.append((name, created, used))
        found.sort(key=lambda item: item[2] or item[1], reverse=True)
        return found

    def save(self, name: str) -> str:
        data = {
            key: copy.deepcopy(self.config.data.get(key, {}))
            for key in SECTIONS
        }
        now = time.time()
        created = now
        path = self._path(name)
        try:
            created = float(
                json.loads(path.read_text(encoding="utf-8")).get(
                    "created", now
                )
            )
        except (OSError, ValueError, TypeError):
            created = now
        payload = {
            "name": name,
            "created": created,
            "updated": now,
            "used": now,
            "signature": signature(self.config),
            "data": data,
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.config.set("profile.current", name)
        self.config.save()
        return name

    def load(self, name: str) -> None:
        path = self._path(name)
        payload = json.loads(path.read_text(encoding="utf-8"))
        data = payload.get("data", {})
        for key in SECTIONS:
            if key in data:
                self.config.data[key] = copy.deepcopy(data[key])
        saved = payload.get("signature")
        if isinstance(saved, dict) and saved.get("seat"):
            # The seat is part of the profile's identity, so it comes back
            # with it instead of staying wherever the last seat left it.
            self.config.set(
                "wizard.seat", "sit" if saved["seat"] == "坐" else "stand"
            )
        payload["used"] = time.time()
        try:
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass
        self.config.set("profile.current", payload.get("name", name))
        self.config.save()

    def delete(self, name: str) -> None:
        try:
            self._path(name).unlink()
        except OSError:
            pass
        if self.config.get("profile.current") == name:
            self.config.set("profile.current", "")
            self.config.save()

    def rename(self, old: str, new: str) -> None:
        payload = json.loads(
            self._path(old).read_text(encoding="utf-8")
        )
        payload["name"] = new
        self._path(new).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        try:
            self._path(old).unlink()
        except OSError:
            pass
        if self.config.get("profile.current") == old:
            self.config.set("profile.current", new)
            self.config.save()

    def load_last(self):
        name = str(self.config.get("profile.current", "")).strip()
        if not name:
            return None
        try:
            self.load(name)
            return name
        except (OSError, ValueError):
            return None

    def seat_changed(self) -> bool:
        """True when the panel now sits somewhere the profile was not made for.

        §8.4 asks for a "检测到新位置，重新画框？" prompt on a moved panel; a
        changed camera or resolution invalidates the saved box just as surely
        as a different seat does.
        """
        name = str(self.config.get("profile.current", "")).strip()
        if not name:
            return False
        try:
            payload = json.loads(
                self._path(name).read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return False
        saved = payload.get("signature")
        if not isinstance(saved, dict):
            return False
        return _stamp(saved) != _stamp(signature(self.config))
