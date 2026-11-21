#!/usr/bin/env python3
"""Build the update manifest for the current executable."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def canonical(obj) -> bytes:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--tag", default="build-latest")
    parser.add_argument("--out", default="manifest.json")
    parser.add_argument("--notes", default="")
    args = parser.parse_args()

    exe = Path(args.exe)
    data = exe.read_bytes()
    entry = {
        "version": args.version,
        "url": (
            f"https://github.com/{args.repo}/releases/download/"
            f"{args.tag}/{exe.name}"
        ),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "min_version": "0.0.0",
        "published_at": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "notes": args.notes,
    }

    signature = ""
    key_b64 = os.environ.get("UPDATE_SIGNING_KEY", "").strip()
    if key_b64:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )

        key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(key_b64))
        signature = base64.b64encode(key.sign(canonical(entry))).decode("ascii")

    manifest = {
        "schema": 1,
        "channel": "stable",
        "airpoint": entry,
        "signature": signature,
    }
    Path(args.out).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"wrote {args.out}: {entry['version']} "
        f"{entry['sha256']} signature={'yes' if signature else 'no'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
