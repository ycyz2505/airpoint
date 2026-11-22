from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import importlib.util
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from airpoint.config import Config  # noqa: E402
from airpoint.gestures import GestureEngine, TwoHandOpen  # noqa: E402
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


class _FakePose:
    def __init__(self, left, right, shoulder=0.12, shoulder_y=0.5):
        self._left = left
        self._right = right
        self._shoulder = shoulder
        self._shoulder_y = shoulder_y

    def wrist(self, hand="right"):
        return self._left if hand == "left" else self._right

    def shoulder_width(self) -> float:
        return self._shoulder

    def landmark(self, index, min_visibility=0.3):
        if index == 11:
            return (0.5 - self._shoulder / 2, self._shoulder_y, 1.0)
        if index == 12:
            return (0.5 + self._shoulder / 2, self._shoulder_y, 1.0)
        return None


class TwoHandTests(unittest.TestCase):
    def test_raised_spread_hands_fire_after_the_hold(self):
        with TempConfig() as config:
            config.set("overlay.two_hand_hold_ms", 1500)
            detector = TwoHandOpen(config)
            pose = _FakePose((0.2, 0.30), (0.8, 0.30))
            self.assertFalse(detector.update(pose, 10.0))
            self.assertFalse(detector.update(pose, 11.0))
            self.assertTrue(detector.update(pose, 11.6))
            self.assertFalse(detector.update(pose, 12.5))

    def test_narrow_hands_do_not_fire(self):
        with TempConfig() as config:
            detector = TwoHandOpen(config)
            pose = _FakePose((0.45, 0.30), (0.55, 0.30))
            for step in range(30):
                self.assertFalse(detector.update(pose, 10.0 + step * 0.1))

    def test_lowered_hands_do_not_fire(self):
        with TempConfig() as config:
            detector = TwoHandOpen(config)
            pose = _FakePose((0.2, 0.75), (0.8, 0.75))
            for step in range(30):
                self.assertFalse(detector.update(pose, 10.0 + step * 0.1))

    def test_disabled_switch_never_fires(self):
        with TempConfig() as config:
            config.set("overlay.two_hand_bar", False)
            detector = TwoHandOpen(config)
            pose = _FakePose((0.2, 0.30), (0.8, 0.30))
            for step in range(30):
                self.assertFalse(detector.update(pose, 10.0 + step * 0.1))


class TierTests(unittest.TestCase):
    def test_capability_score_rewards_big_hands_and_few_drops(self):
        from airpoint.tiers import capability

        config = Config.__new__(Config)
        config.data = {"tier": {"palm_good": 30.0}}

        self.assertEqual(capability(0.0, 1.0, config), 0)
        self.assertEqual(capability(30.0, 0.0, config), 100)
        self.assertEqual(capability(60.0, 0.0, config), 100)
        # Half the palm size and half the frames lost lands in the middle.
        self.assertEqual(capability(15.0, 0.5, config), 50)

    def test_palm_pixels_are_reported_at_1080p(self):
        from airpoint.tiers import palm_px_1080

        # 0.02 of the frame width is 38 px at 1080p but 77 px at 4K; the
        # acceptance thresholds only make sense against the 1080p number, so
        # the same hand must score the same on either camera.
        self.assertAlmostEqual(palm_px_1080(0.02), 38.4)
        self.assertAlmostEqual(palm_px_1080(0.02) / 1920.0, 0.02)
        self.assertAlmostEqual(palm_px_1080(0.0), 0.0)

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
            # The gesture preference must not switch the hand model back on;
            # E runs on the wrist alone and that is a cost decision.
            self.assertFalse(state.hands)

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


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        if importlib.util.find_spec("cv2") is None:
            self.skipTest("opencv not installed")

    def _samples(self, outlier=False):
        import cv2
        import numpy as np

        from airpoint.calibrate import TARGETS

        screen = np.array([1920.0, 1080.0])
        dst = np.array(
            [[x * 1920.0, y * 1080.0] for x, y in TARGETS], dtype=np.float64
        )
        matrix = np.array(
            [[1.3, 0.1, 20.0], [0.05, 1.2, 10.0], [1e-4, 2e-4, 1.0]]
        )
        src = cv2.perspectiveTransform(
            dst.reshape(-1, 1, 2), np.linalg.inv(matrix)
        ).reshape(-1, 2)
        if outlier:
            src = src.copy()
            src[4] += (0.12, -0.08) * screen
        return src, dst

    def test_nine_targets_form_a_grid(self):
        from airpoint.calibrate import TARGETS

        self.assertEqual(len(TARGETS), 9)
        self.assertEqual(len({point[0] for point in TARGETS}), 3)
        self.assertEqual(len({point[1] for point in TARGETS}), 3)

    def test_clean_samples_fit_exactly(self):
        import cv2
        import numpy as np

        from airpoint.calibrate import _fit_homography

        src, dst = self._samples()
        matrix = _fit_homography(src, dst)
        self.assertIsNotNone(matrix)
        projected = cv2.perspectiveTransform(
            src.reshape(-1, 1, 2), matrix
        ).reshape(-1, 2)
        self.assertLess(float(np.abs(projected - dst).max()), 1.0)

    def test_one_wild_sample_is_dropped(self):
        import cv2
        import numpy as np

        from airpoint.calibrate import _fit_homography

        src, dst = self._samples(outlier=True)
        matrix = _fit_homography(src, dst)
        self.assertIsNotNone(matrix)
        good = np.arange(len(src)) != 4
        projected = cv2.perspectiveTransform(
            src[good].reshape(-1, 1, 2), matrix
        ).reshape(-1, 2)
        self.assertLess(float(np.abs(projected - dst[good]).max()), 2.0)


class PersonalizeTests(unittest.TestCase):
    def _hand(self, spread: float):
        # Synthetic 21-point hand: landmark 0 at origin, 9 one unit up.
        points = [(0.5, 0.5)] * 21
        points[9] = (0.5, 0.5 - spread)
        points[4] = (0.5 + spread, 0.5)
        points[8] = (0.5 + spread * 2, 0.5)
        points[12] = (0.5 + spread * 3, 0.5)
        points[16] = (0.5 + spread * 4, 0.5)
        points[20] = (0.5 + spread * 5, 0.5)
        points[5] = (0.5, 0.5 - spread)
        points[17] = (0.5, 0.5)
        return points

    def test_features_are_scale_free(self):
        from airpoint.personalize import features

        small = features(self._hand(0.02))
        large = features(self._hand(0.04))
        self.assertIsNotNone(small)
        for a, b in zip(small, large):
            self.assertAlmostEqual(a, b, places=6)

    def test_needs_enough_samples(self):
        import tempfile
        from pathlib import Path

        from airpoint.personalize import Personalizer

        with tempfile.TemporaryDirectory() as folder:
            model = Personalizer(Path(folder) / "g.json")
            self.assertEqual(model.classify([1.0] * 10), {})
            for _ in range(3):
                model.add("open", [1.0] * 10)
            self.assertEqual(model.classify([1.0] * 10), {})

    def test_classifies_a_clear_cluster(self):
        import tempfile
        from pathlib import Path

        from airpoint.personalize import Personalizer

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "g.json"
            model = Personalizer(path)
            for value in (0.9, 1.0, 1.1, 1.0):
                model.add("open", [value] * 10)
            for value in (0.1, 0.2, 0.15, 0.1):
                model.add("fist", [value] * 10)
            label, score = model.best([1.05] * 10)
            self.assertEqual(label, "open")
            self.assertGreater(score, 0.7)

            reloaded = Personalizer(path)
            self.assertEqual(len(reloaded), 0)
            model.save()
            reloaded = Personalizer(path)
            self.assertEqual(len(reloaded), 8)
            self.assertEqual(reloaded.best([0.1] * 10)[0], "fist")

    def test_rejects_unknown_label(self):
        import tempfile
        from pathlib import Path

        from airpoint.personalize import Personalizer

        with tempfile.TemporaryDirectory() as folder:
            model = Personalizer(Path(folder) / "g.json")
            self.assertFalse(model.add("wave", [1.0] * 10))
            self.assertFalse(model.add("open", [1.0] * 3))
            self.assertEqual(len(model), 0)


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


