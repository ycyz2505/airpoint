#!/usr/bin/env python3
"""Build the update manifest for the current executable."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


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

    manifest = {
        "schema": 1,
        "channel": "stable",
        "airpoint": entry,
    }
    Path(args.out).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {args.out}: {entry['version']} {entry['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
