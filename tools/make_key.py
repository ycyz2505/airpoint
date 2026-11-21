#!/usr/bin/env python3
"""Generate an Ed25519 update signing key pair.

Run this once on your own machine, then:

1. Put the printed private key into the repository secret
   ``UPDATE_SIGNING_KEY``.
2. Paste the printed public key into ``src/airpoint/update_pubkey.py``.
3. Commit the public key change.

Without a public key in the source, the updater accepts unsigned manifests.
"""

from __future__ import annotations

import argparse
import base64
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out", help="write the private key to this file instead of stdout"
    )
    args = parser.parse_args()

    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )
    except ImportError:
        print("cryptography is not installed", file=sys.stderr)
        return 1

    key = Ed25519PrivateKey.generate()
    private_raw = key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_raw = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    private_b64 = base64.b64encode(private_raw).decode("ascii")
    public_b64 = base64.b64encode(public_raw).decode("ascii")

    if args.out:
        from pathlib import Path

        target = Path(args.out)
        target.write_text(private_b64 + "\n", encoding="ascii")
        print(f"private key written to {target}")
    else:
        print("private key (secret UPDATE_SIGNING_KEY):")
        print(private_b64)
    print()
    print("public key (src/airpoint/update_pubkey.py):")
    print(f'PUBLIC_KEY_B64 = "{public_b64}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