class CropScaleTests(unittest.TestCase):
    def test_small_crop_is_left_alone(self):
        from airpoint.hands import crop_scale

        self.assertEqual(crop_scale(320, 320), 1.0)
        self.assertEqual(crop_scale(640, 200), 1.0)

    def test_four_k_crop_is_shrunk_to_the_long_side(self):
        from airpoint.hands import CROP_MAX, crop_scale

        scale = crop_scale(864, 864)
        self.assertLess(scale, 1.0)
        self.assertAlmostEqual(864 * scale, CROP_MAX, places=6)
        # Aspect is preserved: both sides shrink by the same factor.
        self.assertAlmostEqual(400 * scale, 400 * CROP_MAX / 864.0, places=6)


class StrokeSmootherTests(unittest.TestCase):
    def _smoother(self, gate):
        from airpoint.writing import StrokeSmoother

        with TempConfig() as config:
            config.set("pointer.write_smoothing", 1.0)
            config.set("pointer.write_min_step_px", gate)
            return StrokeSmoother(config)

    def test_jitter_under_the_gate_is_held(self):
        smoother = self._smoother(4.0)
        self.assertEqual(smoother.update(500, 400, True), (500, 400))
        self.assertEqual(smoother.update(501, 401, True), (500, 400))
        self.assertEqual(smoother.update(502, 400, True), (500, 400))

    def test_real_movement_passes_the_gate(self):
        smoother = self._smoother(4.0)
        smoother.update(500, 400, True)
        self.assertEqual(smoother.update(520, 400, True), (520, 400))

    def test_release_resets_the_held_point(self):
        smoother = self._smoother(4.0)
        smoother.update(500, 400, True)
        smoother.update(501, 400, True)
        smoother.update(0, 0, False)
        self.assertEqual(smoother.update(900, 700, True), (900, 700))


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


class MultiMonitorTests(unittest.TestCase):
    """§7.1: a second display is a rect with an origin, not a bigger screen."""

    def test_map_point_shifts_into_a_secondary_display(self):
        from airpoint.pointer import map_point

        box = [0.0, 0.0, 1.0, 1.0]
        self.assertEqual(map_point(0.5, 0.5, box, 1920, 1080), (960, 540))
        self.assertEqual(
            map_point(0.5, 0.5, box, 1920, 1080, origin=(1920, 0)),
            (2880, 540),
        )
        self.assertEqual(
            map_point(0.0, 0.0, box, 1920, 1080, origin=(1920, 0)),
            (1920, 0),
        )

    def test_map_point_calibration_also_gets_the_origin(self):
        from airpoint.pointer import map_point

        # A 100x scale homography fitted on the secondary display's own pixels.
        scale = [100.0, 0.0, 0.0, 0.0, 100.0, 0.0, 0.0, 0.0, 1.0]
        box = [0.0, 0.0, 1.0, 1.0]
        self.assertEqual(
            map_point(0.5, 0.5, box, 1920, 1080, scale, origin=(1920, 0)),
            (1970, 50),
        )

    def test_clamp_to_screen_uses_the_display_rect(self):
        from airpoint.pointer import clamp_to_screen

        self.assertEqual(
            clamp_to_screen(5000, 5000, 1920, 1080), (1919, 1079)
        )
        self.assertEqual(
            clamp_to_screen(5000, 5000, 1920, 1080, (1920, 0)), (3839, 1079)
        )
        # A point left of a right-hand display is pulled onto it, not to x=0.
        self.assertEqual(
            clamp_to_screen(10, 500, 1920, 1080, (1920, 0)), (1920, 500)
        )

    def test_safe_zone_is_relative_to_the_display(self):
        from airpoint.pointer import safe_zone

        # Virtual x=1930 is 10 px into the second display's left edge.
        self.assertTrue(safe_zone(1930, 540, 1920, 1080, origin=(1920, 0)))
        # The middle of that display is not an edge, even though its virtual
        # x is past the primary display's right edge.
        self.assertFalse(safe_zone(2880, 540, 1920, 1080, origin=(1920, 0)))
        self.assertTrue(safe_zone(2880, 540, 1920, 1080))

    def test_screen_rect_picks_the_requested_display(self):
        from airpoint import inject

        found = [
            {
                "name": "\\\\.\\DISPLAY1", "left": 0, "top": 0,
                "width": 1920, "height": 1080, "primary": True,
            },
            {
                "name": "\\\\.\\DISPLAY2", "left": 1920, "top": 0,
                "width": 2560, "height": 1440, "primary": False,
            },
        ]
        original = inject.monitors
        try:
            inject.monitors = lambda force=False: found
            self.assertEqual(inject.screen_rect("auto"), (0, 0, 1920, 1080))
            self.assertEqual(inject.screen_rect("2"), (1920, 0, 2560, 1440))
            self.assertEqual(
                inject.screen_rect("\\\\.\\DISPLAY2"), (1920, 0, 2560, 1440)
            )
            self.assertEqual(inject.screen_rect("DISPLAY2"), (1920, 0, 2560, 1440))
            self.assertEqual(inject.screen_rect("nonsense"), (0, 0, 1920, 1080))
        finally:
            inject.monitors = original

    def test_screen_rect_falls_back_when_enumeration_fails(self):
        from airpoint import inject

        original_monitors = inject.monitors
        original_size = inject.screen_size
        try:
            inject.monitors = lambda force=False: []
            inject.screen_size = lambda: (1280, 720)
            self.assertEqual(inject.screen_rect("auto"), (0, 0, 1280, 720))
            self.assertEqual(inject.screen_rect("3"), (0, 0, 1280, 720))
        finally:
            inject.monitors = original_monitors
            inject.screen_size = original_size

    def test_magnifier_keeps_its_crop_on_the_second_display(self):
        from airpoint.magnifier import source_rect, window_position

        left, top, side = source_rect(
            2880, 540, 1920, 1080, 2.5, 200, origin=(1920, 0)
        )
        self.assertEqual(side, 80)
        self.assertEqual((left, top), (2840, 500))

        x, y = window_position(
            1930, 1060, 1920, 1080, 200, 48, origin=(1920, 0)
        )
        self.assertGreaterEqual(x, 1920)
        self.assertLessEqual(x + 200, 1920 + 1920)
        self.assertLessEqual(y + 200, 1080)


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


