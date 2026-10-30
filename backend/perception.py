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
                "head_pose": {"yaw_degrees": None, "pitch_degrees": None},
            }

        points = [(point.x, point.y) for point in result.face_landmarks[0]]
        left_ear = self._ear(points, self.LEFT_EYE)
        right_ear = self._ear(points, self.RIGHT_EYE)
        mouth = [points[i] for i in self.MOUTH]
        mar = self._distance(mouth[1], mouth[3]) / self._distance(mouth[0], mouth[2])

        return {
            "face_detected": True,
            "landmarks": [
                {"x": p.x, "y": p.y, "z": p.z}
                for p in result.face_landmarks[0]
            ],
            "ear": self._rounded((left_ear + right_ear) / 2),
            "mar": self._rounded(mar),
            "head_pose": self._head_pose(points, width, height),
        }

    def close(self):
        self.detector.close()
