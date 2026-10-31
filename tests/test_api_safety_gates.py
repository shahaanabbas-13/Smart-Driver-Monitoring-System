import unittest
from unittest.mock import patch

from backend import main as api


class ApiSafetyGateTests(unittest.TestCase):
    def test_occupant_alert_requires_nine_continuous_seconds_and_clears_on_absence_or_ignition(self):
        clock = [100.0]
        state = {"face_detected": True}
        with patch("backend.main.monotonic", lambda: clock[0]), patch.object(
            api, "perception_is_fresh", lambda: True
        ), patch.object(api, "latest_perception", state), patch.object(
            api, "ignition_on", False
        ), patch.object(api, "profile_enrollment_id", None), patch.object(
            api, "occupant_presence_since", None
        ), patch.object(api, "occupant_left_behind", False):
            api.update_occupant_state()
            self.assertFalse(state["occupant_left_behind"])
            self.assertEqual(state["occupant_countdown_seconds"], 9.0)

            clock[0] += 9.0
            api.update_occupant_state()
            self.assertTrue(state["occupant_left_behind"])

            state["face_detected"] = False
            api.update_occupant_state()
            self.assertFalse(state["occupant_left_behind"])
            self.assertIsNone(state["occupant_countdown_seconds"])

            state["face_detected"] = True
            api.update_occupant_state()
            self.assertFalse(state["occupant_left_behind"])
            clock[0] += 8.9
            api.update_occupant_state()
            self.assertFalse(state["occupant_left_behind"])
            clock[0] += 0.11
            api.update_occupant_state()
            self.assertTrue(state["occupant_left_behind"])

            with patch.object(api, "ignition_on", True):
                api.update_occupant_state()
            self.assertFalse(state["occupant_left_behind"])

    def test_voice_actions_work_without_eyes_on_road_but_dismiss_does_not(self):
        with patch.object(api, "last_frame_received_at", 100.0), patch.object(
            api, "latest_perception", {"eyes_on_road": "No", "face_detected": True}
        ), patch.object(api, "profile_enrollment_id", None), patch(
            "backend.main.monotonic", lambda: 100.1
        ):
            for command in ("next track", "phone call"):
                result = api.authorize_voice_command(api.VoiceCommandRequest(command=command))
                self.assertTrue(result["authorized"])
                self.assertEqual(result["reason"], "Eyes on road is not required for this command")
            dismissal = api.authorize_voice_command(
                api.VoiceCommandRequest(command="dismiss alarm")
            )
            self.assertFalse(dismissal["authorized"])
            self.assertEqual(dismissal["reason"], "Dismiss alarm requires Eyes on road = Yes")

    def test_stale_perception_allows_non_dismiss_actions_but_rejects_dismissal(self):
        with patch.object(api, "last_frame_received_at", None), patch.object(
            api, "latest_perception", {"eyes_on_road": "Yes", "face_detected": True}
        ), patch.object(api, "profile_enrollment_id", None):
            authorization = api.authorize_voice_command(api.VoiceCommandRequest(command="next track"))
            self.assertTrue(authorization["authorized"])
            dismissal = api.authorize_voice_command(api.VoiceCommandRequest(command="dismiss alarm"))
            self.assertFalse(dismissal["authorized"])

    def test_stale_camera_reports_unavailable_instead_of_old_face_state(self):
        state = {
            "presence": "Detected",
            "face_detected": True,
            "eyes_on_road": "Yes",
            "yawning": True,
            "alarm_active": True,
            "alarm_escalation": "warning",
            "occupant_left_behind": False,
        }
        with patch.object(api, "last_frame_received_at", None), patch.object(
            api, "latest_perception", state
        ), patch.object(
            api, "update_occupant_state", lambda: None
        ):
            snapshot = api.cockpit_status()
        self.assertFalse(snapshot["camera_connected"])
        self.assertFalse(snapshot["face_detected"])
        self.assertEqual(snapshot["presence"], "Waiting")
        self.assertEqual(snapshot["eyes_on_road"], "Unknown")
        self.assertFalse(snapshot["yawning"])
        self.assertFalse(snapshot["alarm_active"])


if __name__ == "__main__":
    unittest.main()
