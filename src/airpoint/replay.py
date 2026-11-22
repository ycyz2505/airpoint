from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import deque
from pathlib import Path

import cv2

from .config import Config
from .gestures import GestureEngine
from .hands import HandTracker
from .pointer import AbsolutePointer, RelativePointer
from .pose import PoseTracker
from .tiers import TierEngine, palm_px_1080


class ReplayAnalyzer:
    """Runs the perception and pointer pipeline over a recorded video.

    The analyzer never injects input. It reports how the current settings
    would have behaved on the recording, so feel and tiering can be tuned
    without a camera or a classroom.
    """

    def __init__(self, config, log=None, pose=None, hands=None):
        self.config = config
        self.log = log or (lambda message: None)
        self.pose = pose or PoseTracker(config, self.log)
        self.hands = hands or HandTracker(config, self.log)

    def _resolved(self):
        moving_hand = str(self.config.get("pointer.hand", "right"))
        scheme = str(self.config.get("gesture.scheme", "auto"))
        if scheme == "auto":
            scheme = "single"
        click_name = str(self.config.get("gesture.click", "auto"))
        if click_name == "auto":
            click_name = "pinch" if scheme == "single" else "fist"
        mode = str(self.config.get("pointer.mode", "relative"))
        box = self.config.get("pointer.box", [])
        if not isinstance(box, list) or len(box) != 4:
            box = []
        if mode == "auto":
            mode = "absolute" if box else "relative"
        if mode == "absolute" and not box:
            mode = "relative"
        return moving_hand, scheme, click_name, mode, box

    def run(self, path, limit=None, csv_path=None):
        capture = cv2.VideoCapture(str(path))
        if not capture.isOpened():
            raise ValueError(f"cannot open {path}")

        source_fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        if source_fps <= 0:
            source_fps = 30.0

        moving_hand, scheme, click_name, mode, box = self._resolved()
        screen_w, screen_h = 1920, 1080

        pointer = RelativePointer(self.config)
        absolute = AbsolutePointer(self.config)
        gestures = GestureEngine(self.config, self.log)
        tier = TierEngine(self.config, self.log)

        seen = deque(maxlen=60)
        rows = []
        tier_counts = {}
        hits = 0
        hand_hits = 0
        palm_values = []
        wrist_conf = []
        dx_values = []
        dy_values = []
        max_step = 0.0
        total_px = 0.0
        cursor = (screen_w / 2.0, screen_h / 2.0)
        carry_x = 0.0
        carry_y = 0.0
        last_palm_px = 0.0
        inside_prev = False
        clicks = 0
        right_clicks = 0
        drags = 0
        frames = 0
        index = 0

        calibration = self.config.get("pointer.calibration", [])
        calibration_size = self.config.get("pointer.calibration_size", [])
        calibrated = bool(
            isinstance(calibration, list)
            and len(calibration) == 9
            and isinstance(calibration_size, list)
            and len(calibration_size) == 2
            and int(calibration_size[0]) == screen_w
            and int(calibration_size[1]) == screen_h
        )
        active_calibration = calibration if calibrated else None

        while True:
            ok, frame = capture.read()
            if not ok or frame is None:
                break
            if limit is not None and frames >= int(limit):
                break
            frames += 1
            index += 1
            now = frames / source_fps
            height, width = frame.shape[:2]

            if width > 1280:
                scale = 1280.0 / float(width)
                small = cv2.resize(
                    frame,
                    (1280, max(1, int(height * scale))),
                    interpolation=cv2.INTER_AREA,
                )
            else:
                small = frame

            wrist = self.pose.process(small)
            seen.append(1 if wrist is not None else 0)
            drop = 1.0 - (sum(seen) / len(seen))
            shoulder = self.pose.shoulder_width()
            pose_conf = wrist[2] if wrist else 0.0
            busy = gestures.state != "Idle"
            tier_state = tier.update(
                shoulder, last_palm_px, drop, pose_conf, busy, now
            )
            tier_counts[tier_state.name] = (
                tier_counts.get(tier_state.name, 0) + 1
            )

            result = None
            if wrist is not None:
                hits += 1
                wrist_conf.append(wrist[2])
                result = self.hands.process(
                    frame, wrist, self.pose.elbow(moving_hand), moving_hand
                )
                if result is not None:
                    hand_hits += 1
                    if result.get("palm"):
                        last_palm_px = palm_px_1080(float(result["palm"]))
                        palm_values.append(last_palm_px)

            point = None
            if wrist is not None:
                point = (wrist[0], wrist[1])
                if result and result.get("landmarks"):
                    lm = result["landmarks"]
                    point = (
                        sum(lm[i][0] for i in (0, 5, 9, 13, 17)) / 5.0,
                        sum(lm[i][1] for i in (0, 5, 9, 13, 17)) / 5.0,
                    )

            inside = True
            if box:
                bx0, by0, bx1, by1 = [float(item) for item in box]
                inside = (
                    point is not None
                    and bx0 <= point[0] <= bx1
                    and by0 <= point[1] <= by1
                )

            dx = 0.0
            dy = 0.0
            if point is not None and inside:
                if mode == "absolute":
                    target = absolute.update(
                        point[0],
                        point[1],
                        now,
                        box,
                        screen_w,
                        screen_h,
                        calibration=active_calibration,
                    )
                    if target is not None:
                        dx = target[0] - cursor[0]
                        dy = target[1] - cursor[1]
                        cursor = (target[0], target[1])
                else:
                    if not inside_prev:
                        pointer.reset()
                    step_x, step_y = pointer.update(
                        point[0], point[1], now, screen_w, screen_h
                    )
                    dx = step_x + carry_x
                    dy = step_y + carry_y
                    next_x = round(cursor[0] + dx)
                    next_y = round(cursor[1] + dy)
                    carry_x = max(-1.0, min(1.0, cursor[0] + dx - next_x))
                    carry_y = max(-1.0, min(1.0, cursor[1] + dy - next_y))
                    cursor = (next_x, next_y)
            inside_prev = inside

            kind = "none"
            if inside and result is not None:
                kind = "down" if result["gesture"] == click_name else "up"
            if scheme != "single":
                kind = "none"
            out = gestures.update(kind, now, (int(cursor[0]), int(cursor[1])))
            if out.left_click:
                clicks += 1
            if out.right_click:
                right_clicks += 1
            if out.left_down:
                drags += 1

            magnitude = (dx * dx + dy * dy) ** 0.5
            total_px += magnitude
            max_step = max(max_step, magnitude)
            dx_values.append(dx)
            dy_values.append(dy)

            rows.append(
                (
                    f"{now:.4f}",
                    f"{point[0]:.5f}" if point else "",
                    f"{point[1]:.5f}" if point else "",
                    f"{wrist[2]:.3f}" if wrist else "",
                    f"{last_palm_px:.1f}",
                    f"{result.get('pinch_ratio', 0.0):.3f}" if result else "",
                    f"{result.get('fist_ratio', 0.0):.3f}" if result else "",
                    result["gesture"] if result else "none",
                    tier_state.name,
                    f"{dx:.2f}",
                    f"{dy:.2f}",
                )
            )

        capture.release()

        duration = frames / source_fps
        summary = {
            "file": str(path),
            "frames": frames,
            "duration_s": round(duration, 2),
            "source_fps": round(source_fps, 2),
            "settings": {
                "mode": mode,
                "scheme": scheme,
                "click": click_name,
                "hand": moving_hand,
                "tier_apply": bool(self.config.get("tier.apply", True)),
                "calibrated": calibrated,
                "box": box,
            },
            "pose_hit_pct": round(100.0 * hits / max(1, frames), 2),
            "hand_hit_pct": round(100.0 * hand_hits / max(1, frames), 2),
            "wrist_confidence": _mean(wrist_conf),
            "palm_px": _stats(palm_values),
            "tier": tier_counts,
            "cursor": {
                "path_px": round(total_px, 1),
                "max_step_px": round(max_step, 1),
                "dx_std": round(_std(dx_values), 2),
                "dy_std": round(_std(dy_values), 2),
            },
            "gestures": {
                "clicks": clicks,
                "right_clicks": right_clicks,
                "drags": drags,
            },
        }

        if csv_path is not None:
            target = Path(csv_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            lines = [
                "t,wrist_x,wrist_y,wrist_conf,palm_px,pinch_ratio,"
                "fist_ratio,gesture,tier,dx,dy"
            ]
            lines.extend(",".join(row) for row in rows)
            target.write_text("\n".join(lines) + "\n", encoding="utf-8")
            summary["csv"] = str(target)

        return summary


def _mean(values):
    return round(sum(values) / len(values), 3) if values else 0.0


def _std(values):
    if len(values) < 2:
        return 0.0
    return statistics.pstdev(values)


def _stats(values):
    if not values:
        return {"count": 0, "mean": 0.0, "median": 0.0}
    return {
        "count": len(values),
        "mean": round(statistics.fmean(values), 1),
        "median": round(statistics.median(values), 1),
    }


def analyze(path, config=None, log=None, limit=None, csv_path=None):
    config = config or Config()
    return ReplayAnalyzer(config, log).run(
        path, limit=limit, csv_path=csv_path
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="airpoint.replay",
        description="Analyze a recorded camera clip offline.",
    )
    parser.add_argument("video", help="recorded .avi file")
    parser.add_argument("--csv", help="write per-frame metrics here")
    parser.add_argument("--json", help="write the summary here")
    parser.add_argument("--limit", type=int, help="stop after N frames")
    parser.add_argument("--config", help="use this config.json")
    args = parser.parse_args(argv)

    config = Config(Path(args.config)) if args.config else Config()
    summary = analyze(
        args.video, config=config, limit=args.limit, csv_path=args.csv
    )
    text = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.json:
        target = Path(args.json)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + "\n", encoding="utf-8")
        print(f"written {target}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