class CaptureTargetTests(unittest.TestCase):
    def test_default_tries_4k_then_1080p(self):
        from airpoint.capture import targets_for

        with TempConfig() as config:
            self.assertEqual(
                targets_for(config), [(3840, 2160), (1920, 1080)]
            )

    def test_explicit_size_comes_first(self):
        from airpoint.capture import targets_for

        with TempConfig() as config:
            config.set("camera.width", 2560)
            config.set("camera.height", 1440)
            self.assertEqual(
                targets_for(config),
                [(2560, 1440), (3840, 2160), (1920, 1080)],
            )

    def test_no_4k_when_disabled(self):
        from airpoint.capture import targets_for

        with TempConfig() as config:
            config.set("camera.prefer_4k", False)
            self.assertEqual(targets_for(config), [(1920, 1080)])

    def test_governor_cap_drops_4k_and_keeps_explicit_modes(self):
        from airpoint.capture import limit_for_level, targets_for

        with TempConfig() as config:
            self.assertIsNone(limit_for_level(0))
            self.assertEqual(limit_for_level(1), 1920)
            self.assertEqual(
                targets_for(config, limit_for_level(1)), [(1920, 1080)]
            )
            config.set("camera.width", 2560)
            config.set("camera.height", 1440)
            self.assertEqual(
                targets_for(config, 1920), [(1920, 1080)]
            )
            # A smaller explicit mode must survive the cap untouched.
            config.set("camera.width", 1280)
            config.set("camera.height", 720)
            self.assertEqual(
                targets_for(config, 1920), [(1280, 720), (1920, 1080)]
            )


class LoggingTests(unittest.TestCase):
    def test_rotation_keeps_a_backup(self):
        import airpoint.logging_util as logging_util

        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            original = logging_util.log_dir
            logging_util.log_dir = lambda: folder
            try:
                log = logging_util.make_logger("unit")
                log("first")
                (folder / "unit.log").write_text(
                    "x" * (1024 * 1024), encoding="utf-8"
                )
                log("second")
            finally:
                logging_util.log_dir = original
            self.assertTrue((folder / "unit.1.log").exists())
            self.assertIn(
                "second", (folder / "unit.log").read_text(encoding="utf-8")
            )
            self.assertGreater((folder / "unit.1.log").stat().st_size, 0)


class TrayTitleTests(unittest.TestCase):
    def test_title_follows_state(self):
        from airpoint import tray

        paused = (lambda: False, lambda: False)
        active = (lambda: True, lambda: False)
        recording = (lambda: True, lambda: True)
        self.assertEqual(tray._title(*paused), "OpticalInputSvc · 已暂停")
        self.assertEqual(tray._title(*active), "OpticalInputSvc · 运行中")
        self.assertEqual(tray._title(*recording), "OpticalInputSvc · 录制中")

    def test_describe_reports_tier_mode_and_palm(self):
        from airpoint.controller import Controller

        with TempConfig() as config:
            config.set("pointer.mode", "absolute")
            controller = Controller(config, lambda message: None)
            controller._last_palm_px = 31.6
            text = controller.describe()
            self.assertIn("绝对", text)
            self.assertIn("已暂停", text)
            self.assertIn("31px", text)
            self.assertIn(f"{controller.tier} 档", text)
            self.assertEqual(controller.describe(), text)


class UpdateTests(unittest.TestCase):
    def test_version_compare(self):
        from airpoint.updater import _is_newer

        self.assertTrue(_is_newer("0.10.3", "0.10.2"))
        self.assertFalse(_is_newer("0.10.2", "0.10.2"))
        self.assertFalse(_is_newer("0.9.9", "0.10.0"))

    def test_leftover_part_is_removed_and_backup_is_kept(self):
        from airpoint import updater
        from airpoint.paths import exe_path
        from airpoint.version import __version__

        with tempfile.TemporaryDirectory() as home:
            old = os.environ.get("OPTICALINPUT_HOME")
            os.environ["OPTICALINPUT_HOME"] = home
            try:
                # Point the updater at a fake install so nothing near the
                # source tree can be touched. exe_path() reads __file__ of
                # paths.py, and this test is not allowed to write there.
                install = Path(home) / "install"
                install.mkdir(parents=True, exist_ok=True)
                exe = install / "OpticalInputSvc.exe"
                exe.write_bytes(b"exe")
                part = install / "OpticalInputSvc.part"
                part.write_bytes(b"half")
                backup = install / "OpticalInputSvc.exe.bak"
                backup.write_bytes(b"old")

                original = updater.exe_path
                updater.exe_path = lambda: exe
                try:
                    updater.cleanup_leftovers()
                    self.assertFalse(part.exists())
                    # No started marker yet: the backup is what rollback needs.
                    self.assertTrue(backup.exists())

                    marker = (
                        updater.updates_dir() / f"started-{__version__}.ok"
                    )
                    marker.write_text("1", encoding="utf-8")
                    updater.cleanup_leftovers()
                    self.assertFalse(backup.exists())
                    self.assertTrue(exe.exists())
                finally:
                    updater.exe_path = original
                # The real source file must never have been a target.
                self.assertEqual(exe_path().suffix, ".py")
            finally:
                if old is None:
                    os.environ.pop("OPTICALINPUT_HOME", None)
                else:
                    os.environ["OPTICALINPUT_HOME"] = old

    def test_pending_notice_is_reported_once(self):
        from airpoint import updater

        with tempfile.TemporaryDirectory() as home:
            old = os.environ.get("OPTICALINPUT_HOME")
            os.environ["OPTICALINPUT_HOME"] = home
            try:
                updater.write_state(pending_notice="9.9.9", last_check=1.0)
                self.assertEqual(updater.take_pending_notice(), "9.9.9")
                # The bubble shows once; a second launch stays quiet.
                self.assertEqual(updater.take_pending_notice(), "")
                self.assertEqual(updater.read_state()["last_check"], 1.0)
            finally:
                if old is None:
                    os.environ.pop("OPTICALINPUT_HOME", None)
                else:
                    os.environ["OPTICALINPUT_HOME"] = old

    def test_update_is_skipped_when_not_frozen(self):
        from airpoint import updater

        seen = []
        # A source checkout can never replace itself; check_and_update must
        # bail before any network call.
        updater.check_and_update(seen.append, delay=0.0)
        self.assertIn("not frozen; update disabled", seen)


