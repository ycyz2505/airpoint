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

The download goes through WinHTTP first (system proxy, cross-domain
redirects) and falls back to `urllib` with the registry's proxy settings. It is
streamed to disk, checked against the SHA-256 and size in the manifest, then
the app restarts itself to apply it. A failed start rolls back to the previous
build; the previous `config.json` is kept as `config.json.bak` on every save.
`update.apply_when` chooses between installing at startup, staging until
the next start, or only on request. A read-only install folder disables the
updater instead of failing every check. Set `OPTICALINPUT_NO_UPDATE=1` to
disable automatic updates. Update traffic is logged to `logs\update.log`, and
settings → 更新 shows the current version, the last check and a manual button.

Settings files carry a `schema_version`. An older `config.json` is migrated
forward on load — renamed keys move to their new names, removed keys are
dropped, and an explicit new-name value always wins — then written back once,
with the pre-migration file kept as `config.json.bak`. A file from a newer
build is left untouched and the app starts from defaults.

## Current build

- Camera capture at the size picked in settings (falling back to 4K and then
  1080p if the device refuses it); automatic device and backend selection.
  MJPEG is requested first and YUY2 is tried when the device refuses it, and a
  short manual exposure is kept only when the driver reads it back. The hand
  crop is capped at 640 px a side, since the model works at 224 internally and
  a 4K crop would only waste decode time. When the performance governor reaches
  L1 the device is reopened at 1920 wide, because resizing after the read still
  pays for 4K decode; it returns to 4K at L0 unless it has tripped twice. The
  governor samples once a second, acts only after a condition has held for a
  full 5 s window, and never moves two rungs inside one window; L5 pauses the
  service and raises a tray balloon.
- MediaPipe Pose wrist tracking; right or left hand.
- Relative cursor with One Euro smoothing and short prediction.
- Tray toggle; `Ctrl+Alt+Shift+F9` toggle, `Ctrl+Alt+Shift+F10` cycle mode,
  `Ctrl+Alt+Shift+F8` control bar, `F12` emergency stop.
- Preview/diagnostics window with the camera image, pose skeleton, wrist hit
  rate, and live gain/smoothing/prediction sliders. The status strip along the
  top carries the tier, mode, fps, governor level, palm size, drop rate and
  per-frame cost.
- Left click by pinch or fist; right click by holding 1 s; drag, double click,
  and edge wheel scrolling.
- Single-hand and two-hand schemes (right hand moves, left hand clicks).
- Hand landmarks and gesture state in the preview window.
- Absolute "virtual touch" mode: draw a box in the preview; the palm maps to
  the screen inside the box. The same box acts as an active zone in relative
  mode; outside it, the cursor freezes. A tier change eases the cursor to the
  new mapping point over 150 ms instead of teleporting it.
- Nine-point calibration for absolute mode: a 3x3 grid of full-screen targets,
  hold still at each one, and a homography replaces the box affine mapping. The
  extra points cover the corners and keep the fit stable in least squares.
- Automatic A–E tiering from shoulder width plus palm size and hand drop rate,
  with downgrade/upgrade hysteresis. Near picks absolute + single hand; far
  picks relative + two hands and drops hand gestures when the hand is too
  small. Manual lock and advisory-only mode are available.
- Seat profiles: save and load the box, nine-point calibration, feel settings,
  tier lock, gestures, and camera settings. Names default to
  `坐/站_摄像头_分辨率` and can be renamed. The last profile loads at startup;
  if the seat, camera or resolution no longer match, the app asks whether to
  redraw the box.
- Physical mouse takeover: moving the real mouse pauses the air cursor and it
  resumes after 3 seconds of mouse inactivity; the HUD, control bar, tray line
  and preview status strip all say `物理鼠标` / `MOUSE` while that is why the
  cursor is idle. A hotkey or tray toggle cancels the automatic resume.
- Manual recording from the tray: the controller stops cursor work and writes
  the raw camera stream to `recordings/rec-*.avi` (MJPG) after checking free
  disk space.
- Start with Windows: the **开机自动运行** switch writes or removes the
  `HKCU\...\CurrentVersion\Run` entry, and the app reconciles it on launch so
  a stale entry never survives a moved executable.
