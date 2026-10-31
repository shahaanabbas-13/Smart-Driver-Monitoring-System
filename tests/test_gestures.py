import unittest

from backend.gestures import GestureActionGate


class GestureActionGateTests(unittest.TestCase):
    def test_only_stable_thumbs_up_emits_dismiss_alarm(self):
        gate = GestureActionGate(stable_frames=3)
        self.assertFalse(gate.process("Thumbs Up", True)["triggered"])
        self.assertFalse(gate.process("Thumbs Up", True)["triggered"])
        third = gate.process("Thumbs Up", True)
        self.assertEqual(third["action"], "dismiss alarm")
        self.assertTrue(third["authorized"])
        self.assertFalse(gate.process("Thumbs Up", True)["triggered"])

    def test_dismiss_requires_eyes_on_road(self):
        gate = GestureActionGate(stable_frames=2)
        gate.process("Thumbs Up", False)
        denied = gate.process("Thumbs Up", False)
        self.assertTrue(denied["triggered"])
        self.assertFalse(denied["authorized"])
        self.assertIsNone(denied["action"])
        self.assertEqual(denied["reason"], "Dismiss alarm requires Eyes on road = Yes")

    def test_open_palm_and_fist_have_no_gesture_action(self):
        gate = GestureActionGate(stable_frames=1)
        for pose in ("Open Palm", "Fist", "Other"):
            result = gate.process(pose, False)
            self.assertFalse(result["triggered"])
            self.assertIsNone(result["action"])


if __name__ == "__main__":
    unittest.main()
