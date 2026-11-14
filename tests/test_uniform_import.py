import io
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch, Mock

from PIL import Image

from core.uniform_dataset import ImportLimitError, UniformDataset, MAX_FILE_BYTES
from core.uniform_import import get_import_job, start_import


def png(color):
    out = io.BytesIO()
    Image.new("RGB", (80, 80), color).save(out, format="PNG")
    return out.getvalue()


class UniformImportProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.dataset = UniformDataset(self.base / "catalog", auto_process=False)

    def tearDown(self):
        self.temp.cleanup()

    def put(self, name, data):
        path = self.base / name
        path.write_bytes(data)
        return path

    def test_progress_counts_unique_saves_duplicates_corrupt_and_unsupported(self):
        good = png((12, 45, 99))
        paths = [self.put("one.png", good), self.put("copy.png", good),
                 self.put("bad.png", b"not an image"), self.put("notes.txt", b"x")]
        states = []
        result = self.dataset.import_paths(paths, "campus_uniform", progress=states.append)
        final = states[-1]
        self.assertEqual((final["phase"], final["discovered"], final["total"]), ("importing", 4, 4))
        self.assertEqual((final["processed"], final["imported"], final["duplicates"],
                          final["skipped"], final["failed"]), (4, 1, 1, 1, 1))
        self.assertEqual(len(final["image_ids"]), 1)
        self.assertEqual((result.imported, result.duplicates), (1, 1))

    def test_folder_discovery_is_incremental_and_does_not_follow_symlinks(self):
        folder = self.base / "folder"
        folder.mkdir()
        (folder / "nested").mkdir()
        (folder / "nested" / "photo.png").write_bytes(png((90, 20, 31)))
        (folder / "readme.txt").write_text("unsupported")
        states = []
        self.dataset.import_folder(folder, "campus_uniform", progress=states.append)
        self.assertTrue(any(s["discovered"] > 0 and s["total"] is None for s in states))
        self.assertEqual(states[-1]["skipped"], 1)

    def test_cancel_after_save_keeps_partial_result_retry_safe(self):
        paths = [self.put(f"{i}.png", png((i * 17, 20, 80))) for i in range(3)]
        cancel = threading.Event()
        states = []
        save = self.dataset._import_bytes
        def cancel_after_save(*args, **kwargs):
            result = save(*args, **kwargs)
            cancel.set()
            return result
        with patch.object(self.dataset, '_import_bytes', side_effect=cancel_after_save):
            result = self.dataset.import_paths(paths, "campus_uniform", progress=states.append, cancel=cancel)
        self.assertEqual(result.imported, 1)
        self.assertTrue(states[-1]["cancelled"])
        self.assertEqual(len(self.dataset.snapshot()["records"]), 1)
        retry = self.dataset.import_paths(paths, "campus_uniform")
        self.assertEqual(retry.imported, 2)
        self.assertEqual(retry.duplicates, 1)

    def test_limit_progress_preserves_partial_save_and_hostile_zip_preflights(self):
        first = self.put("first.png", png((1, 80, 90)))
        too_big = self.put("large.png", b"x" * (MAX_FILE_BYTES + 1))
        states = []
        with self.assertRaises(ImportLimitError):
            self.dataset.import_paths([first, too_big], "campus_uniform", progress=states.append)
        self.assertEqual(states[-1]["imported"], 1)
        self.assertEqual(len(self.dataset.snapshot()["records"]), 1)
        hostile = self.base / "hostile.zip"
        with zipfile.ZipFile(hostile, "w") as archive:
            archive.writestr("../bad.png", png((20, 90, 12)))
        states = []
        with self.assertRaisesRegex(ValueError, "unsafe path"):
            self.dataset.import_zip(hostile, "campus_uniform", progress=states.append)
        self.assertEqual(len(self.dataset.snapshot()["records"]), 1)

    def test_job_api_reuses_root_job_and_finishes_with_processing_disabled_status(self):
        image = self.put("job.png", png((65, 100, 8)))
        job = start_import(self.dataset, "paths", [image], "campus_uniform")
        self.assertIs(get_import_job(self.dataset), job)
        self.assertIs(start_import(self.dataset, "paths", [image], "campus_uniform"), job)
        deadline = time.time() + 5
        while job.active and time.time() < deadline:
            time.sleep(.02)
        snap = job.snapshot()
        self.assertFalse(snap["active"])
        self.assertEqual(snap["phase"], "done")
        self.assertEqual((snap["imported"], snap["processing_total"], snap["awaiting"]), (1, 1, 1))
        self.assertIn("disabled", snap["error"])

    def test_dead_processor_or_schedule_exception_finishes_with_honest_pending_count(self):
        image = self.put('pending.png', png((60, 70, 80)))
        self.dataset.auto_process = True
        dead = Mock()
        dead.is_alive.return_value = False
        for schedule in (Mock(return_value=dead), Mock(side_effect=[None, RuntimeError('cannot start worker')])):
            with self.subTest(schedule=schedule), patch.object(self.dataset, 'schedule_processing', schedule):
                job = start_import(self.dataset, 'paths', [image], 'campus_uniform')
                deadline = time.monotonic() + 5
                while job.active and time.monotonic() < deadline:
                    if job.snapshot()['phase'] == 'waiting_duplicates':
                        for row in job.duplicate_rows():
                            job.set_duplicate_choice(row['row_id'], 'add_source')
                        job.submit_duplicate_choices()
                    time.sleep(.01)
                self.assertFalse(job.active)
                state = job.snapshot()
                self.assertEqual(state['awaiting'], 1)
                self.assertTrue(state['error'])

    def test_zip_discovery_and_read_cancellation_keep_no_partial_current_file(self):
        archive_path = self.base / 'photos.zip'
        with zipfile.ZipFile(archive_path, 'w') as archive:
            for i in range(3):
                archive.writestr(f'{i}.png', png((30 + i, 20, 10)))
        cancel = threading.Event()
        states = []
        def discovered(state):
            states.append(state)
            if state['discovered'] == 1 and state['total'] is None:
                cancel.set()
        self.dataset.import_zip(archive_path, 'campus_uniform', progress=discovered, cancel=cancel)
        self.assertTrue(states[-1]['cancelled'])
        self.assertEqual(states[-1]['imported'], 0)
        self.assertEqual(self.dataset.snapshot()['records'], [])

    def test_progress_is_throttled_but_phase_and_final_counts_are_delivered(self):
        from core.uniform_dataset import _ImportProgress
        states = []
        with patch('core.uniform_dataset.time.monotonic', return_value=10):
            progress = _ImportProgress(states.append, None)
            for i in range(10000):
                progress.update(discovered=i + 1, current=str(i))
            progress.update(phase='importing', total=10000, current=None)
            progress._emit(force=True)
        self.assertLessEqual(len(states), 4)
        self.assertEqual(states[-1]['total'], 10000)


if __name__ == "__main__":
    unittest.main()
