from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

ITERATIONS = 200_000


class Security:
    def __init__(self, config):
        self.config = config
        self.failures = 0
        try:
            self.locked_until = float(config.get("security.locked_until", 0) or 0)
        except (TypeError, ValueError):
            self.locked_until = 0.0

    def _persist_lockout(self) -> None:
        # Kept on disk so restarting the tray does not hand back five fresh
        # guesses to someone who cannot read the password.
        try:
            self.config.set("security.locked_until", round(self.locked_until, 3))
            self.config.save()
        except Exception:
            pass

    @property
    def has_password(self) -> bool:
        return bool(self.config.get("security.password_hash")) and bool(
            self.config.get("security.password_salt")
        )

    def set_password(self, password: str) -> None:
        salt = os.urandom(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, ITERATIONS, 32
        )
        self.config.set(
            "security.password_salt", base64.b64encode(salt).decode("ascii")
        )
        self.config.set(
            "security.password_hash", base64.b64encode(digest).decode("ascii")
        )
        self.config.set("security.locked_until", 0)
        self.config.save()
        self.failures = 0
        self.locked_until = 0.0

    def check(self, password: str) -> bool:
        if not self.has_password:
            return True
        now = time.time()
        if now < self.locked_until:
            return False
        try:
            salt = base64.b64decode(self.config.get("security.password_salt"))
            expected = base64.b64decode(self.config.get("security.password_hash"))
        except Exception:
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, ITERATIONS, 32
        )
        if hmac.compare_digest(actual, expected):
            self.failures = 0
            return True
        self.failures += 1
        if self.failures >= 5:
            self.locked_until = now + int(
                self.config.get("security.lockout_seconds", 30)
            )
            self.failures = 0
            self._persist_lockout()
        return False

    def locked_for(self) -> int:
        return max(0, int(self.locked_until - time.time()))