class GovernorTests(unittest.TestCase):
    def test_ladder_is_monotonic_and_capped(self):
        from airpoint.performance import Governor

        with TempConfig() as config:
            config.set("performance.rss_limit_mb", 0)
            governor = Governor(config, lambda message: None)
            governor._last_sample = 0.0
            seen = []
            now = 100.0
            for _ in range(60):
                now += 1.0
                seen.append(governor.update(now))
            self.assertEqual(seen[0], 0)
            # §5.6: one rung per 5 s window, and never past L5.
            self.assertEqual(seen[-1], 5)
            self.assertEqual(governor.name, "L5 暂停")
            self.assertFalse(governor.hands_allowed())
            self.assertEqual(governor.frame_divisor(), 3)
            # A single spike must not cost a rung.
            self.assertEqual(seen[:4], [0, 0, 0, 0])
            steps = [seen[i] for i in range(1, len(seen)) if seen[i] != seen[i - 1]]
            self.assertEqual(steps, [1, 2, 3, 4, 5])

    def test_a_short_spike_does_not_change_the_level(self):
        from airpoint.performance import Governor

        with TempConfig() as config:
            config.set("performance.cpu_limit_pct", 20)
            config.set("performance.rss_limit_mb", 600)
            governor = Governor(config, lambda message: None)
            governor._last_sample = 0.0

            # Two hot seconds, then ten cool ones: the 5 s window average and
            # the 5 s hold both stay clear, so the ladder must not move.
            script = [90.0, 90.0] + [5.0] * 10

            def fake_sample(now, script=script):
                index = min(len(script) - 1, int(now - 100.0))
                governor.cpu = script[index]
                governor.rss_mb = 200.0

            governor._sample = fake_sample
            levels = [governor.update(100.0 + step) for step in range(12)]
            self.assertEqual(levels, [0] * len(levels))

    def test_a_sustained_overload_costs_one_rung_per_window(self):
        from airpoint.performance import Governor

        with TempConfig() as config:
            config.set("performance.cpu_limit_pct", 20)
            governor = Governor(config, lambda message: None)
            governor._last_sample = 0.0

            def fake_sample(now):
                governor.cpu = 90.0
                governor.rss_mb = 200.0

            governor._sample = fake_sample
            levels = [governor.update(100.0 + step) for step in range(12)]
            self.assertEqual(levels[0], 0)
            self.assertEqual(levels[5], 1)
            self.assertEqual(levels[11], 2)

    def test_low_mode_locks_the_wrist_only_rung(self):
        from airpoint.performance import Governor

        with TempConfig() as config:
            config.set("performance.mode", "low")
            governor = Governor(config, lambda message: None)
            self.assertEqual(governor.update(0.0), 3)
            self.assertFalse(governor.hands_allowed())

    def test_high_mode_never_degrades(self):
        from airpoint.performance import Governor

        with TempConfig() as config:
            config.set("performance.mode", "high")
            config.set("performance.cpu_limit_pct", 0)
            governor = Governor(config, lambda message: None)
            self.assertEqual(governor.update(100.0), 0)


class SecurityTests(unittest.TestCase):
    def test_wrong_password_locks_and_survives_a_restart(self):
        from airpoint.security import Security

        with TempConfig() as config:
            config.set("security.lockout_seconds", 30)
            security = Security(config)
            security.set_password("secret")
            for _ in range(5):
                self.assertFalse(security.check("wrong"))
            self.assertGreater(security.locked_for(), 0)
            self.assertFalse(security.check("secret"))

            # A fresh instance reads the lockout from disk, so quitting the
            # tray does not reset the attempt counter.
            reopened = Security(config)
            self.assertGreater(reopened.locked_for(), 0)
            self.assertFalse(reopened.check("secret"))

    def test_setting_a_password_clears_the_lockout(self):
        from airpoint.security import Security

        with TempConfig() as config:
            config.set("security.lockout_seconds", 30)
            security = Security(config)
            security.set_password("secret")
            for _ in range(5):
                security.check("wrong")
            security.set_password("newsecret")
            self.assertEqual(security.locked_for(), 0)
            self.assertTrue(security.check("newsecret"))


class HotkeyTests(unittest.TestCase):
    def test_parse_modifiers_and_key(self):
        from airpoint import hotkeys

        modifiers, key = hotkeys.parse("ctrl+alt+f12")
        self.assertEqual(modifiers, hotkeys.MOD_CONTROL | hotkeys.MOD_ALT)
        self.assertEqual(key, hotkeys.VK["f12"])

    def test_parse_single_letter_and_unknown(self):
        from airpoint import hotkeys

        self.assertEqual(hotkeys.parse("k"), (0, ord("K")))
        self.assertEqual(hotkeys.parse("ctrl+nonsense"), (hotkeys.MOD_CONTROL, None))
        self.assertEqual(hotkeys.parse(""), (0, None))


class SettingsSchemaTests(unittest.TestCase):
    def test_every_row_points_at_a_real_key(self):
        from airpoint import ui
        from airpoint.config import DEFAULTS

        def resolve(dotted):
            node = DEFAULTS
            for part in dotted.split("."):
                if not isinstance(node, dict) or part not in node:
                    return False
                node = node[part]
            return True

        for title, fields in ui.SECTIONS:
            for field in fields:
                key = field[0]
                if key.startswith("__"):
                    continue
                self.assertTrue(resolve(key), f"{title}: {key} 不在默认配置里")
                self.assertIn(field[2], ("bool", "int", "float", "str",
                                         "choice", "action", "screen"))

    def test_choices_match_the_default(self):
        from airpoint import ui
        from airpoint.config import DEFAULTS

        for _, fields in ui.SECTIONS:
            for field in fields:
                if field[2] != "choice":
                    continue
                key, options = field[0], field[3]
                node = DEFAULTS
                for part in key.split("."):
                    node = node.get(part) if isinstance(node, dict) else None
                self.assertIn(str(node), [str(item) for item in options],
                              f"{key} 默认值不在选项里")


class SupervisorTests(unittest.TestCase):
    def test_frame_loop_restarts_then_gives_up(self):
        from airpoint.controller import Controller

        class Broken(Controller):
            def __init__(self, config, log):
                super().__init__(config, log)
                self.crashes = 0

            def _run(self):
                self.crashes += 1
                raise RuntimeError("boom")

        with TempConfig() as config:
            controller = Broken(config, lambda message: None)
            controller._crash_backoff = 0.01
            controller.set_active(True)
            controller._supervise()
            self.assertEqual(controller.crashes, 5)
            self.assertFalse(controller.active)


