# airpoint

Camera-based air pointing for interactive flat panels.

A Windows utility that turns hand movement in front of a built-in camera into
mouse cursor control. It is designed for classroom interactive displays, where
the camera sits at the top of the screen and the user may sit or stand several
meters away.

## Status

Early prototype: silent tray, password-protected settings, self-update, and a
pose-based relative cursor (right or left wrist).

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
signature, then the app restarts itself to apply it. A failed start rolls back
to the previous build. Set `OPTICALINPUT_NO_UPDATE=1` to disable automatic
updates.

## Current build

- Camera capture at 1080p or 4K; automatic device and backend selection.
- MediaPipe Pose wrist tracking; right or left hand.
- Relative cursor with One Euro smoothing and short prediction.
- Tray toggle; `Ctrl+Alt+Shift+F9` toggle, `F12` emergency stop.
- Preview/diagnostics window with the camera image, pose skeleton, wrist hit
  rate, and live gain/smoothing/prediction sliders.
- Left click by pinch or fist; right click by holding 1 s; drag, double click,
  and edge wheel scrolling.
- Single-hand and two-hand schemes (right hand moves, left hand clicks).
- Hand landmarks and gesture state in the preview window.
- Absolute "virtual touch" mode: draw a box in the preview; the palm maps to
  the screen inside the box. The same box acts as an active zone in relative
  mode; outside it, the cursor freezes.
- Speed-adaptive gain and sub-pixel cursor accumulation for slow movement.
- All mouse buttons are released on pause, exit, or error.

## Settings

The tray menu's **设置…** and **退出** require the management password set on
first run. Settings cover update, startup, performance, camera, tiers,
pointer, gestures, overlay, hotkeys, recording, and about. The password is
stored as a PBKDF2-HMAC-SHA256 hash; five failures lock the menu for 30
seconds.

## Build

GitHub Actions builds and publishes on every push to `main`; no local build is
required. The workflow is `.github/workflows/build.yml`. It produces
`OpticalInputSvc.exe` (PyInstaller onefile, no console) and `manifest.json`,
updates the `build-latest` release, and creates a versioned release for `v*`
tags.

## Target environment

- Windows 10
- 1080p display
- Built-in RGB camera (4K preferred)
- Python prototype first, packaged as a single executable

## Notes

- Fully offline; no network access.
- No telemetry.
