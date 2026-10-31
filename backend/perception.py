from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODEL_PATH = PROJECT_ROOT / "data" / "face_landmarker.task"


class FacePerception:
    """Face landmark detection and the raw measurements used by safety logic."""

    # MediaPipe Face Mesh landmark groups (indices from the 478-point topology).
    LEFT_EYE = (33, 160, 158, 133, 153, 144)
    RIGHT_EYE = (362, 385, 387, 263, 373, 380)
    MOUTH = (78, 13, 308, 14)
    POSE_IDS = (1, 152, 33, 263, 61, 291)
    MODEL_POINTS = np.array(
        [
            (0.0, 0.0, 0.0),
            (0.0, -330.0, -65.0),
            (-225.0, 170.0, -135.0),
            (225.0, 170.0, -135.0),
            (-150.0, -150.0, -125.0),
            (150.0, -150.0, -125.0),
        ],
        dtype=np.float64,
    )

    def __init__(self):
        if not MODEL_PATH.exists():
            raise FileNotFoundError(f"Face Landmarker model not found: {MODEL_PATH}")
        options = mp.tasks.vision.FaceLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(MODEL_PATH)),
            running_mode=mp.tasks.vision.RunningMode.IMAGE,
            num_faces=1,
            output_face_blendshapes=False,
            output_facial_transformation_matrixes=False,
        )
        self.detector = mp.tasks.vision.FaceLandmarker.create_from_options(options)

    @staticmethod
    def _distance(a, b):
        return float(np.linalg.norm(np.asarray(a) - np.asarray(b)))

    @classmethod
    def _ear(cls, points, indices):
        p = [points[i] for i in indices]
        return (cls._distance(p[1], p[5]) + cls._distance(p[2], p[4])) / (
            2 * cls._distance(p[0], p[3])
        )

    @staticmethod
    def _rounded(value, digits=3):
        return round(float(value), digits) if np.isfinite(value) else None

    @classmethod
    def _mouth_aspect_ratio(cls, points):
        # Keep the central inner-lip opening as the primary score. Averaging
        # side openings can dilute a real yawn, especially with a small face.
        mouth_width = cls._distance(points[78], points[308])
        if mouth_width <= 1e-6:
            return 0.0
        center_opening = cls._distance(points[13], points[14]) / mouth_width
        side_opening = (
            cls._distance(points[82], points[87])
            + cls._distance(points[312], points[317])
        ) / (2 * mouth_width)
        return 0.75 * center_opening + 0.25 * side_opening

    @classmethod
    def _emotion_hint(cls, points, ear, mar):
        """Return a conservative geometry hint, not a psychological diagnosis."""
        eye_span = cls._distance(points[33], points[263])
        if eye_span <= 1e-6:
            return "calm"
        brow_gap = (
            cls._distance(points[105], points[159])
            + cls._distance(points[334], points[386])
        ) / (2 * eye_span)
        if ear < 0.17 or mar > 0.34:
            return "tired"
        if brow_gap < 0.18:
            return "stressed"
        return "calm"

    @classmethod
    def _gaze_and_frontality(cls, points):
        if len(points) < 478:
            return None, None

        def ratio_between(value, start, end):
            span = end - start
            return (value - start) / span if abs(span) > 1e-6 else None

        horizontal_positions = (
            ratio_between(points[468][0], points[33][0], points[133][0]),
            ratio_between(points[473][0], points[263][0], points[362][0]),
        )
        left_top = (points[159][1] + points[158][1]) / 2
        left_bottom = (points[145][1] + points[153][1]) / 2
        right_top = (points[386][1] + points[385][1]) / 2
        right_bottom = (points[374][1] + points[380][1]) / 2
        vertical_positions = (
            ratio_between(points[468][1], left_top, left_bottom),
            ratio_between(points[473][1], right_top, right_bottom),
        )
        horizontal_positions = [v for v in horizontal_positions if v is not None]
        vertical_positions = [v for v in vertical_positions if v is not None]
        gaze = None
        if horizontal_positions and vertical_positions:
            gaze = {
                "horizontal": cls._rounded(sum(horizontal_positions) / len(horizontal_positions)),
                "vertical": cls._rounded(sum(vertical_positions) / len(vertical_positions)),
            }

        left_eye_width = cls._distance(points[33], points[133])
        right_eye_width = cls._distance(points[263], points[362])
        face_width = abs(points[234][0] - points[454][0])
        if min(left_eye_width, right_eye_width, face_width) <= 1e-6:
            frontal_score = None
        else:
            symmetry = min(left_eye_width, right_eye_width) / max(left_eye_width, right_eye_width)
            left_eye_center = (points[33][0] + points[133][0]) / 2
            right_eye_center = (points[263][0] + points[362][0]) / 2
            eye_midpoint = (left_eye_center + right_eye_center) / 2
            nose_offset = abs(points[1][0] - eye_midpoint) / face_width
            nose_centering = max(0.0, 1.0 - nose_offset / 0.22)
            frontal_score = cls._rounded(0.65 * symmetry + 0.35 * nose_centering)
        return gaze, frontal_score

    def _head_pose(self, points, width, height):
        image_points = np.array(
            [(points[i][0] * width, points[i][1] * height) for i in self.POSE_IDS],
            dtype=np.float64,
        )
        focal = float(width)
        camera_matrix = np.array(
            [[focal, 0, width / 2], [0, focal, height / 2], [0, 0, 1]],
            dtype=np.float64,
        )
        ok, rotation, _ = cv2.solvePnP(
            self.MODEL_POINTS,
            image_points,
            camera_matrix,
            np.zeros((4, 1), dtype=np.float64),
            flags=cv2.SOLVEPNP_ITERATIVE,
        )
        if not ok:
            return {"yaw_degrees": None, "pitch_degrees": None}
        matrix, _ = cv2.Rodrigues(rotation)
        angles = cv2.RQDecomp3x3(matrix)[0]
        return {
            "yaw_degrees": self._rounded(angles[1], 1),
            "pitch_degrees": self._rounded(angles[0], 1),
        }

    def analyze_rgb(self, rgb_image):
        height, width = rgb_image.shape[:2]
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_image)
        result = self.detector.detect(image)
        if not result.face_landmarks:
            return {
                "face_detected": False,
                "landmarks": [],
                "ear": None,
                "mar": None,
                "gaze": None,
                "frontal_score": None,
                "emotion": "calm",
                "head_pose": {"yaw_degrees": None, "pitch_degrees": None},
            }

        points = [(point.x, point.y) for point in result.face_landmarks[0]]
        left_ear = self._ear(points, self.LEFT_EYE)
        right_ear = self._ear(points, self.RIGHT_EYE)
        mar = self._mouth_aspect_ratio(points)
        gaze, frontal_score = self._gaze_and_frontality(points)
        ear = (left_ear + right_ear) / 2

        return {
            "face_detected": True,
            "landmarks": [
                {"x": p.x, "y": p.y, "z": p.z}
                for p in result.face_landmarks[0]
            ],
            "ear": self._rounded(ear),
            "mar": self._rounded(mar),
            "emotion": self._emotion_hint(points, ear, mar),
            "gaze": gaze,
            "frontal_score": frontal_score,
            "head_pose": self._head_pose(points, width, height),
        }

    def close(self):
        self.detector.close()
