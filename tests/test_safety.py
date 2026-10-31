import unittest
from unittest.mock import patch

from backend.safety import SafetyEngine


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds=0.35):
        self.now += seconds


def frame(*, yaw=0.0, pitch=0.0, gaze_h=0.5, gaze_v=0.5, frontal=0.9,
          mar=0.2, ear=0.3, face=True):
    return {
        "face_detected": face,
        "ear": ear if face else None,
        "mar": mar if face else None,
        "gaze": {"horizontal": gaze_h, "vertical": gaze_v} if face else None,
        "frontal_score": frontal if face else None,
        "head_pose": {
            "yaw_degrees": yaw if face else None,
            "pitch_degrees": pitch if face else None,
        },
    }


class SafetyEngineTests(unittest.TestCase):
    def test_resting_mouth_ratio_does_not_count_as_yawn(self):
        clock = FakeClock()
        with patch("backend.safety.monotonic", clock):
            engine = SafetyEngine()
            for _ in range(20):
                state = engine.update(frame(mar=0.39))
                clock.advance(0.25)
            self.assertFalse(state["yawning"])
            self.assertEqual(state["yawn_count_60s"], 0)
            self.assertEqual(state["drowsiness"], "Inactive")

    def test_distraction_alarm_waits_for_four_confirming_frames(self):
        clock = FakeClock()
        with patch("backend.safety.monotonic", clock):
            engine = SafetyEngine()
            for _ in range(engine.ATTENTION_CALIBRATION_SAMPLES):
                state = engine.update(frame())
                clock.advance()

            state = engine.update(frame())
            self.assertEqual(state["eyes_on_road"], "Yes")
            away = frame(yaw=30.0, gaze_h=0.78, frontal=0.55)

            for _ in range(5):
                clock.advance()
                state = engine.update(away)
                self.assertFalse(state["alarm_active"])

            clock.advance()
            state = engine.update(away)
            self.assertEqual(state["distraction"], "Distracted")
            self.assertFalse(state["alarm_active"])
            self.assertEqual(state["alarm_confirmation_frames"], 1)

            for expected_frame in (2, 3):
                clock.advance()
                state = engine.update(away)
                self.assertFalse(state["alarm_active"])
                self.assertEqual(state["alarm_confirmation_frames"], expected_frame)

            clock.advance()
            state = engine.update(away)
            self.assertTrue(state["alarm_active"])
            self.assertEqual(state["alarm_confirmation_frames"], 4)

    def test_missing_face_alarm_requires_four_consecutive_frames(self):
        clock = FakeClock()
        with patch("backend.safety.monotonic", clock):
            engine = SafetyEngine()
            for index in range(4):
                state = engine.update(frame(face=False))
                self.assertEqual(state["alarm_active"], index == 3)
                self.assertEqual(state["alarm_confirmation_frames"], index + 1)
                clock.advance()

            for _ in range(4):
                state = engine.update(frame(face=True))
                self.assertFalse(state["alarm_active"])
                clock.advance()
            for index in range(4):
                state = engine.update(frame(face=False))
                self.assertEqual(state["alarm_active"], index == 3)
                clock.advance()

    def test_three_separate_yawns_mark_drowsy_and_alarm_waits_five_seconds(self):
        clock = FakeClock()
        with patch("backend.safety.monotonic", clock):
            engine = SafetyEngine()
            state = None
            for _ in range(3):
                for _ in range(3):
                    state = engine.update(frame(mar=0.6))
                    clock.advance(0.25)
                for _ in range(4):
                    state = engine.update(frame(mar=0.2))
                    clock.advance(0.25)

            self.assertEqual(state["yawn_count_60s"], 3)
            self.assertEqual(state["drowsiness"], "Drowsy")
            self.assertFalse(state["alarm_active"])

            # Keep the detector cadence regular while the drowsy condition holds.
            for _ in range(10):
                state = engine.update(frame(mar=0.2))
                self.assertFalse(state["alarm_active"])
                clock.advance(0.4)

            for index in range(4):
                state = engine.update(frame(mar=0.2))
                self.assertEqual(state["alarm_active"], index == 3)
                clock.advance(0.35)


if __name__ == "__main__":
    unittest.main()
