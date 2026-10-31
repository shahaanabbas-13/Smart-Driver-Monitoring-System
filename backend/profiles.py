"""Local SQLite storage and OpenCV LBPH recognition for family driver profiles."""

import base64
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import cv2
import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_PATH = PROJECT_ROOT / "data" / "driver_profiles.sqlite3"
SAMPLES_REQUIRED = 40
MAX_PROFILES = 8
LBPH_CONFIDENCE_LIMIT = 75.0
PROFILE_THEMES = {"ocean", "sage", "amber"}
PROFILE_PLAYLISTS = {"Focus Flow", "Ambient Calm", "Upbeat Energy", "Late Night Drive"}


class DriverProfiles:
    def __init__(self):
        DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_database()
        self.recognizer = None
        self._train_model()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(DATABASE_PATH)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize_database(self):
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS drivers ("
                "id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, age INTEGER, driving_license TEXT NOT NULL DEFAULT '', "
                "theme TEXT NOT NULL DEFAULT 'ocean', temperature INTEGER NOT NULL DEFAULT 22, "
                "playlist TEXT NOT NULL DEFAULT 'Focus Flow', "
                "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS face_samples ("
                "id INTEGER PRIMARY KEY, driver_id INTEGER NOT NULL REFERENCES drivers(id), "
                "image BLOB NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            columns = {row["name"] for row in db.execute("PRAGMA table_info(drivers)")}
            if "age" not in columns:
                db.execute("ALTER TABLE drivers ADD COLUMN age INTEGER")
            if "driving_license" not in columns:
                db.execute("ALTER TABLE drivers ADD COLUMN driving_license TEXT NOT NULL DEFAULT ''")
            if "theme" not in columns:
                db.execute("ALTER TABLE drivers ADD COLUMN theme TEXT NOT NULL DEFAULT 'ocean'")
            if "temperature" not in columns:
                db.execute("ALTER TABLE drivers ADD COLUMN temperature INTEGER NOT NULL DEFAULT 22")
            if "playlist" not in columns:
                db.execute("ALTER TABLE drivers ADD COLUMN playlist TEXT NOT NULL DEFAULT 'Focus Flow'")

    def list_profiles(self):
        with self._connect() as db:
            rows = db.execute(
                "SELECT d.id, d.name, d.theme, d.temperature, d.playlist, COUNT(s.id) AS sample_count "
                "FROM drivers d LEFT JOIN face_samples s ON s.driver_id = d.id "
                "GROUP BY d.id ORDER BY d.id"
            ).fetchall()
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "sample_count": row["sample_count"],
                "trained": row["sample_count"] >= SAMPLES_REQUIRED,
                "theme": row["theme"],
                "temperature": row["temperature"],
                "playlist": row["playlist"],
            }
            for row in rows
        ]

    def create_profile(self, name, age, driving_license, theme="ocean", temperature=22, playlist="Focus Flow"):
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Enter a driver name")
        if age < 0 or age > 120:
            raise ValueError("Enter an age between 0 and 120")
        clean_license = driving_license.strip()
        if theme not in PROFILE_THEMES:
            raise ValueError("Choose a supported light dashboard theme")
        if temperature < 16 or temperature > 30:
            raise ValueError("Choose a temperature between 16 and 30 °C")
        if playlist not in PROFILE_PLAYLISTS:
            raise ValueError("Choose a supported demo playlist")
        with self._connect() as db:
            existing = db.execute("SELECT id FROM drivers WHERE name = ?", (clean_name,)).fetchone()
            if existing is not None:
                sample_count = db.execute(
                    "SELECT COUNT(*) FROM face_samples WHERE driver_id = ?", (existing["id"],)
                ).fetchone()[0]
                if sample_count < SAMPLES_REQUIRED:
                    db.execute(
                        "UPDATE drivers SET age = ?, driving_license = ?, theme = ?, temperature = ?, playlist = ? WHERE id = ?",
                        (age, clean_license, theme, temperature, playlist, existing["id"]),
                    )
                    return {
                        "id": existing["id"],
                        "name": clean_name,
                        "sample_count": sample_count,
                        "trained": False,
                        "theme": theme,
                        "temperature": temperature,
                        "playlist": playlist,
                    }
                raise ValueError("That driver profile is already trained")
            count = db.execute("SELECT COUNT(*) FROM drivers").fetchone()[0]
            if count >= MAX_PROFILES:
                raise ValueError(f"This build supports up to {MAX_PROFILES} family profiles")
            cursor = db.execute(
                "INSERT INTO drivers(name, age, driving_license, theme, temperature, playlist) VALUES (?, ?, ?, ?, ?, ?)",
                (clean_name, age, clean_license, theme, temperature, playlist),
            )
            return {
                "id": cursor.lastrowid, "name": clean_name, "sample_count": 0, "trained": False,
                "theme": theme, "temperature": temperature, "playlist": playlist,
            }

    def get_profile(self, profile_id):
        with self._connect() as db:
            row = db.execute(
                "SELECT d.id, d.name, d.age, d.driving_license, d.theme, d.temperature, d.playlist, COUNT(s.id) AS sample_count "
                "FROM drivers d LEFT JOIN face_samples s ON s.driver_id = d.id "
                "WHERE d.id = ? GROUP BY d.id",
                (profile_id,),
            ).fetchone()
            sample = db.execute(
                "SELECT image FROM face_samples WHERE driver_id = ? ORDER BY id LIMIT 1",
                (profile_id,),
            ).fetchone()
        if row is None:
            return None
        photo = None
        if sample:
            photo_bytes = bytes(sample["image"])
            photo = "data:image/png;base64," + base64.b64encode(photo_bytes).decode("ascii")
        return {
            "id": row["id"],
            "name": row["name"],
            "age": row["age"],
            "driving_license": row["driving_license"],
            "theme": row["theme"],
            "temperature": row["temperature"],
            "playlist": row["playlist"],
            "sample_count": row["sample_count"],
            "trained": row["sample_count"] >= SAMPLES_REQUIRED,
            "photo": photo,
        }

    def remove_profile(self, profile_id):
        with self._connect() as db:
            row = db.execute("SELECT id FROM drivers WHERE id = ?", (profile_id,)).fetchone()
            if row is None:
                return False
            db.execute("DELETE FROM face_samples WHERE driver_id = ?", (profile_id,))
            db.execute("DELETE FROM drivers WHERE id = ?", (profile_id,))
        self._train_model()
        return True

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
            row = db.execute(
                "SELECT id, name, theme, temperature, playlist FROM drivers WHERE id = ?",
                (int(label),),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"], "name": row["name"], "confidence": round(float(confidence), 1),
            "theme": row["theme"], "temperature": row["temperature"], "playlist": row["playlist"],
        }
