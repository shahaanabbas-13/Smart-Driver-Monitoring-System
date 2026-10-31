import unittest

from backend.gestures import GestureActionGate


class GestureActionGateTests(unittest.TestCase):
    def test_gesture_emits_once_after_stability_and_requires_eyes_on_road(self):
        gate = GestureActionGate(stable_frames=3)
        first = gate.process("Open Palm", True)
        second = gate.process("Open Palm", True)
        third = gate.process("Open Palm", True)

        self.assertFalse(first["triggered"])
        self.assertFalse(second["triggered"])
        self.assertEqual(third["action"], "next track")
        self.assertTrue(third["authorized"])
        self.assertFalse(gate.process("Open Palm", True)["triggered"])

        gate.reset()
        for _ in range(2):
            gate.process("Thumbs Up", False)
        denied = gate.process("Thumbs Up", False)
        self.assertTrue(denied["triggered"])
        self.assertFalse(denied["authorized"])
        self.assertIsNone(denied["action"])


if __name__ == "__main__":
    unittest.main()
