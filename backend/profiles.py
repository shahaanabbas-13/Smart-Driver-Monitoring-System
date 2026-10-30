"""Local SQLite storage and OpenCV LBPH recognition for two driver profiles."""

import sqlite3
from pathlib import Path

import cv2
import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_PATH = PROJECT_ROOT / "data" / "driver_profiles.sqlite3"
SAMPLES_REQUIRED = 40
MAX_PROFILES = 2
LBPH_CONFIDENCE_LIMIT = 75.0


class DriverProfiles:
    def __init__(self):
        DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_database()
        self.recognizer = None
        self._train_model()

    def _connect(self):
        connection = sqlite3.connect(DATABASE_PATH)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize_database(self):
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS drivers ("
                "id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS face_samples ("
                "id INTEGER PRIMARY KEY, driver_id INTEGER NOT NULL REFERENCES drivers(id), "
                "image BLOB NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )

    def list_profiles(self):
        with self._connect() as db:
            rows = db.execute(
                "SELECT d.id, d.name, COUNT(s.id) AS sample_count "
                "FROM drivers d LEFT JOIN face_samples s ON s.driver_id = d.id "
                "GROUP BY d.id ORDER BY d.id"
            ).fetchall()
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "sample_count": row["sample_count"],
                "trained": row["sample_count"] >= SAMPLES_REQUIRED,
            }
            for row in rows
        ]

    def create_profile(self, name):
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Enter a driver name")
        with self._connect() as db:
            existing = db.execute("SELECT id FROM drivers WHERE name = ?", (clean_name,)).fetchone()
            if existing is not None:
                sample_count = db.execute(
                    "SELECT COUNT(*) FROM face_samples WHERE driver_id = ?", (existing["id"],)
                ).fetchone()[0]
                if sample_count < SAMPLES_REQUIRED:
                    return {
                        "id": existing["id"],
                        "name": clean_name,
                        "sample_count": sample_count,
                        "trained": False,
                    }
                raise ValueError("That driver profile is already trained")
            count = db.execute("SELECT COUNT(*) FROM drivers").fetchone()[0]
            if count >= MAX_PROFILES:
                raise ValueError("This build supports up to two driver profiles")
            cursor = db.execute("INSERT INTO drivers(name) VALUES (?)", (clean_name,))
            return {"id": cursor.lastrowid, "name": clean_name, "sample_count": 0, "trained": False}

    @staticmethod
    def prepare_face(image):
        """Normalize an already-cropped face for LBPH enrollment and matching."""
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, (128, 128), interpolation=cv2.INTER_AREA)
        return cv2.equalizeHist(gray)

    def add_sample(self, profile_id, face_image):
        normalized = self.prepare_face(face_image)
        ok, encoded = cv2.imencode(".png", normalized)
        if not ok:
            raise ValueError("Could not encode face sample")
        added = False
        with self._connect() as db:
            profile = db.execute("SELECT id FROM drivers WHERE id = ?", (profile_id,)).fetchone()
            if profile is None:
                raise ValueError("Driver profile not found")
            count = db.execute(
                "SELECT COUNT(*) FROM face_samples WHERE driver_id = ?", (profile_id,)
            ).fetchone()[0]
            if count < SAMPLES_REQUIRED:
                db.execute(
                    "INSERT INTO face_samples(driver_id, image) VALUES (?, ?)",
                    (profile_id, encoded.tobytes()),
                )
                count += 1
                added = True
        if added and count == SAMPLES_REQUIRED:
            self._train_model()
        return {"sample_count": count, "target": SAMPLES_REQUIRED, "complete": count >= SAMPLES_REQUIRED}

    def _train_model(self):
        images, labels = [], []
        with self._connect() as db:
            profiles = db.execute(
                "SELECT id FROM drivers WHERE "
                "(SELECT COUNT(*) FROM face_samples WHERE driver_id = drivers.id) >= ?",
                (SAMPLES_REQUIRED,),
            ).fetchall()
            for profile in profiles:
                samples = db.execute(
                    "SELECT image FROM face_samples WHERE driver_id = ?", (profile["id"],)
                ).fetchall()
                for row in samples:
                    decoded = cv2.imdecode(
                        np.frombuffer(row["image"], dtype=np.uint8), cv2.IMREAD_GRAYSCALE
                    )
                    if decoded is not None:
                        images.append(decoded)
                        labels.append(profile["id"])
        if not images:
            self.recognizer = None
            return
        model = cv2.face.LBPHFaceRecognizer_create(
            radius=1, neighbors=8, grid_x=8, grid_y=8, threshold=LBPH_CONFIDENCE_LIMIT
        )
        model.train(images, np.asarray(labels, dtype=np.int32))
        self.recognizer = model

    def recognize(self, face_image):
        if self.recognizer is None:
            return None
        label, confidence = self.recognizer.predict(self.prepare_face(face_image))
        if confidence > LBPH_CONFIDENCE_LIMIT:
            return None
        with self._connect() as db:
            row = db.execute("SELECT id, name FROM drivers WHERE id = ?", (int(label),)).fetchone()
        if row is None:
            return None
        return {"id": row["id"], "name": row["name"], "confidence": round(float(confidence), 1)}
