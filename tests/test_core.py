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


class SurveyTests(unittest.TestCase):
    def test_near_seat_is_top_tier(self):
        from airpoint.tiers import suggest_tier

        with TempConfig() as config:
            tier, reason = suggest_tier(0.10, 60.0, 0.05, 0.9, config)
        self.assertEqual(tier, "A")
        self.assertIn("A 档", reason)

    def test_small_palm_lowers_tier(self):
        from airpoint.tiers import suggest_tier

        with TempConfig() as config:
            tier, reason = suggest_tier(0.10, 12.0, 0.05, 0.9, config)
        self.assertEqual(tier, "B")
        self.assertIn("手掌偏小", reason)

    def test_drop_and_low_confidence_lower_tier(self):
        from airpoint.tiers import suggest_tier

        with TempConfig() as config:
            tier, reason = suggest_tier(0.05, 0.0, 0.5, 0.2, config)
        self.assertEqual(tier, "E")
        self.assertIn("丢手偏多", reason)
        self.assertIn("姿态置信偏低", reason)

    def test_collector_averages_samples(self):
        from airpoint.survey import _Collector

        collector = _Collector()
        collector.reset()
        collector.add({"palm_ok": True, "palm_px": 40.0, "drop": 0.2,
                       "wrist_conf": 0.8, "shoulder": 0.06})
        collector.add({"palm_ok": False, "palm_px": 0.0, "drop": 0.4,
                       "wrist_conf": 0.0, "shoulder": 0.08})
        summary = collector.summary()
        self.assertAlmostEqual(summary["palm"], 40.0)
        self.assertAlmostEqual(summary["drop"], 0.3)
        self.assertAlmostEqual(summary["shoulder"], 0.07)
        self.assertEqual(summary["samples"], 2)


class AutoBoxTests(unittest.TestCase):
    def test_box_covers_body_with_padding(self):
        from airpoint.autobox import suggest_box

        box = suggest_box([(0.4, 0.2), (0.6, 0.8)], pad=0.10)
        self.assertLess(box[0], 0.4)
        self.assertGreater(box[2], 0.6)
        self.assertLess(box[1], 0.2)
        self.assertGreater(box[3], 0.8)

    def test_box_keeps_equal_gain_on_both_axes(self):
        from airpoint.autobox import suggest_box

        box = suggest_box(
            [(0.45, 0.2), (0.55, 0.9)], frame_aspect=4 / 3,
            screen_aspect=16 / 9,
        )
        width = box[2] - box[0]
        height = box[3] - box[1]
        self.assertAlmostEqual(width / height, (16 / 9) / (4 / 3), places=2)

    def test_box_stays_inside_frame(self):
        from airpoint.autobox import suggest_box

        box = suggest_box([(0.02, 0.05), (0.98, 0.95)], pad=0.30)
        self.assertGreaterEqual(box[0], 0.0)
        self.assertGreaterEqual(box[1], 0.0)
        self.assertLessEqual(box[2], 1.0)
        self.assertLessEqual(box[3], 1.0)

    def test_needs_two_points(self):
        from airpoint.autobox import suggest_box

        self.assertIsNone(suggest_box([(0.5, 0.5)]))


class WizardTests(unittest.TestCase):
    def test_module_imports_without_camera_stack(self):
        import airpoint.wizard as wizard

        self.assertEqual(wizard.STEPS[0], "摄像头")
        self.assertEqual(len(wizard.STEPS), 7)

    def test_defaults_carry_wizard_section(self):
        from airpoint.config import DEFAULTS

        self.assertFalse(DEFAULTS["wizard"]["done"])
        self.assertEqual(DEFAULTS["wizard"]["seat"], "stand")

    def test_wizard_flag_survives_roundtrip(self):
        with TempConfig() as config:
            config.set("wizard.done", True)
            config.set("wizard.profile", "教室")
            config.save()
            again = Config(config.path)
            self.assertTrue(again.get("wizard.done"))
            self.assertEqual(again.get("wizard.profile"), "教室")


    def test_second_tap_is_double_click(self):
        with TempConfig() as config:
            engine = GestureEngine(config, lambda message: None)
            engine.update("down", 10.0, (100, 100))
            first = engine.update("up", 10.1, (100, 100))
            engine.update("down", 10.2, (101, 100))
            second = engine.update("up", 10.25, (101, 100))
            self.assertTrue(first.left_click)
            self.assertFalse(first.double_click)
            self.assertTrue(second.double_click)
            self.assertFalse(second.left_click)

    def test_slow_second_tap_is_single_click(self):
        with TempConfig() as config:
            config.set("gesture.double_click_ms", 300)
            engine = GestureEngine(config, lambda message: None)
            engine.update("down", 10.0, (100, 100))
            engine.update("up", 10.1, (100, 100))
            engine.update("down", 11.0, (100, 100))
            second = engine.update("up", 11.1, (100, 100))
            self.assertTrue(second.left_click)
            self.assertFalse(second.double_click)

    def test_distant_second_tap_is_single_click(self):
        with TempConfig() as config:
            engine = GestureEngine(config, lambda message: None)
            engine.update("down", 10.0, (100, 100))
            engine.update("up", 10.1, (100, 100))
            engine.update("down", 10.2, (400, 400))
            second = engine.update("up", 10.25, (400, 400))
            self.assertTrue(second.left_click)
            self.assertFalse(second.double_click)


    def test_drag_is_measured_against_hand_not_frozen_cursor(self):
        with TempConfig() as config:
            engine = GestureEngine(config, lambda message: None)
            frozen = (100, 100)
            engine.update("down", 10.0, frozen, (100, 100))
            out = engine.update("down", 10.1, frozen, (160, 100))
            self.assertTrue(out.left_down)
            self.assertEqual(engine.state, "Dragging")

    def test_frozen_cursor_does_not_break_hold(self):
        with TempConfig() as config:
            config.set("gesture.right_click_ms", 1000)
            engine = GestureEngine(config, lambda message: None)
            engine.update("down", 10.0, (100, 100), (100, 100))
            out = engine.update("down", 11.2, (100, 100), (101, 100))
            self.assertTrue(out.right_click)


    def test_hold_progress_fills_then_resets(self):
        with TempConfig() as config:
            config.set("gesture.right_click_ms", 1000)
            engine = GestureEngine(config, lambda message: None)
            self.assertEqual(engine.hold_progress(10.0), 0.0)
            engine.update("down", 10.0, (100, 100))
            self.assertAlmostEqual(engine.hold_progress(10.5), 0.5, places=2)
            self.assertEqual(engine.hold_progress(12.0), 1.0)
            engine.update("down", 12.0, (100, 100))
            self.assertEqual(engine.hold_progress(12.1), 0.0)


