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

    def save(self, name: str) -> str:
        data = {
            key: copy.deepcopy(self.config.data.get(key, {}))
            for key in SECTIONS
        }
        payload = {
            "name": name,
            "updated": time.time(),
            "data": data,
        }
        self._path(name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.config.set("profile.current", name)
        self.config.save()
        return name

    def load(self, name: str) -> None:
        payload = json.loads(
            self._path(name).read_text(encoding="utf-8")
        )
        data = payload.get("data", {})
        for key in SECTIONS:
            if key in data:
                self.config.data[key] = copy.deepcopy(data[key])
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
