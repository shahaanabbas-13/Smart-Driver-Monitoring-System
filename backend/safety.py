from collections import deque
from time import monotonic


class SafetyEngine:
    """Temporal filtering and safety decisions for successive perception frames."""

    DROWSINESS_CONFIRM_SECONDS = 5.0
    PULL_OVER_AFTER_WARNING_SECONDS = 10.0

    def __init__(self):
        self.reset()

    def reset(self):
        self.closed_since = None
        self.yawn_since = None
        self.drowsy_candidate_since = None
        self.lookaway_since = None
        self.alarm_since = None
        self.yawns = deque()
        self.yawn_active = False
        self.drowsy = False
        self.distracted = False
        self.baseline = None
        self.baseline_samples = 0
        self.last_frame_at = None

    def update(self, result):
        now = monotonic()
        # A long gap means the camera was paused or the service stalled; do not
        # accumulate that interval as if the driver's eyes/head stayed fixed.
        if self.last_frame_at is not None and now - self.last_frame_at > 1.5:
            self.closed_since = self.yawn_since = self.lookaway_since = None
            self.drowsy_candidate_since = None
        self.last_frame_at = now

        if not result["face_detected"]:
            self.closed_since = self.yawn_since = self.lookaway_since = None
            self.drowsy_candidate_since = None
            self.drowsy = self.distracted = False
            self.alarm_since = None
            return self._state(False, None, None, None)

        ear, mar = result["ear"], result["mar"]
        pose = result["head_pose"]
        yaw, pitch = pose["yaw_degrees"], pose["pitch_degrees"]

        eye_closure_candidate = False
        if ear is not None:
            if ear < 0.21:
                self.closed_since = self.closed_since or now
                if now - self.closed_since >= 1.2:
                    eye_closure_candidate = True
            else:
                self.closed_since = None

        if mar is not None and mar > 0.68:
            self.yawn_since = self.yawn_since or now
            if now - self.yawn_since >= 1.0 and not self.yawn_active:
                self.yawns.append(now)
                self.yawn_active = True
        else:
            self.yawn_since = None
            self.yawn_active = False
        while self.yawns and now - self.yawns[0] > 60:
            self.yawns.popleft()
        repeated_yawns = len(self.yawns) >= 3
        drowsiness_candidate = eye_closure_candidate or repeated_yawns
        if drowsiness_candidate:
            self.drowsy_candidate_since = self.drowsy_candidate_since or now
            if now - self.drowsy_candidate_since >= self.DROWSINESS_CONFIRM_SECONDS:
                self.drowsy = True
        else:
            self.drowsy_candidate_since = None
            if ear is not None and ear > 0.24 and (mar is None or mar < 0.55):
                self.drowsy = False

        eyes_on_road = None
        if yaw is not None and pitch is not None:
            if self.baseline is None:
                self.baseline = [yaw, pitch]
                self.baseline_samples = 1
            elif self.baseline_samples < 30:
                n = self.baseline_samples
                self.baseline[0] += (yaw - self.baseline[0]) / (n + 1)
                self.baseline[1] += (pitch - self.baseline[1]) / (n + 1)
                self.baseline_samples += 1
            yaw_delta = abs(yaw - self.baseline[0])
            pitch_delta = abs(pitch - self.baseline[1])
            looking_away = self.baseline_samples >= 10 and (
                yaw_delta > 22 or pitch_delta > 18
            )
            eyes_on_road = not looking_away if self.baseline_samples >= 10 else None
            if looking_away:
                self.lookaway_since = self.lookaway_since or now
                if now - self.lookaway_since >= 1.5:
                    self.distracted = True
            else:
                self.lookaway_since = None
                if yaw_delta < 16 and pitch_delta < 13:
                    self.distracted = False
                    # Slowly adapt to normal posture only while centered.
                    self.baseline[0] = self.baseline[0] * 0.995 + yaw * 0.005
                    self.baseline[1] = self.baseline[1] * 0.995 + pitch * 0.005

        alert = self.drowsy or self.distracted
        self.alarm_since = (self.alarm_since or now) if alert else None
        return self._state(True, eyes_on_road, ear, mar, yaw, pitch, now)

    def _state(self, present, eyes, ear, mar, yaw=None, pitch=None, now=None):
        alarm = self.drowsy or self.distracted
        elapsed = (now - self.alarm_since) if alarm and self.alarm_since and now else 0
        return {
            "presence": "Detected" if present else "Not detected",
            "face_detected": present,
            "drowsiness": "Drowsy" if self.drowsy else "Inactive",
            "distraction": "Distracted" if self.distracted else "Inactive",
            "eyes_on_road": ("Yes" if eyes else "No") if eyes is not None else "Calibrating",
            "ear": ear,
            "mar": mar,
            "head_pose": {"yaw_degrees": yaw, "pitch_degrees": pitch},
            "alarm_active": alarm,
            "alarm_escalation": "pull_over" if elapsed >= self.PULL_OVER_AFTER_WARNING_SECONDS else ("warning" if alarm else "none"),
            "camera_connected": present,
        }