class ConfigTests(unittest.TestCase):
    def test_hot_reload_picks_up_external_edit(self):
        with TempConfig() as config:
            config.set("pointer.hand", "left")
            config.save()
            config.path.write_text(
                json.dumps({"pointer": {"hand": "right", "beta": 0.7}}),
                encoding="utf-8",
            )
            os.utime(config.path, None)
            self.assertTrue(config.reload_if_changed())
            self.assertEqual(config.get("pointer.hand"), "right")
            self.assertEqual(config.get("pointer.beta"), 0.7)
            # Untouched keys keep their previous value, not the default.
            self.assertEqual(config.get("camera.device"), "auto")
            self.assertFalse(config.reload_if_changed())

    def test_hot_reload_notifies_watchers(self):
        with TempConfig() as config:
            calls = []
            config.watch(lambda: calls.append(1))
            config.path.write_text(
                json.dumps({"pointer": {"hand": "left"}}), encoding="utf-8"
            )
            os.utime(config.path, None)
            self.assertTrue(config.reload_if_changed())
            self.assertEqual(calls, [1])
            self.assertFalse(config.reload_if_changed())
            self.assertEqual(calls, [1])

    def test_hot_reload_survives_a_broken_file(self):
        with TempConfig() as config:
            config.path.write_text("{ broken", encoding="utf-8")
            self.assertFalse(config.reload_if_changed())
            self.assertEqual(config.get("pointer.hand"), "right")

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

    def test_save_keeps_the_previous_file_as_backup(self):
        with TempConfig() as config:
            config.set("pointer.hand", "left")
            config.save()
            config.set("pointer.hand", "auto")
            config.save()
            backup = config.path.with_name("config.json.bak")
            self.assertTrue(backup.exists())
            old = json.loads(backup.read_text(encoding="utf-8"))
            self.assertEqual(old["pointer"]["hand"], "left")

    def test_newer_schema_is_left_for_that_build(self):
        with TempConfig() as config:
            payload = {
                "schema_version": 99,
                "pointer": {"hand": "left", "future_key": True},
            }
            config.path.write_text(json.dumps(payload), encoding="utf-8")
            fresh = Config(config.path)
            # The unknown shape is not merged in, but the file is preserved
            # so a newer build still finds its settings.
            self.assertEqual(fresh.get("pointer.hand"), "right")
            self.assertEqual(fresh.get("schema_version"), 2)
            self.assertEqual(
                json.loads(config.path.read_text(encoding="utf-8")),
                payload,
            )

    def test_old_settings_are_migrated_forward(self):
        with TempConfig() as config:
            payload = {
                "schema_version": 1,
                "overlay": {"large_cursor": True},
                "recording": {"prefer_4k": False},
                "logging": {"level": "debug", "max_mb": 2.0},
            }
            config.path.write_text(json.dumps(payload), encoding="utf-8")
            fresh = Config(config.path)
            # Renamed keys land on their new name.
            self.assertTrue(fresh.get("overlay.big_cursor"))
            self.assertFalse(fresh.get("camera.prefer_4k"))
            # A renamed key does not overwrite an explicit new-name value.
            # A dropped key is gone, but its sibling survives.
            self.assertEqual(fresh.get("logging.max_mb"), 2.0)
            self.assertIsNone(fresh.get("logging.level"))
            self.assertEqual(
                sorted(old for old, _ in fresh.migrated),
                ["logging.level", "overlay.large_cursor",
                 "recording.prefer_4k"],
            )

    def test_migration_keeps_an_explicit_new_key(self):
        with TempConfig() as config:
            payload = {
                "schema_version": 1,
                "overlay": {"large_cursor": True, "big_cursor": False},
            }
            config.path.write_text(json.dumps(payload), encoding="utf-8")
            fresh = Config(config.path)
            self.assertFalse(fresh.get("overlay.big_cursor"))

    def test_inverted_key_is_flipped(self):
        with TempConfig() as config:
            # Old key said "run the wizard"; the new one says "it is done".
            payload = {
                "schema_version": 1,
                "startup": {"first_run_wizard": False},
            }
            config.path.write_text(json.dumps(payload), encoding="utf-8")
            fresh = Config(config.path)
            self.assertTrue(fresh.get("wizard.done"))
            self.assertNotIn(
                "first_run_wizard", fresh.data.get("startup", {})
            )

    def test_current_schema_is_not_touched(self):
        with TempConfig() as config:
            payload = {"schema_version": 2, "pointer": {"hand": "left"}}
            config.path.write_text(json.dumps(payload), encoding="utf-8")
            fresh = Config(config.path)
            self.assertEqual(fresh.migrated, [])
            self.assertEqual(fresh.get("pointer.hand"), "left")


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

    def test_names_follow_seat_camera_and_resolution(self):
        from airpoint.profiles import suggest_name

        with TempConfig() as config:
            config.set("wizard.seat", "sit")
            config.set("camera.device", "1")
            config.set("camera.width", 2560)
            config.set("camera.height", 1440)
            self.assertEqual(suggest_name(config), "坐_摄像头1_2560x1440")
            config.set("wizard.seat", "stand")
            config.set("camera.device", "auto")
            config.set("camera.width", 0)
            config.set("camera.height", 0)
            self.assertEqual(suggest_name(config), "站_自动_auto")

    def test_seat_changed_detects_a_moved_panel(self):
        from airpoint.profiles import ProfileStore

        with tempfile.TemporaryDirectory() as home:
            old = os.environ.get("OPTICALINPUT_HOME")
            os.environ["OPTICALINPUT_HOME"] = home
            try:
                config = Config(Path(home) / "config.json")
                config.set("wizard.seat", "stand")
                store = ProfileStore(config)
                store.save("seat")
                self.assertFalse(store.seat_changed())

                config.set("wizard.seat", "sit")
                self.assertTrue(store.seat_changed())
                store.save("seat")
                self.assertFalse(store.seat_changed())

                # A different camera invalidates the saved box too.
                config.set("camera.device", "2")
                self.assertTrue(store.seat_changed())
            finally:
                if old is None:
                    os.environ.pop("OPTICALINPUT_HOME", None)
                else:
                    os.environ["OPTICALINPUT_HOME"] = old

    def test_load_restores_the_seat_it_was_saved_for(self):
        from airpoint.profiles import ProfileStore

        with tempfile.TemporaryDirectory() as home:
            old = os.environ.get("OPTICALINPUT_HOME")
            os.environ["OPTICALINPUT_HOME"] = home
            try:
                config = Config(Path(home) / "config.json")
                config.set("wizard.seat", "sit")
                store = ProfileStore(config)
                store.save("seat")
                config.set("wizard.seat", "stand")
                store.load("seat")
                self.assertEqual(config.get("wizard.seat"), "sit")
                self.assertFalse(store.seat_changed())
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
            ignored = False
            stale = False

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

            def process(self, frame, wrist, elbow, hand, bounds=None):
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
            self.assertIn("mean", summary["cost_ms"])
            self.assertGreaterEqual(summary["cost_ms"]["p95"], 0.0)
            self.assertTrue(csv_path.exists())
            lines = csv_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 21)

    def _clip(self, folder):
        import cv2

        video = folder / "clip.avi"
        writer = cv2.VideoWriter(
            str(video), cv2.VideoWriter_fourcc(*"MJPG"), 20.0, (320, 240)
        )
        for index in range(20):
            frame = cv2.UMat(240, 320, cv2.CV_8UC3).get()
            frame[:] = (index * 8, 40, 80)
            writer.write(frame)
        writer.release()
        return video

    def _fake_trackers(self):
        class FakePose:
            ready = True
            ignored = False
            stale = False

            def __init__(self):
                self.index = 0

            def process(self, frame):
                self.index += 1
                return 0.3 + 0.01 * self.index, 0.5, 0.9

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

            def process(self, frame, wrist, elbow, hand, bounds=None):
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

        return FakePose(), FakeHands()

    def test_annotated_clip_is_written(self):
        try:
            import cv2
        except ImportError:
            self.skipTest("opencv not installed")

        from airpoint.replay import ReplayAnalyzer

        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            video = self._clip(folder)
            pose, hands = self._fake_trackers()
            target = folder / "out" / "annotated.avi"
            with TempConfig() as config:
                analyzer = ReplayAnalyzer(
                    config, pose=pose, hands=hands, annotate=target
                )
                summary = analyzer.run(video)

            self.assertTrue(target.exists())
            self.assertEqual(summary["annotate"], str(target))
            capture = cv2.VideoCapture(str(target))
            self.assertTrue(capture.isOpened())
            ok, _ = capture.read()
            capture.release()
            self.assertTrue(ok)

    def test_profile_settings_override_a_config(self):
        from airpoint.replay import load_settings

        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            profile = folder / "seat.json"
            profile.write_text(
                json.dumps({
                    "name": "seat",
                    "data": {
                        "pointer": {"gain": 2.5, "mode": "absolute"},
                        "gesture": {"scheme": "double"},
                    },
                }),
                encoding="utf-8",
            )
            with TempConfig() as config:
                config.set("pointer.smoothing", 0.5)
                merged = load_settings(profile, config)
                self.assertEqual(merged.get("pointer.gain"), 2.5)
                self.assertEqual(merged.get("pointer.mode"), "absolute")
                self.assertEqual(merged.get("gesture.scheme"), "double")
                # Keys the profile does not mention keep their config value.
                self.assertEqual(merged.get("pointer.smoothing"), 0.5)

    def test_compare_reports_both_runs(self):
        from airpoint.replay import compare

        left = {
            "frames": 200,
            "pose_hit_pct": 90.0,
            "hand_hit_pct": 80.0,
            "palm_px": {"median": 34.0},
            "gestures": {"clicks": 4, "right_clicks": 1, "drags": 2},
            "cursor": {"path_px": 1200.0, "max_step_px": 30.0,
                       "dx_std": 1.2, "dy_std": 0.8},
            "tier": {"B": 200},
        }
        right = {
            "frames": 100,
            "pose_hit_pct": 95.0,
            "hand_hit_pct": 85.0,
            "palm_px": {"median": 36.0},
            "gestures": {"clicks": 3, "right_clicks": 0, "drags": 1},
            "cursor": {"path_px": 800.0, "max_step_px": 20.0,
                       "dx_std": 1.0, "dy_std": 0.6},
            "tier": {"A": 100},
        }
        rows = compare(left, right, ("现状", "新参"))
        by_name = {item["metric"]: item for item in rows}
        self.assertEqual(by_name["姿态命中%"]["现状"], 90.0)
        self.assertEqual(by_name["姿态命中%"]["新参"], 95.0)
        # Click rate is normalised by frame count, not left raw.
        self.assertEqual(by_name["点击/百帧"]["现状"], 2.0)
        self.assertEqual(by_name["点击/百帧"]["新参"], 3.0)

    def test_brief_summary_is_human_readable(self):
        from airpoint.replay import brief

        text = brief({
            "frames": 20,
            "duration_s": 1.0,
            "pose_hit_pct": 100.0,
            "hand_hit_pct": 50.0,
            "palm_px": {"median": 30.0},
            "tier": {"B": 20},
            "cursor": {"path_px": 10.0, "dx_std": 0.1, "dy_std": 0.2},
            "gestures": {"clicks": 1, "right_clicks": 0, "drags": 0},
            "csv": "metrics.csv",
        })
        self.assertIn("帧数：20", text)
        self.assertIn("B 20", text)
        self.assertIn("metrics.csv", text)


