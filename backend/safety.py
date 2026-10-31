from collections import deque
from statistics import median
from time import monotonic


class SafetyEngine:
    """Temporal filtering and safety decisions for successive perception frames."""

    DROWSINESS_ALARM_CONFIRM_SECONDS = 5.0
    PULL_OVER_AFTER_WARNING_SECONDS = 10.0
    ATTENTION_CALIBRATION_SAMPLES = 12
    LOOKAWAY_CONFIRM_SECONDS = 1.5
    ATTENTION_RECOVERY_SECONDS = 0.8
    ALARM_CONFIRM_FRAMES = 4
    YAWN_MAR_THRESHOLD = 0.52
    YAWN_RELEASE_THRESHOLD = 0.42
    YAWN_CONFIRM_SECONDS = 0.45
    YAWN_CLOSE_CONFIRM_SECONDS = 0.3

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
        self.yawning = False
        self.yawn_end_since = None
        self.drowsy = False
        self.distracted = False
        self.baseline = None
        self.baseline_samples = 0
        self.baseline_window = []
        self.attention_recovery_since = None
        self.last_frame_at = None
        self.alarm_candidate_frames = 0
        self.missing_face_frames = 0

    def recalibrate_attention(self):
        """Relearn the driver's forward head pose from the next stable frames."""
        self.baseline = None
        self.baseline_samples = 0
        self.baseline_window = []
        self.lookaway_since = None
        self.attention_recovery_since = None
        self.distracted = False
        self.alarm_candidate_frames = 0
        self.alarm_since = None
        self.missing_face_frames = 0

    def update(self, result):
        now = monotonic()
        # A long gap means the camera was paused or the service stalled; do not
        # accumulate that interval as if the driver's eyes/head stayed fixed.
        if self.last_frame_at is not None and now - self.last_frame_at > 1.5:
            self.closed_since = self.yawn_since = self.lookaway_since = None
            self.drowsy_candidate_since = None
            # Reset timers/counts that depend on uninterrupted frames without
            # discarding a still-active alarm or missing-face streak.
            if not self.drowsy and not self.distracted:
                self.alarm_candidate_frames = 0
                self.alarm_since = None
        self.last_frame_at = now

        if not result["face_detected"]:
            self.closed_since = self.yawn_since = self.lookaway_since = None
            self.drowsy_candidate_since = None
            self.missing_face_frames += 1
            self.drowsy = False
            self.distracted = self.missing_face_frames >= self.ALARM_CONFIRM_FRAMES
            self.alarm_candidate_frames = min(self.missing_face_frames, self.ALARM_CONFIRM_FRAMES)
            alarm = self.alarm_candidate_frames >= self.ALARM_CONFIRM_FRAMES
            self.alarm_since = (self.alarm_since or now) if alarm else None
            self.yawning = False
            return self._state(False, False, None, None, now=now, alarm=alarm)

        if self.missing_face_frames:
            self.missing_face_frames = 0
            self.distracted = False
            self.alarm_candidate_frames = 0
            self.alarm_since = None

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

        if mar is not None and mar >= self.YAWN_MAR_THRESHOLD:
            self.yawn_end_since = None
            if not self.yawn_active:
                self.yawn_since = self.yawn_since or now
                if now - self.yawn_since >= self.YAWN_CONFIRM_SECONDS:
                    self.yawns.append(now)
                    self.yawn_active = True
                    self.yawning = True
        elif mar is None or mar <= self.YAWN_RELEASE_THRESHOLD:
            if self.yawn_active:
                self.yawn_end_since = self.yawn_end_since or now
                if now - self.yawn_end_since >= self.YAWN_CLOSE_CONFIRM_SECONDS:
                    self.yawn_since = None
                    self.yawn_end_since = None
                    self.yawn_active = False
                    self.yawning = False
            else:
                self.yawn_since = None
                self.yawning = False
        else:
            # Keep a detected yawn active across small score fluctuations.
            self.yawn_end_since = None
            if not self.yawn_active:
                self.yawn_since = None
        while self.yawns and now - self.yawns[0] > 60:
            self.yawns.popleft()
        repeated_yawns = len(self.yawns) >= 3
        drowsiness_candidate = eye_closure_candidate or repeated_yawns
        if drowsiness_candidate:
            self.drowsy_candidate_since = self.drowsy_candidate_since or now
            # Three distinct yawn events can update the visible state; the
            # alarm still waits for a sustained 5-second drowsy condition.
            if repeated_yawns or now - self.drowsy_candidate_since >= self.DROWSINESS_ALARM_CONFIRM_SECONDS:
                self.drowsy = True
        else:
            self.drowsy_candidate_since = None
            if ear is not None and ear > 0.24 and (mar is None or mar < 0.55):
                self.drowsy = False

        eyes_on_road = None
        gaze = result.get("gaze")
        frontal_score = result.get("frontal_score")
        if yaw is not None and pitch is not None:
            if self.baseline is None:
                gaze_h = gaze.get("horizontal") if gaze else None
                gaze_v = gaze.get("vertical") if gaze else None
                self.baseline_window.append((yaw, pitch, gaze_h, gaze_v, frontal_score))
                self.baseline_samples = len(self.baseline_window)
                if self.baseline_samples >= self.ATTENTION_CALIBRATION_SAMPLES:
                    self.baseline = [
                        median(sample[0] for sample in self.baseline_window),
                        median(sample[1] for sample in self.baseline_window),
                        median(sample[2] for sample in self.baseline_window if sample[2] is not None) if any(sample[2] is not None for sample in self.baseline_window) else None,
                        median(sample[3] for sample in self.baseline_window if sample[3] is not None) if any(sample[3] is not None for sample in self.baseline_window) else None,
                        median(sample[4] for sample in self.baseline_window if sample[4] is not None) if any(sample[4] is not None for sample in self.baseline_window) else None,
                    ]
                    self.baseline_window = []
            else:
                yaw_delta = abs(yaw - self.baseline[0])
                pitch_delta = abs(pitch - self.baseline[1])
                gaze_h = gaze.get("horizontal") if gaze else None
                gaze_v = gaze.get("vertical") if gaze else None
                gaze_away = (
                    (gaze_h is not None and self.baseline[2] is not None and abs(gaze_h - self.baseline[2]) > 0.17)
                    or (gaze_v is not None and self.baseline[3] is not None and abs(gaze_v - self.baseline[3]) > 0.22)
                )
                face_turned = frontal_score is not None and self.baseline[4] is not None and (
                    frontal_score < 0.42 or self.baseline[4] - frontal_score > 0.20
                )
                looking_away = yaw_delta > 22 or pitch_delta > 18 or gaze_away or face_turned
                centered = yaw_delta < 16 and pitch_delta < 13 and not gaze_away and not face_turned
                if looking_away:
                    self.lookaway_since = self.lookaway_since or now
                    self.attention_recovery_since = None
                    if now - self.lookaway_since >= self.LOOKAWAY_CONFIRM_SECONDS:
                        self.distracted = True
                else:
                    self.lookaway_since = None
                    if centered:
                        self.attention_recovery_since = self.attention_recovery_since or now
                        if now - self.attention_recovery_since >= self.ATTENTION_RECOVERY_SECONDS:
                            self.distracted = False
                        if not self.distracted:
                            # Slowly adapt only while the driver is centered.
                            self.baseline[0] = self.baseline[0] * 0.995 + yaw * 0.005
                            self.baseline[1] = self.baseline[1] * 0.995 + pitch * 0.005
                    else:
                        self.attention_recovery_since = None
                # Match the dashboard attention label to the sustained,
                # hysteresis-filtered distraction state rather than one noisy frame.
                eyes_on_road = not self.distracted

        sustained_drowsiness = (
            self.drowsy
            and self.drowsy_candidate_since is not None
            and now - self.drowsy_candidate_since >= self.DROWSINESS_ALARM_CONFIRM_SECONDS
        )
        alert_candidate = sustained_drowsiness or self.distracted or eyes_on_road is False
        self.alarm_candidate_frames = self.alarm_candidate_frames + 1 if alert_candidate else 0
        alarm = self.alarm_candidate_frames >= self.ALARM_CONFIRM_FRAMES
        self.alarm_since = (self.alarm_since or now) if alarm else None
        return self._state(
            True, eyes_on_road, ear, mar, yaw, pitch, now, alarm,
            result.get("emotion", "calm"),
        )

    def _state(self, present, eyes, ear, mar, yaw=None, pitch=None, now=None, alarm=False, emotion="calm"):
        elapsed = (now - self.alarm_since) if alarm and self.alarm_since and now else 0
        return {
            "presence": "Detected" if present else "Not detected",
            "face_detected": present,
            "drowsiness": "Drowsy" if self.drowsy else "Inactive",
            "distraction": "Distracted" if self.distracted else "Inactive",
            "eyes_on_road": ("Yes" if eyes else "No") if eyes is not None else "Calibrating",
            "ear": ear,
            "mar": mar,
            "emotion": emotion,
            "yawning": self.yawning,
            "yawn_count_60s": len(self.yawns),
            "alarm_confirmation_frames": self.alarm_candidate_frames,
            "head_pose": {"yaw_degrees": yaw, "pitch_degrees": pitch},
            "attention_calibration_remaining": max(
                0, self.ATTENTION_CALIBRATION_SAMPLES - self.baseline_samples
            ),
            "alarm_active": alarm,
            "alarm_escalation": "pull_over" if elapsed >= self.PULL_OVER_AFTER_WARNING_SECONDS else ("warning" if alarm else "none"),
            "camera_connected": present,
        }