- Offline replay: `python -m airpoint.replay clip.avi --csv metrics.csv`
  re-runs pose, hands, pointer, gestures, and tiering over a recording and
  reports hit rate, palm size, cursor path, per-frame cost (mean / p95 / max)
  and tier time, so feel can be tuned without the classroom. `--profile`
  takes a seat profile straight from `profiles\`, `--annotate out.avi` writes
  a copy with the skeleton, gesture and tier burned in, and
  `--compare other.json --compare-labels 现状,新参` runs the same clip twice
  and prints a side-by-side table for A/B tuning. The packaged executable
  does the same with `OpticalInputSvc.exe --replay clip.avi` and shows the
  brief summary in a dialog.
- Handwriting mode: while the button is held the cursor is filtered harder so
  strokes drawn in the Seewo whiteboard come out as lines instead of jitter,
  and a minimum step keeps sub-pixel drift from becoming a wobbly stroke.
- Control bar: a small always-on-top bar with activate, click, mode, preview,
  and settings, reachable by the air cursor at six metres. Its buttons are
  padded to 120 px so they are still hittable from the back row.
- Performance governor: six levels from full quality down to pause, chosen
  from the process CPU and working set, with a slow recovery back up.
- Tiering now only changes the mapping mode by default; gesture scheme changes
  are opt-in so the interaction does not flip underneath the user.
- Speed-adaptive gain and sub-pixel cursor accumulation for slow movement.
- All mouse buttons are released on pause, exit, or error.
- If a frame throws, the loop releases the buttons, backs off, and restarts;
  five consecutive crashes pause the service and log why, instead of leaving
  it running but inert.
- The tray tooltip tracks the real state (`运行中` / `已暂停` / `录制中`) even
  with the menu closed, and the menu opens with a greyed-out status line such
  as `B 档 · 绝对 · 运行中 · 手掌 34px · 能力 78`. The capability score puts
  half its weight on palm size and half on the hand drop rate, and it keeps
  updating while the tier is manually locked.
- `config.json` is watched for changes: editing it by hand (or syncing it from
  another machine) reloads it live and reopens the camera, and a half-written
  file is ignored until it parses. Logs roll to `app.1.log` instead of being
  deleted, so the run before a crash is still there.
- Writing magnifier: a small always-on-top, click-through window follows the
  cursor while a button is held and shows a 2.5x (adjustable) view of the
  screen area under it, so the stroke lands where intended.
- Presentation mode: a 5 % safe band around the screen edge ignores clicks so
  the cursor cannot fire a tool by accident; the status line shows `DEMO` and
  the control bar stays hidden.
- Diagnostics export: the preview window's **导出诊断** button writes the last
  30 seconds of per-frame features (tier, band, palm size, drop rate, gesture
  state, cursor, fps, CPU, memory, frame cost) as a CSV into the data
  directory. The **跟踪框** switch draws the tracked hand's bounding box over
  the camera image.
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
  The watchdog is its own 20 Hz thread, so it still fires when the camera
  stalls or a model throws and no frame is running; `atexit` and a cleanup at
  startup cover the crash cases where no thread survives.
- Long-press ring: a small ring under the cursor fills while a press builds, so
  a right click is not a surprise; toggle with **长按进度环**.
- Screen HUD: an optional click-through overlay draws the mapping box and a
  `暂停` / `录制` badge over the desktop, readable from the back of the room
  (**屏幕上显示映射框**), and flashes the new tier for two seconds when it
  changes (**档位变化提示**).
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
- Personalized gestures: **录制手势样本…** in the gesture tab walks through
  open / pinch / fist / point, 40 seconds each, and stores a scale-free
  ten-number shape per sample. With **个性化手势（kNN）** on, the nearest
  neighbours among those samples get a vote, so a user whose pinch never
  crosses the fixed ratio still gets clicks. It stays off by default and only
  overrides a gesture when its confidence is at least 0.7.

## Settings

The tray menu's **设置…** and **退出** require the management password set on
first run. Settings are a tabbed window: update, startup, performance, camera,
tiers, tier thresholds (shoulder-width ratios, palm size, drop rate,
hysteresis), pointer, perception (pose and hand model complexity and
confidence), gestures, overlay, hotkeys, recording, plus separate tabs for the
password, seat profiles, and about. Saving applies immediately — the frame
loop reloads the config and the hotkeys re-register without a restart. The
password is stored as a PBKDF2-HMAC-SHA256 hash; five failures lock the menu
for 30 seconds.

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