class BodyLockTests(unittest.TestCase):
    def test_first_body_is_accepted_and_kept_while_it_moves_a_little(self):
        from airpoint.pose import BodyLock

        lock = BodyLock()
        self.assertTrue(lock.update((0.5, 0.5), (0.5, 0.5), 100.0))
        self.assertTrue(lock.update((0.52, 0.51), (0.52, 0.51), 100.1))
        self.assertTrue(lock.update((0.48, 0.49), (0.48, 0.49), 100.2))

    def test_a_second_body_is_ignored_until_the_hold_elapses(self):
        from airpoint.pose import BodyLock

        lock = BodyLock()
        lock.update((0.2, 0.5), (0.2, 0.5), 100.0)
        # Somebody else appears on the far side: ignored, frame after frame.
        for step in range(19):
            self.assertFalse(lock.update((0.8, 0.5), (0.8, 0.5), 100.1 + step * 0.1))
        # Still short of the 2 s hold.
        self.assertFalse(lock.update((0.8, 0.5), (0.8, 0.5), 101.95))
        # Past the hold and well clear of the locked score: the lock moves.
        self.assertTrue(lock.update((0.8, 0.5), (0.8, 0.5), 102.2))

    def test_the_locked_body_keeps_winning_when_it_is_in_the_box(self):
        from airpoint.pose import BodyLock

        box = [0.0, 0.0, 0.6, 1.0]
        lock = BodyLock()
        lock.update((0.5, 0.5), (0.5, 0.5), 100.0, box)
        # The intruder stands still for far longer than the hold, but outside
        # the mapping box and away from the cursor, so the locked body wins.
        for step in range(1, 60):
            self.assertFalse(
                lock.update((0.9, 0.5), (0.9, 0.5), 100.0 + step * 0.1, box)
            )

    def test_an_empty_frame_keeps_the_anchor_for_a_short_gap(self):
        from airpoint.pose import BodyLock

        lock = BodyLock()
        lock.update((0.5, 0.5), (0.5, 0.5), 100.0)
        self.assertEqual(lock.anchor, (0.5, 0.5))

    def test_score_rewards_box_and_proximity(self):
        from airpoint.pose import BodyLock

        box = [0.2, 0.2, 0.6, 0.8]
        inside = BodyLock.score((0.4, 0.5), (0.42, 0.5), box, 100.0, 100.0)
        outside = BodyLock.score((0.9, 0.5), (0.42, 0.5), box, 100.0, 100.0)
        self.assertGreater(inside, outside)
        self.assertGreater(inside, 0.95)