class SelfCalibrationTests(unittest.TestCase):
    def test_offset_moves_toward_the_error(self):
        from airpoint.selfcal import SelfCalibrator

        calibrator = SelfCalibrator()
        calibrator.add((100.0, 100.0), (110.0, 90.0))
        self.assertGreater(calibrator.x, 0.0)
        self.assertLess(calibrator.y, 0.0)
        self.assertLessEqual(abs(calibrator.x), 2.0)
        self.assertLessEqual(abs(calibrator.y), 2.0)

    def test_step_is_capped(self):
        from airpoint.selfcal import SelfCalibrator

        calibrator = SelfCalibrator()
        calibrator.add((0.0, 0.0), (5000.0, 5000.0))
        self.assertLessEqual(calibrator.x, 2.0)
        self.assertLessEqual(calibrator.y, 2.0)

    def test_total_offset_is_bounded(self):
        from airpoint.selfcal import SelfCalibrator

        calibrator = SelfCalibrator()
        for _ in range(500):
            calibrator.add((0.0, 0.0), (5000.0, 5000.0))
        self.assertLessEqual(calibrator.x, 60.0)
        self.assertLessEqual(calibrator.y, 60.0)

    def test_apply_shifts_the_target(self):
        from airpoint.selfcal import SelfCalibrator

        calibrator = SelfCalibrator([4.0, -3.0])
        self.assertEqual(calibrator.apply((100.0, 200.0)), (104.0, 197.0))
        self.assertIsNone(calibrator.apply(None))

    def test_stored_offset_is_restored(self):
        from airpoint.selfcal import SelfCalibrator

        calibrator = SelfCalibrator([1.5, -2.5])
        self.assertEqual(calibrator.offset, [1.5, -2.5])


class MagnifierTests(unittest.TestCase):
    def test_source_rect_centres_on_cursor(self):
        from airpoint.magnifier import source_rect

        left, top, side = source_rect(960, 540, 1920, 1080, 2.5, 200)
        self.assertEqual(side, 80)
        self.assertEqual((left, top), (920, 500))

    def test_source_rect_clamps_at_edges(self):
        from airpoint.magnifier import source_rect

        left, top, side = source_rect(3, 2, 1920, 1080, 2.0, 200)
        self.assertEqual((left, top, side), (0, 0, 100))
        left, top, side = source_rect(1919, 1079, 1920, 1080, 2.0, 200)
        self.assertEqual(left + side, 1920)
        self.assertEqual(top + side, 1080)

    def test_window_flips_near_edges(self):
        from airpoint.magnifier import window_position

        x, y = window_position(100, 100, 1920, 1080, 200, 48)
        self.assertEqual((x, y), (148, 148))
        x, y = window_position(1900, 1060, 1920, 1080, 200, 48)
        self.assertLessEqual(x + 200, 1920)
        self.assertLessEqual(y + 200, 1080)
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)


class SafeZoneTests(unittest.TestCase):
    def test_edges_are_blocked(self):
        from airpoint.pointer import safe_zone

        self.assertTrue(safe_zone(10, 540, 1920, 1080))
        self.assertTrue(safe_zone(960, 1070, 1920, 1080))
        self.assertTrue(safe_zone(1915, 540, 1920, 1080))
        self.assertTrue(safe_zone(960, 5, 1920, 1080))

    def test_centre_is_free(self):
        from airpoint.pointer import safe_zone

        self.assertFalse(safe_zone(960, 540, 1920, 1080))
        self.assertFalse(safe_zone(100, 100, 1920, 1080))


class DiagnosticsTests(unittest.TestCase):
    def test_export_writes_header_and_rows(self):
        from airpoint.diagnostics import COLUMNS, Diagnostics

        diag = Diagnostics()
        diag.add(("1.0", 1, "C", "C", "40.0", "0.1"))
        with tempfile.TemporaryDirectory() as folder:
            target = diag.export(Path(folder) / "diag.csv")
            lines = target.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], ",".join(COLUMNS))
        self.assertEqual(len(lines), 2)
        self.assertEqual(len(diag), 1)

    def test_ring_buffer_is_bounded(self):
        from airpoint.diagnostics import Diagnostics

        diag = Diagnostics(capacity=3)
        for index in range(5):
            diag.add((index,))
        self.assertEqual(len(diag), 3)


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
