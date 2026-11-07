"""Model location selection never loads inference or touches student data."""
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.recognizer import FaceRecognizer


class FaceModelPathTests(unittest.TestCase):
    def test_explicit_directory_is_preserved(self):
        db = MagicMock()
        db.get_all_students.return_value = []
        with patch.dict(os.environ, CBVMS_FACE_MODEL_DIR='models/operator-choice'):
            recognizer = FaceRecognizer(db)
        self.assertEqual(recognizer.model_dir, Path('models/operator-choice').resolve())

    def test_missing_default_uses_complete_project_pack(self):
        db = MagicMock()
        db.get_all_students.return_value = []
        project_pack = Path(__file__).resolve().parents[1] / 'models' / 'buffalo_l'
        def exists(path):
            return path.parent == project_pack
        with patch.dict(os.environ, {'USERPROFILE': str(project_pack.parent)}, clear=True), patch.object(Path, 'is_file', exists), patch.object(Path, 'stat', return_value=MagicMock(st_size=4096)):
            recognizer = FaceRecognizer(db)
        self.assertEqual(recognizer.model_dir, project_pack)