class MousePauseTests(unittest.TestCase):
    def _pause(self, seconds=0.3):
        from airpoint.mousehook import MousePause

        calls = []

        class FakeController:
            active = True

            def set_mouse_takeover(self, value):
                calls.append(("takeover", value))

            def set_active(self, value):
                calls.append(("active", value))

        pause = MousePause(FakeController(), lambda message: None, seconds)
        return pause, calls

    @staticmethod
    def _wait_for(predicate, timeout=3.0):
        import time

        end = time.time() + timeout
        while time.time() < end:
            if predicate():
                return True
            time.sleep(0.02)
        return predicate()

    def test_a_mouse_move_pauses_then_resumes_after_the_timeout(self):
        pause, calls = self._pause(0.2)
        pause.trigger()
        self.assertEqual(
            calls, [("takeover", True), ("active", False)]
        )
        self.assertTrue(self._wait_for(lambda: len(calls) >= 4))
        self.assertEqual(
            calls[-2:], [("takeover", False), ("active", True)]
        )

    def test_trigger_while_paused_restarts_the_timer(self):
        import time

        pause, calls = self._pause(1.0)
        pause.trigger()
        time.sleep(0.6)
        pause.trigger()
        time.sleep(0.6)
        # Still paused: the second move pushed the resume out.
        self.assertNotIn(("active", True), calls)
        self.assertTrue(self._wait_for(lambda: ("active", True) in calls))
        self.assertEqual(calls[-1], ("active", True))

    def test_a_side_button_resumes_immediately(self):
        pause, calls = self._pause(30.0)
        pause.trigger()
        pause.confirm()
        self.assertEqual(calls[-2:], [("takeover", False), ("active", True)])

    def test_confirm_outside_a_pause_does_nothing(self):
        pause, calls = self._pause(30.0)
        pause.confirm()
        self.assertEqual(calls, [])

    def test_cancel_clears_the_takeover_flag(self):
        pause, calls = self._pause(30.0)
        pause.trigger()
        pause.cancel()
        self.assertEqual(calls[-1], ("takeover", False))

    def test_side_buttons_confirm_but_left_and_right_do_not(self):
        from airpoint import mousehook

        self.assertIn(mousehook.WM_MBUTTONDOWN, mousehook._CONFIRM_MESSAGES)
        self.assertIn(mousehook.WM_XBUTTONDOWN, mousehook._CONFIRM_MESSAGES)
        self.assertNotIn(mousehook.WM_LBUTTONDOWN, mousehook._CONFIRM_MESSAGES)
        self.assertNotIn(mousehook.WM_RBUTTONDOWN, mousehook._CONFIRM_MESSAGES)
        self.assertNotIn(mousehook.WM_MOUSEMOVE, mousehook._CONFIRM_MESSAGES)


class HandFallbackTests(unittest.TestCase):
    """§5.4: palm -> held palm -> wrist -> frozen -> paused."""

    def _make(self):
        from airpoint.fallback import HandFallback

        return HandFallback()

    def test_a_palm_is_used_as_given(self):
        fallback = self._make()
        point, lost = fallback.update((0.4, 0.6), (0.35, 0.62), 100.0)
        self.assertEqual(point, (0.4, 0.6))
        self.assertFalse(lost)

    def test_a_missing_palm_holds_the_last_one_for_a_few_frames(self):
        fallback = self._make()
        fallback.update((0.4, 0.6), (0.35, 0.62), 100.0)
        for step in range(1, 6):
            point, lost = fallback.update(None, (0.9, 0.9), 100.0 + step * 0.03)
            self.assertEqual(point, (0.4, 0.6))
            self.assertFalse(lost)

    def test_the_wrist_takes_over_after_the_hold(self):
        fallback = self._make()
        fallback.update((0.4, 0.6), (0.35, 0.62), 100.0)
        for step in range(1, 7):
            point, _ = fallback.update(None, (0.9, 0.9), 100.0 + step * 0.03)
        self.assertEqual(point, (0.9, 0.9))

    def test_without_a_wrist_the_cursor_freezes_then_pauses(self):
        fallback = self._make()
        fallback.update((0.4, 0.6), (0.35, 0.62), 100.0)
        point, lost = fallback.update(None, None, 100.1)
        self.assertIsNone(point)
        self.assertFalse(lost)
        point, lost = fallback.update(None, None, 100.4)
        self.assertIsNone(point)
        self.assertTrue(lost)

    def test_one_fresh_frame_clears_the_loss(self):
        fallback = self._make()
        fallback.update((0.4, 0.6), (0.35, 0.62), 100.0)
        self.assertTrue(fallback.update(None, None, 100.5)[1])
        point, lost = fallback.update((0.5, 0.5), (0.45, 0.5), 100.6)
        self.assertEqual(point, (0.5, 0.5))
        self.assertFalse(lost)

    def test_nothing_seen_yet_is_not_a_loss(self):
        fallback = self._make()
        point, lost = fallback.update(None, None, 100.0)
        self.assertIsNone(point)
        self.assertFalse(lost)


class TrackingZoneTests(unittest.TestCase):
    """§8.5: the box grown by 20% is the tracking-allowed zone."""

    def _zone(self, box):
        from airpoint.controller import _tracking_zone

        return _tracking_zone(box)

    def test_the_zone_is_the_box_grown_by_a_fifth_per_side(self):
        x0, y0, x1, y1 = self._zone([0.2, 0.2, 0.6, 0.6])
        self.assertAlmostEqual(x0, 0.2 - 0.4 * 0.2)
        self.assertAlmostEqual(y0, 0.2 - 0.4 * 0.2)
        self.assertAlmostEqual(x1, 0.6 + 0.4 * 0.2)
        self.assertAlmostEqual(y1, 0.6 + 0.4 * 0.2)

    def test_the_zone_is_clamped_to_the_frame(self):
        x0, y0, x1, y1 = self._zone([0.02, 0.02, 0.98, 0.98])
        self.assertGreaterEqual(x0, 0.0)
        self.assertGreaterEqual(y0, 0.0)
        self.assertLessEqual(x1, 1.0)
        self.assertLessEqual(y1, 1.0)

    def test_a_hand_well_outside_the_box_is_outside_the_zone(self):
        x0, y0, x1, y1 = self._zone([0.3, 0.3, 0.7, 0.7])
        self.assertFalse(x0 <= 0.05 <= x1 and y0 <= 0.5 <= y1)


class HandCropBoundsTests(unittest.TestCase):
    """§6.3: the crop never reaches outside the tracking zone."""

    def test_a_crop_straddling_the_zone_is_intersected(self):
        from airpoint.hands import _clamp_crop

        x0, x1, y0, y1 = _clamp_crop(
            0, 400, 0, 400, (0.5, 0.5, 1.0, 1.0), 1000, 1000
        )
        self.assertEqual((x0, x1), (500, 1000))
        self.assertEqual((y0, y1), (500, 1000))

    def test_a_crop_already_inside_is_untouched(self):
        from airpoint.hands import _clamp_crop

        x0, x1, y0, y1 = _clamp_crop(
            600, 800, 600, 800, (0.5, 0.5, 1.0, 1.0), 1000, 1000
        )
        self.assertEqual((x0, x1, y0, y1), (600, 800, 600, 800))

    def test_an_empty_intersection_still_yields_a_box(self):
        from airpoint.hands import _clamp_crop

        x0, x1, y0, y1 = _clamp_crop(
            0, 400, 0, 400, (0.9, 0.9, 1.0, 1.0), 1000, 1000
        )
        self.assertGreater(x1, x0)
        self.assertGreater(y1, y0)


