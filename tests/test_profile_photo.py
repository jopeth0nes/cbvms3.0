"""Portal photo changes must preserve enrollment data."""
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image
from database.db_manager import CBVMSDatabase
from ui.student_portal import StudentPortal


class ProfilePhotoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = CBVMSDatabase(self.root / "test.db")
        self.db.initialize()
        self.db.insert_student("S1", "One", "BSIT", "1A", b"encoding", b"registered")
        self.db.insert_student("S2", "Two", "BSIT", "1A", b"other", b"other-photo")
        self.portal = SimpleNamespace(db=self.db, student_id="S1", _toast=Mock(),
                                      _show=Mock(), _log_activity=Mock())
        # Exercise the operation and its delivery separately from the native worker tests.
        def run_action(operation, callback):
            try:
                result = operation(self.db, "S1")
            except Exception as exc:
                self.portal._toast(str(exc), "error")
            else:
                callback(result)
        self.portal._run_action = run_action

    def choose(self, path):
        with patch("ui.student_portal.filedialog.askopenfilename", return_value=str(path)):
            StudentPortal._change_profile_photo(self.portal)

    def test_upload_persists_without_changing_registration_or_other_student(self):
        path = self.root / "photo.png"
        Image.new("RGB", (1200, 600), "blue").save(path)
        self.choose(path)
        self.db.initialize()
        student = CBVMSDatabase(self.db.db_path).get_student_by_student_id("S1")
        self.assertEqual(student["photo"], b"registered")
        self.assertEqual(student["encoding"], b"encoding")
        with Image.open(io.BytesIO(student["profile_photo"])) as image:
            self.assertEqual(image.size, (800, 400))
        self.assertIsNone(self.db.get_student_by_student_id("S2")["profile_photo"])
        self.portal._show.assert_called_once_with("profile")

    def test_cancel_and_invalid_image_keep_existing_photo(self):
        self.db.update_student_profile_photo("S1", b"existing")
        self.choose("")
        path = self.root / "bad.jpg"
        path.write_text("not an image")
        self.choose(path)
        self.assertEqual(self.db.get_student_by_student_id("S1")["profile_photo"], b"existing")
        self.portal._show.assert_not_called()
        self.assertEqual(self.portal._toast.call_args.args[1], "error")

    def test_migration_preserves_existing_enrollment(self):
        with self.db.connect() as conn:
            conn.execute("ALTER TABLE students DROP COLUMN profile_photo")
        self.db.initialize()
        self.db.initialize()
        student = self.db.get_student_by_student_id("S1")
        self.assertIsNone(student["profile_photo"])
        self.assertEqual(student["photo"], b"registered")
        self.assertEqual(student["encoding"], b"encoding")
        self.assertFalse(self.db.update_student_profile_photo("missing", b"photo"))


if __name__ == "__main__":
    unittest.main()
