import unittest
from unittest.mock import patch

from backend import main as api


class ApiSafetyGateTests(unittest.TestCase):
    def test_stale_perception_does_not_authorize_voice_or_release_routine_alerts(self):
        with patch.object(api, "last_frame_received_at", None), patch.object(
            api, "latest_perception", {"eyes_on_road": "Yes", "face_detected": True}
        ), patch.object(api, "profile_enrollment_id", None), patch.object(
            api, "queued_notifications", []
        ), patch.object(api, "released_notifications", []):
            authorization = api.authorize_voice_command(api.VoiceCommandRequest(command="next track"))
            self.assertFalse(authorization["authorized"])
            notification = api.submit_notification(
                api.NotificationRequest(title="Routine", priority="non-critical")
            )
            self.assertEqual(notification["status"], "queued")

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
        ), patch.object(api, "released_notifications", []), patch.object(
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
