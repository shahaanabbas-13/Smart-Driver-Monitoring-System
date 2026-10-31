"""MediaPipe Hand Landmarker wrapper and one-shot gesture action gating."""

from math import hypot
from pathlib import Path

import mediapipe as mp


PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODEL_PATH = PROJECT_ROOT / "data" / "hand_landmarker.task"


class HandGestureDetector:
    def __init__(self):
        if not MODEL_PATH.exists():
            raise FileNotFoundError(f"Hand Landmarker model not found: {MODEL_PATH}")
        options = mp.tasks.vision.HandLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(MODEL_PATH)),
            running_mode=mp.tasks.vision.RunningMode.IMAGE,
            num_hands=1,
            min_hand_detection_confidence=0.55,
            min_hand_presence_confidence=0.55,
            min_tracking_confidence=0.5,
        )
        self.detector = mp.tasks.vision.HandLandmarker.create_from_options(options)

    @staticmethod
    def _distance(a, b):
        return hypot(a["x"] - b["x"], a["y"] - b["y"])

    @classmethod
    def classify(cls, landmarks):
        """Classify common upright-hand poses from the 21 normalized landmarks."""
        wrist = landmarks[0]
        fingers = ((8, 6), (12, 10), (16, 14), (20, 18))
        extended = [
            cls._distance(landmarks[tip], wrist)
            > cls._distance(landmarks[pip], wrist) * 1.12
            for tip, pip in fingers
        ]
        thumb_extended = (
            cls._distance(landmarks[4], wrist)
            > cls._distance(landmarks[3], wrist) * 1.12
            and cls._distance(landmarks[4], landmarks[5]) > 0.12
        )

        if all(extended) and thumb_extended:
            return "Open Palm"
        if (
            thumb_extended
            and not any(extended)
            and landmarks[4]["y"] < landmarks[2]["y"] - 0.04
        ):
            return "Thumbs Up"
        if sum(extended) <= 1 and not thumb_extended:
            return "Fist"
        return "Other"

    def detect(self, rgb_image):
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_image)
        result = self.detector.detect(image)
        if not result.hand_landmarks:
            return None
        landmarks = [
            {"x": point.x, "y": point.y, "z": point.z}
            for point in result.hand_landmarks[0]
        ]
        return self.classify(landmarks)

    def close(self):
        self.detector.close()


class GestureActionGate:
    """Debounce poses and emit one action per gesture, only while eyes are on road."""

    ACTIONS = {
        "Open Palm": "next track",
        "Thumbs Up": "dismiss alarm",
        "Fist": "phone call",
    }

    def __init__(self, stable_frames=3):
        self.stable_frames = stable_frames
        self.candidate = None
        self.candidate_frames = 0
        self.emitted_for_pose = None

    def reset(self):
        self.candidate = None
        self.candidate_frames = 0
        self.emitted_for_pose = None

    def process(self, gesture, eyes_on_road, enabled=True):
        if not enabled or gesture in (None, "Other"):
            self.reset()
            return {"gesture": gesture, "action": None, "authorized": False, "triggered": False}
        if gesture == self.candidate:
            self.candidate_frames += 1
        else:
            self.candidate = gesture
            self.candidate_frames = 1

        if self.candidate_frames < self.stable_frames or self.emitted_for_pose == gesture:
            return {"gesture": gesture, "action": None, "authorized": eyes_on_road, "triggered": False}

        self.emitted_for_pose = gesture
        action = self.ACTIONS.get(gesture)
        authorized = eyes_on_road and action is not None
        return {
            "gesture": gesture,
            "action": action if authorized else None,
            "authorized": authorized,
            "triggered": True,
        }
