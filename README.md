# airpoint

Camera-based air pointing for interactive flat panels.

A Windows utility that turns hand movement in front of a built-in camera into
mouse cursor control. It is designed for classroom interactive displays, where
the camera sits at the top of the screen and the user may sit or stand several
meters away.

## Status

Design phase. No runnable build yet.

## Planned capabilities

- Absolute ("virtual touch") and relative ("air mouse") pointing modes
- Auto tiering by hand size, detection rate, jitter, and latency
- Region-of-interest gating with pause / re-engage
- Left click, right click, drag, double click, and wheel
- Sit/stand profiles and offline replay for tuning

## Auto-update

On startup (about 3 seconds later) and every 6 hours, the app reads the
`manifest.json` from the `build-latest` release, compares versions, and
downloads the new executable through a list of GitHub mirrors:

1. `https://ghfast.top/`
2. `https://gh-proxy.com/`
3. `https://ghproxy.net/`
4. direct GitHub

The download is checked against the SHA-256 in the manifest and an Ed25519
signature before it replaces the running executable. Replacement happens only
when the app is idle; a failed start rolls back to the previous build. Set
`OPTICALINPUT_NO_UPDATE=1` to disable automatic updates.

## Build output

`OpticalInputSvc.exe` — PyInstaller onefile, no console window.

## Target environment

- Windows 10
- 1080p display
- Built-in RGB camera (4K preferred)
- Python prototype first, packaged as a single executable

## Notes

- Fully offline; no network access.
- No telemetry.
