import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.profiles import DriverProfiles, MAX_PROFILES


class DriverProfilesTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "profiles.sqlite3"
        self.path_patcher = patch("backend.profiles.DATABASE_PATH", self.database_path)
        self.path_patcher.start()
        self.profiles = DriverProfiles()

    def tearDown(self):
        self.path_patcher.stop()
        self.temp_dir.cleanup()

    def test_profile_persists_personalization_and_can_be_removed(self):
        profile = self.profiles.create_profile(
            "Asha", 29, "DL-123", theme="sage", temperature=24, playlist="Ambient Calm"
        )
        details = self.profiles.get_profile(profile["id"])
        self.assertEqual(details["theme"], "sage")
        self.assertEqual(details["temperature"], 24)
        self.assertEqual(details["playlist"], "Ambient Calm")
        self.assertEqual(details["driving_license"], "DL-123")
        self.assertTrue(self.profiles.remove_profile(profile["id"]))
        self.assertIsNone(self.profiles.get_profile(profile["id"]))

    def test_profile_cap_is_eight_and_personalization_is_validated(self):
        for index in range(MAX_PROFILES):
            self.profiles.create_profile(f"Driver {index}", 20, "")
        with self.assertRaisesRegex(ValueError, "up to 8"):
            self.profiles.create_profile("Ninth", 20, "")
        with self.assertRaisesRegex(ValueError, "supported light dashboard theme"):
            self.profiles.create_profile("Bad theme", 20, "", theme="#fff")

    def test_old_profile_schema_is_migrated(self):
        import sqlite3

        legacy_path = Path(self.temp_dir.name) / "legacy.sqlite3"
        db = sqlite3.connect(legacy_path)
        try:
            db.execute(
                "CREATE TABLE drivers (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, "
                "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            db.execute("INSERT INTO drivers(name) VALUES ('Legacy')")
            db.execute(
                "CREATE TABLE face_samples (id INTEGER PRIMARY KEY, driver_id INTEGER NOT NULL, "
                "image BLOB NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            db.commit()
        finally:
            db.close()
        with patch("backend.profiles.DATABASE_PATH", legacy_path):
            migrated = DriverProfiles().get_profile(1)
        self.assertEqual(migrated["theme"], "ocean")
        self.assertEqual(migrated["temperature"], 22)
        self.assertEqual(migrated["playlist"], "Focus Flow")


if __name__ == "__main__":
    unittest.main()