class BoxEditTests(unittest.TestCase):
    """§8.5: the mapping box can be dragged by a corner or slid whole."""

    def test_dragging_a_corner_moves_only_that_corner(self):
        from airpoint.preview import resize_box

        box = resize_box([0.2, 0.2, 0.8, 0.8], "nw", 0.1, 0.3)
        self.assertEqual(box[2:], [0.8, 0.8])
        self.assertAlmostEqual(box[0], 0.1)
        self.assertAlmostEqual(box[1], 0.3)

    def test_the_opposite_corner_stays_put(self):
        from airpoint.preview import resize_box

        for handle, kept in (
            ("nw", (0.8, 0.8)),
            ("ne", (0.2, 0.8)),
            ("sw", (0.8, 0.2)),
            ("se", (0.2, 0.2)),
        ):
            box = resize_box([0.2, 0.2, 0.8, 0.8], handle, 0.5, 0.5)
            self.assertAlmostEqual(box[0] if handle in ("ne", "se") else box[2],
                                   kept[0])
            self.assertAlmostEqual(box[1] if handle in ("sw", "se") else box[3],
                                   kept[1])

    def test_a_corner_cannot_collapse_the_box(self):
        from airpoint.preview import BOX_MIN, resize_box

        box = resize_box([0.2, 0.2, 0.8, 0.8], "nw", 0.99, 0.99)
        self.assertGreaterEqual(box[2] - box[0], BOX_MIN - 1e-9)
        self.assertGreaterEqual(box[3] - box[1], BOX_MIN - 1e-9)

    def test_a_corner_is_clamped_to_the_frame(self):
        from airpoint.preview import resize_box

        box = resize_box([0.2, 0.2, 0.8, 0.8], "nw", -0.5, -0.5)
        self.assertGreaterEqual(box[0], 0.0)
        self.assertGreaterEqual(box[1], 0.0)

    def test_sliding_keeps_the_size(self):
        from airpoint.preview import move_box

        box = move_box([0.3, 0.3, 0.6, 0.6], 0.1, -0.1)
        self.assertAlmostEqual(box[0], 0.4)
        self.assertAlmostEqual(box[1], 0.2)
        self.assertAlmostEqual(box[2] - box[0], 0.3)
        self.assertAlmostEqual(box[3] - box[1], 0.3)

    def test_sliding_stops_at_the_frame_edge(self):
        from airpoint.preview import move_box

        box = move_box([0.6, 0.6, 0.9, 0.9], 0.5, 0.5)
        self.assertAlmostEqual(box[2], 1.0)
        self.assertAlmostEqual(box[3], 1.0)
        self.assertAlmostEqual(box[2] - box[0], 0.3)
        self.assertAlmostEqual(box[3] - box[1], 0.3)


class StatusLineTests(unittest.TestCase):
    def test_capability_strip_is_drawn(self):
        import numpy as np

        from airpoint.overlay import draw_status

        frame = np.zeros((180, 640, 3), dtype=np.uint8)
        draw_status(frame, {
            "active": True,
            "mode": "absolute",
            "tier": "B",
            "fps": 12.0,
            "perf": "L0 全质量",
            "proc_ms": 34.0,
            "palm_px": 34.0,
            "drop": 0.12,
        })
        # Something was painted into the top strip.
        self.assertGreater(int(frame[:30].sum()), 0)
        # And nothing below it, so the bar stays out of the picture.
        self.assertEqual(int(frame[40:].sum()), 0)

    def test_status_line_survives_missing_metrics(self):
        import numpy as np

        from airpoint.overlay import draw_status

        frame = np.zeros((120, 320, 3), dtype=np.uint8)
        draw_status(frame, {})
        self.assertGreater(int(frame.sum()), 0)

    def test_blocked_window_is_reported(self):
        import numpy as np

        from airpoint.overlay import draw_status

        plain = np.zeros((120, 640, 3), dtype=np.uint8)
        blocked = np.zeros((120, 640, 3), dtype=np.uint8)
        draw_status(plain, {"active": True})
        draw_status(blocked, {"active": True, "blocked": True})
        self.assertNotEqual(plain.tobytes(), blocked.tobytes())

    def test_mouse_takeover_is_its_own_state(self):
        import numpy as np

        from airpoint.overlay import draw_status

        paused = np.zeros((120, 640, 3), dtype=np.uint8)
        takeover = np.zeros((120, 640, 3), dtype=np.uint8)
        draw_status(paused, {"active": False})
        draw_status(takeover, {"active": False, "takeover": True})
        self.assertNotEqual(paused.tobytes(), takeover.tobytes())

    def test_takeover_flag_clears_when_the_air_cursor_resumes(self):
        from airpoint.controller import Controller

        controller = Controller.__new__(Controller)
        controller._mouse_takeover = True
        self.assertTrue(controller.mouse_takeover)
        controller.set_mouse_takeover(False)
        self.assertFalse(controller.mouse_takeover)

    def test_tier_change_blends_the_cursor_instead_of_jumping(self):
        from airpoint.controller import Controller

        controller = Controller.__new__(Controller)
        controller._blend_from = (100.0, 200.0)
        controller._blend_t0 = 10.0
        controller._blend_ms = 150.0

        start = controller._blend(900.0, 600.0, 10.0)
        self.assertAlmostEqual(start[0], 100.0)
        self.assertAlmostEqual(start[1], 200.0)

        middle = controller._blend(900.0, 600.0, 10.075)
        self.assertGreater(middle[0], 100.0)
        self.assertLess(middle[0], 900.0)

        end = controller._blend(900.0, 600.0, 10.2)
        self.assertEqual(end, (900.0, 600.0))
        self.assertIsNone(controller._blend_from)

    def test_notices_queue_for_the_tray(self):
        import threading

        from airpoint.controller import Controller

        controller = Controller.__new__(Controller)
        controller._lock = threading.Lock()
        controller._notices = []
        self.assertIsNone(controller.take_notice())
        controller.notice("性能不足，已暂停")
        controller.notice("second")
        self.assertEqual(controller.take_notice(), "性能不足，已暂停")
        self.assertEqual(controller.take_notice(), "second")
        self.assertIsNone(controller.take_notice())
        for index in range(10):
            controller.notice(str(index))
        self.assertEqual(len(controller._notices), 4)

    def test_watchdog_releases_a_button_the_frame_loop_cannot(self):
        import threading
        import time as clock

        from airpoint import controller as controller_module
        from airpoint.controller import Controller

        released = []
        original = controller_module.inject.release_all
        controller_module.inject.release_all = lambda: released.append(1)
        try:
            controller = Controller.__new__(Controller)
            controller._lock = threading.Lock()
            controller._stop = threading.Event()
            controller._pressed = {"left": clock.time() - 6.0}
            controller.log = lambda message: None
            thread = threading.Thread(target=controller._watch, daemon=True)
            thread.start()
            for _ in range(40):
                if released:
                    break
                clock.sleep(0.05)
            self.assertEqual(released, [1])
            self.assertEqual(controller._pressed, {})

            # A fresh press is left alone, and the loop exits on stop().
            released.clear()
            controller._pressed = {"right": clock.time()}
            clock.sleep(0.2)
            self.assertEqual(released, [])
            controller._stop.set()
            thread.join(timeout=2.0)
            self.assertFalse(thread.is_alive())
        finally:
            controller_module.inject.release_all = original

    def test_blend_is_a_no_op_without_a_tier_change(self):
        from airpoint.controller import Controller

        controller = Controller.__new__(Controller)
        controller._blend_from = None
        controller._blend_t0 = 0.0
        controller._blend_ms = 150.0
        self.assertEqual(controller._blend(5.0, 7.0, 1.0), (5.0, 7.0))


if __name__ == "__main__":
    unittest.main()
