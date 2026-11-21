from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from airpoint.config import Config  # noqa: E402
from airpoint.gestures import GestureEngine  # noqa: E402
from airpoint.pointer import AbsolutePointer, RelativePointer  # noqa: E402
from airpoint.tiers import TierEngine  # noqa: E402


class TempConfig:
    def __enter__(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = Path(self._dir.name) / "config.json"
        return Config(self.path)

    def __exit__(self, *exc):
        self._dir.cleanup()
        return False


class PointerTests(unittest.TestCase):
    def test_relative_first_frame_is_zero(self):
        with TempConfig() as config:
            pointer = RelativePointer(config)
            self.assertEqual(pointer.update(0.5, 0.5, 1.0, 1920, 1080), (0, 0))

    def test_relative_moves_toward_motion(self):
        with TempConfig() as config:
            pointer = RelativePointer(config)
            pointer.update(0.50, 0.50, 1.00, 1920, 1080)
            dx, dy = pointer.update(0.52, 0.50, 1.04, 1920, 1080)
            self.assertGreater(dx, 0)
            self.assertAlmostEqual(dy, 0.0, places=6)

    def test_relative_rest_is_dead(self):
        with TempConfig() as config:
            pointer = RelativePointer(config)
            pointer.update(0.50, 0.50, 1.00, 1920, 1080)
            dx, dy = pointer.update(0.5001, 0.5001, 1.04, 1920, 1080)
            self.assertEqual((dx, dy), (0.0, 0.0))

    def test_relative_step_is_clamped(self):
        with TempConfig() as config:
            config.set("pointer.max_step_px", 50)
            pointer = RelativePointer(config)
            pointer.update(0.1, 0.1, 1.0, 1920, 1080)
            dx, dy = pointer.update(0.9, 0.9, 1.04, 1920, 1080)
            self.assertLessEqual(abs(dx), 50.0 + 1e-9)
            self.assertLessEqual(abs(dy), 50.0 + 1e-9)

    def test_absolute_box_mapping(self):
        with TempConfig() as config:
            absolute = AbsolutePointer(config)
            target = absolute.update(
                0.5, 0.5, 1.0, [0.0, 0.0, 1.0, 1.0], 1920, 1080
            )
            self.assertIsNotNone(target)
            self.assertAlmostEqual(target[0], 960.0, delta=1.0)
            self.assertAlmostEqual(target[1], 540.0, delta=1.0)

    def test_absolute_outside_box_is_ignored(self):
        with TempConfig() as config:
            absolute = AbsolutePointer(config)
            target = absolute.update(
                0.9, 0.9, 1.0, [0.0, 0.0, 0.5, 0.5], 1920, 1080
            )
            self.assertIsNone(target)

    def test_absolute_homography(self):
        with TempConfig() as config:
            absolute = AbsolutePointer(config)
            identity = [1, 0, 0, 0, 1, 0, 0, 0, 1]
            target = absolute.update(
                0.25, 0.75, 1.0, [0.0, 0.0, 1.0, 1.0], 1920, 1080,
                calibration=identity,
            )
            self.assertIsNotNone(target)
            self.assertAlmostEqual(target[0], 0.25, places=3)
            self.assertAlmostEqual(target[1], 0.75, places=3)


class GestureTests(unittest.TestCase):
    def test_tap_is_left_click(self):
        with TempConfig() as config:
            engine = GestureEngine(config, lambda message: None)
            engine.update("down", 10.0, (100, 100))
            out = engine.update("up", 10.1, (100, 100))
            self.assertTrue(out.left_click)

    def test_hold_is_right_click(self):
        with TempConfig() as config:
            config.set("gesture.right_click_ms", 1000)
            engine = GestureEngine(config, lambda message: None)
            engine.update("down", 10.0, (100, 100))
            out = engine.update("down", 11.2, (100, 100))
            self.assertTrue(out.right_click)
            self.assertEqual(engine.state, "Consumed")

    def test_move_drags(self):
        with TempConfig() as config:
            engine = GestureEngine(config, lambda message: None)
            engine.update("down", 10.0, (100, 100))
            out = engine.update("down", 10.1, (160, 100))
            self.assertTrue(out.left_down)
            self.assertEqual(engine.state, "Dragging")
            out = engine.update("up", 10.2, (160, 100))
            self.assertTrue(out.left_up)


class TierTests(unittest.TestCase):
    def _run(self, engine, shoulder, palm, now):
        return engine.update(shoulder, palm, 0.0, 0.9, False, now)

    def _feed(self, engine, shoulder, palm, start, seconds):
        """Drive the engine at 30 fps so the shoulder EMA can settle."""
        state = None
        steps = max(1, int(seconds * 30))
        for step in range(steps):
            state = self._run(
                engine, shoulder, palm, start + step / 30.0
            )
        return state, start + steps / 30.0

    def test_near_is_absolute_single_hand(self):
        with TempConfig() as config:
            config.set("tier.apply", True)
            engine = TierEngine(config, lambda message: None)
            state = self._run(engine, 0.09, 60.0, 0.0)
            self.assertEqual(state.name, "A")
            self.assertEqual(state.mode, "absolute")
            self.assertEqual(state.scheme, "single")

    def test_far_drops_hands(self):
        with TempConfig() as config:
            config.set("tier.apply", True)
            config.set("tier.apply_gesture", True)
            engine = TierEngine(config, lambda message: None)
            state, _ = self._feed(engine, 0.02, 12.0, 0.0, 1.0)
            self.assertEqual(state.name, "E")
            self.assertFalse(state.hands)

    def test_gesture_is_kept_unless_opted_in(self):
        with TempConfig() as config:
            config.set("tier.apply", True)
            config.set("tier.apply_gesture", False)
            config.set("gesture.scheme", "single")
            engine = TierEngine(config, lambda message: None)
            state, _ = self._feed(engine, 0.02, 12.0, 0.0, 1.0)
            self.assertEqual(state.name, "E")
            self.assertEqual(state.scheme, "single")
            self.assertTrue(state.hands)

    def test_downgrade_waits(self):
        with TempConfig() as config:
            config.set("tier.apply", True)
            config.set("tier.downgrade_ms", 1000)
            engine = TierEngine(config, lambda message: None)
            self._run(engine, 0.09, 60.0, 0.0)
            self.assertEqual(engine.current, "A")

            state, now = self._feed(engine, 0.02, 12.0, 0.0, 0.3)
            self.assertEqual(state.name, "A")

            state, _ = self._feed(engine, 0.02, 12.0, now, 2.0)
            self.assertNotEqual(state.name, "A")

    def test_manual_lock(self):
        with TempConfig() as config:
            config.set("tier.mode", "manual")
            config.set("tier.lock", "D")
            engine = TierEngine(config, lambda message: None)
            state = self._run(engine, 0.09, 60.0, 0.0)
            self.assertEqual(state.name, "D")


class UpdateTests(unittest.TestCase):
    def test_version_compare(self):
        from airpoint.updater import _is_newer

        self.assertTrue(_is_newer("0.10.3", "0.10.2"))
        self.assertFalse(_is_newer("0.10.2", "0.10.2"))
        self.assertFalse(_is_newer("0.9.9", "0.10.0"))


class ConfigTests(unittest.TestCase):
    def test_dotted_get_set_and_roundtrip(self):
        with TempConfig() as config:
            self.assertEqual(config.get("pointer.hand"), "right")
            config.set("pointer.hand", "left")
            config.set("pointer.box", [0.1, 0.2, 0.3, 0.4])
            config.save()
            again = Config(config.path)
            self.assertEqual(again.get("pointer.hand"), "left")
            self.assertEqual(again.get("pointer.box"), [0.1, 0.2, 0.3, 0.4])

    def test_missing_returns_default(self):
        with TempConfig() as config:
            self.assertEqual(config.get("nope.value", 7), 7)

    def test_partial_file_keeps_defaults(self):
        with TempConfig() as config:
            config.path.write_text(
                json.dumps({"pointer": {"hand": "left"}}), encoding="utf-8"
            )
            merged = Config(config.path)
            self.assertEqual(merged.get("pointer.hand"), "left")
            self.assertEqual(merged.get("camera.fps"), 30)


class ProfileTests(unittest.TestCase):
    def test_save_load_roundtrip(self):
        from airpoint.profiles import ProfileStore

        with tempfile.TemporaryDirectory() as home:
            old = os.environ.get("OPTICALINPUT_HOME")
            os.environ["OPTICALINPUT_HOME"] = home
            try:
                config = Config(Path(home) / "config.json")
                store = ProfileStore(config)
                config.set("pointer.gain_relative", 2.7)
                config.set("pointer.box", [0.1, 0.2, 0.3, 0.4])
                store.save("seat")
                self.assertIn("seat", store.list())

                config.set("pointer.gain_relative", 1.0)
                config.set("pointer.box", [])
                store.load("seat")
                self.assertAlmostEqual(
                    config.get("pointer.gain_relative"), 2.7
                )
                self.assertEqual(
                    config.get("pointer.box"), [0.1, 0.2, 0.3, 0.4]
                )
                self.assertEqual(config.get("profile.current"), "seat")
            finally:
                if old is None:
                    os.environ.pop("OPTICALINPUT_HOME", None)
                else:
                    os.environ["OPTICALINPUT_HOME"] = old


class ReplayTests(unittest.TestCase):
    def test_metrics_on_synthetic_clip(self):
        try:
            import cv2
        except ImportError:
            self.skipTest("opencv not installed")

        from airpoint.replay import ReplayAnalyzer

        class FakePose:
            ready = True

            def __init__(self):
                self.index = 0

            def process(self, frame):
                self.index += 1
                x = 0.3 + 0.01 * self.index
                return x, 0.5, 0.9

            def wrist(self, hand="right"):
                return 0.3, 0.5, 0.9

            def elbow(self, hand="right"):
                return None

            def shoulder_width(self):
                return 0.08

            def draw(self, frame):
                return None

        class FakeHands:
            ready = True

            def process(self, frame, wrist, elbow, hand):
                return {
                    "hand": hand,
                    "gesture": "open",
                    "raw": "open",
                    "pinch": False,
                    "fist": False,
                    "pinch_ratio": 0.9,
                    "fist_ratio": 1.8,
                    "palm": 0.05,
                    "landmarks": None,
                    "confidence": 0.9,
                    "box": None,
                }

            def draw(self, frame, result):
                return None

            def reset(self):
                return None

        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            video = folder / "clip.avi"
            writer = cv2.VideoWriter(
                str(video),
                cv2.VideoWriter_fourcc(*"MJPG"),
                20.0,
                (320, 240),
            )
            for index in range(20):
                frame = cv2.UMat(240, 320, cv2.CV_8UC3).get()
                frame[:] = (index * 8, 40, 80)
                writer.write(frame)
            writer.release()

            with TempConfig() as config:
                analyzer = ReplayAnalyzer(
                    config,
                    log=lambda message: None,
                    pose=FakePose(),
                    hands=FakeHands(),
                )
                csv_path = folder / "metrics.csv"
                summary = analyzer.run(video, csv_path=csv_path)

            self.assertEqual(summary["frames"], 20)
            self.assertEqual(summary["pose_hit_pct"], 100.0)
            self.assertGreater(summary["cursor"]["path_px"], 0.0)
            self.assertTrue(summary["tier"])
            for name in summary["tier"]:
                self.assertIn(name, ("A", "B", "C", "D", "E"))
            self.assertTrue(csv_path.exists())
            lines = csv_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 21)


if __name__ == "__main__":
    unittest.main()
