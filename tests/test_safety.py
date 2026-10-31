import unittest
from unittest.mock import patch

from backend.safety import SafetyEngine


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds=0.25):
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
    def run_frame(self, engine, clock, sample, step=0.25):
        state = engine.update(sample)
        clock.advance(step)
        return state

    def confirm_yawn(self, engine, clock):
        for _ in range(engine.YAWN_CONFIRM_FRAMES):
            state = self.run_frame(engine, clock, frame(mar=0.60))
        self.assertTrue(state["yawning"])
        for _ in range(3):
            state = self.run_frame(engine, clock, frame(mar=0.20))
        return state

    def test_resting_mouth_ratio_does_not_count_as_yawn(self):
        clock = FakeClock()
        with patch("backend.safety.monotonic", clock):
            engine = SafetyEngine()
            for _ in range(20):
                state = self.run_frame(engine, clock, frame(mar=0.39))
            self.assertFalse(state["yawning"])
            self.assertEqual(state["yawn_count_60s"], 0)
            self.assertEqual(state["drowsiness"], "Inactive")

    def test_alarm_waits_for_five_consecutive_distraction_frames(self):
        clock = FakeClock()
        with patch("backend.safety.monotonic", clock):
            engine = SafetyEngine()
            for _ in range(engine.ATTENTION_CALIBRATION_SAMPLES):
                state = self.run_frame(engine, clock, frame())
            away = frame(yaw=30.0, gaze_h=0.78, frontal=0.55)
            for _ in range(7):
                state = self.run_frame(engine, clock, away)
                self.assertFalse(state["alarm_active"])
            while state["distraction"] != "Distracted":
                state = self.run_frame(engine, clock, away)
            self.assertEqual(state["alarm_confirmation_frames"], 1)
            for expected in range(2, 5):
                state = self.run_frame(engine, clock, away)
                self.assertFalse(state["alarm_active"])
                self.assertEqual(state["alarm_confirmation_frames"], expected)
            state = self.run_frame(engine, clock, away)
            self.assertTrue(state["alarm_active"])
            self.assertEqual(state["alarm_confirmation_frames"], 5)
            self.assertFalse(state["pull_over_triggered"])

    def test_missing_face_alarm_requires_five_consecutive_frames(self):
        clock = FakeClock()
        with patch("backend.safety.monotonic", clock):
            engine = SafetyEngine()
            for index in range(5):
                state = self.run_frame(engine, clock, frame(face=False))
                self.assertEqual(state["alarm_active"], index == 4)
                self.assertEqual(state["alarm_confirmation_frames"], index + 1)
            state = self.run_frame(engine, clock, frame(face=True))
            self.assertFalse(state["alarm_active"])

    def test_pull_over_only_after_five_yawns_confirmed_by_four_frames_each(self):
        clock = FakeClock()
        with patch("backend.safety.monotonic", clock):
            engine = SafetyEngine()
            for count in range(1, 6):
                state = self.confirm_yawn(engine, clock)
                self.assertEqual(state["yawn_count_60s"], count)
                self.assertEqual(state["pull_over_triggered"], count >= 5)
                # End the current yawn before starting the next one.
                for _ in range(3):
                    state = self.run_frame(engine, clock, frame(mar=0.20))
            self.assertEqual(state["pull_over_reason"], "repeated_yawning")
            self.assertEqual(state["pull_over_safety_check"], "assumed_clear_demo")

    def test_distraction_alone_never_triggers_pull_over(self):
        clock = FakeClock()
        with patch("backend.safety.monotonic", clock):
            engine = SafetyEngine()
            for _ in range(engine.ATTENTION_CALIBRATION_SAMPLES):
                state = self.run_frame(engine, clock, frame())
            for _ in range(30):
                state = self.run_frame(engine, clock, frame(yaw=35, gaze_h=0.8, frontal=0.5))
            self.assertEqual(state["distraction"], "Distracted")
            self.assertFalse(state["pull_over_triggered"])
            self.assertEqual(state["yawn_count_60s"], 0)


if __name__ == "__main__":
    unittest.main()
