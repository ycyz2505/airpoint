# airpoint

Camera-based air pointing for interactive flat panels.

A Windows utility that turns hand movement in front of a built-in camera into
mouse cursor control. It is designed for classroom interactive displays, where
the camera sits at the top of the screen and the user may sit or stand several
meters away.

## Status

Working build: silent tray, password-protected settings, self-update, pose and
hand tracking, absolute and relative pointing, A–E tiering, calibration,
profiles, recording, replay, magnifier, presentation mode, diagnostics export,
survey mode, a first-run wizard, a suggested mapping box, online
self-calibration, and double-click detection.

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

The download is checked against the SHA-256 and size in the manifest, then the
app restarts itself to apply it. A failed start rolls back to the previous
build. `update.apply_when` chooses between installing at startup, staging until
the next start, or only on request. Set `OPTICALINPUT_NO_UPDATE=1` to disable
automatic updates.

## Current build

- Camera capture at 1080p or 4K; automatic device and backend selection.
- MediaPipe Pose wrist tracking; right or left hand.
- Relative cursor with One Euro smoothing and short prediction.
- Tray toggle; `Ctrl+Alt+Shift+F9` toggle, `Ctrl+Alt+Shift+F10` cycle mode,
  `Ctrl+Alt+Shift+F8` control bar, `F12` emergency stop.
- Preview/diagnostics window with the camera image, pose skeleton, wrist hit
  rate, and live gain/smoothing/prediction sliders.
- Left click by pinch or fist; right click by holding 1 s; drag, double click,
  and edge wheel scrolling.
- Single-hand and two-hand schemes (right hand moves, left hand clicks).
- Hand landmarks and gesture state in the preview window.
- Absolute "virtual touch" mode: draw a box in the preview; the palm maps to
  the screen inside the box. The same box acts as an active zone in relative
  mode; outside it, the cursor freezes.
- Five-point calibration for absolute mode: full-screen targets, hold still at
  each one, and a homography replaces the box affine mapping.
- Automatic A–E tiering from shoulder width plus palm size and hand drop rate,
  with downgrade/upgrade hysteresis. Near picks absolute + single hand; far
  picks relative + two hands and drops hand gestures when the hand is too
  small. Manual lock and advisory-only mode are available.
- Seat profiles: save and load the box, five-point calibration, feel settings,
  tier lock, gestures, and camera settings. The last profile loads at startup.
- Physical mouse takeover: moving the real mouse pauses the air cursor and it
  resumes after 3 seconds of mouse inactivity; a hotkey or tray toggle cancels
  the automatic resume.
- Manual recording from the tray: the controller stops cursor work and writes
  the raw camera stream to `recordings/rec-*.avi` (MJPG) after checking free
  disk space.
- Start with Windows: the **开机自动运行** switch writes or removes the
  `HKCU\...\CurrentVersion\Run` entry, and the app reconciles it on launch so
  a stale entry never survives a moved executable.
- Offline replay: `python -m airpoint.replay clip.avi --csv metrics.csv`
  re-runs pose, hands, pointer, gestures, and tiering over a recording and
  reports hit rate, palm size, cursor path, and tier time, so feel can be
  tuned without the classroom. The packaged executable does the same with
  `OpticalInputSvc.exe --replay clip.avi`.
- Handwriting mode: while the button is held the cursor is filtered harder so
  strokes drawn in the Seewo whiteboard come out as lines instead of jitter.
- Control bar: a small always-on-top bar with activate, click, mode, preview,
  and settings, reachable by the air cursor at six metres.
- Performance governor: six levels from full quality down to pause, chosen
  from the process CPU and working set, with a slow recovery back up.
- Tiering now only changes the mapping mode by default; gesture scheme changes
  are opt-in so the interaction does not flip underneath the user.
- Speed-adaptive gain and sub-pixel cursor accumulation for slow movement.
- All mouse buttons are released on pause, exit, or error.
- Writing magnifier: a small always-on-top, click-through window follows the
  cursor while a button is held and shows a 2.5x (adjustable) view of the
  screen area under it, so the stroke lands where intended.
- Presentation mode: a 5 % safe band around the screen edge ignores clicks so
  the cursor cannot fire a tool by accident; the status line shows `DEMO` and
  the control bar stays hidden.
- Diagnostics export: the preview window's **导出诊断** button writes the last
  30 seconds of per-frame features (tier, band, palm size, drop rate, gesture
  state, cursor, fps, CPU, memory) as a CSV into the data directory.
- Survey mode: a fullscreen camera view with large statistics and a 30-second
  test that measures shoulder width, palm size, drop rate, and confidence, then
  suggests a tier (with the reason) and can lock it in one click.
- Tray additions: **首次向导…**, **勘测模式…**, **重新标定…**, and **打开数据目录**.
- Mapping-box suggestion: the preview's **建议框** button takes the pose body
  bounding rect, pads it, and snaps it to the screen aspect, so the box can be
  set from six metres without drawing it by hand.
- First-run wizard: seven steps (camera, hand visible, seat, active area,
  calibration, gestures, save) with a live camera view, a 10-second measurement
  that suggests a tier, one-click box creation, calibration, and a seat profile.
  It opens automatically after the management password is first set.
- Double click: two taps within 350 ms at nearly the same spot send a
  double-click instead of two single clicks.
- Online self-calibration in absolute mode: while the hand rests before a
  click, the difference between where it mapped and where it settled nudges a
  bounded screen-space offset (2 px per sample, 60 px total), so small pointing
  bias disappears with use. Toggle with **点击自动校正偏移**.
- Gesture press freezes the cursor: drag distance is measured from the hand, so
  a long press stays a right click instead of drifting into a drag.
- Watchdog: a held button is released after 5 seconds even if the hand is lost.
- Long-press ring: a small ring under the cursor fills while a press builds, so
  a right click is not a surprise; toggle with **长按进度环**.
- Screen HUD: an optional click-through overlay draws the mapping box and a
  `暂停` / `录制` badge over the desktop, readable from the back of the room
  (**屏幕上显示映射框**).
- Control bar additions: **标定**, **急停**, and **退出**, so a lesson can be
  recovered without the tray; **档位** and **手势** cycle the tier lock and the
  click gesture in place.
- Two-hand toggle: both hands raised and spread for 1.5 s shows or hides the
  control bar, since the tray is out of reach from the back of the room
  (**双手张开呼出控制条**).
- Big cursor: **大光标** raises the Windows cursor size for the session
  (1–8x) so it is visible from the back row, and the previous size is put back
  on exit. The cursor is never hidden or redrawn.
- Scrolling: the cursor keeps scrolling while the hand pushes into the top or
  bottom 5 % of the screen, in both mapping modes. In the near tiers a V sign
  (index and middle up, ring and pinky down) scrolls one notch every 0.6 s,
  with the direction taken from which fingertip is higher. Both are switches
  (**贴边滚动**, **近距 V 字手势滚动**).
- Elevated windows: when Windows refuses injected input (a UIPI-blocked
  window), the status line and HUD say so instead of failing silently.

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

- No telemetry. The app only reaches the network to check for and download its
  own updates.
