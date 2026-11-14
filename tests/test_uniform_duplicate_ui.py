"""Native UI checks for the duplicate decision window using disposable photos."""
import gc
import tempfile
import threading
import time
import unittest
from pathlib import Path

import customtkinter as ctk
from PIL import Image

from ui.uniform_duplicate_review import DuplicateReviewWindow
from ui.window_lifecycle import WorkspaceWindow


class FakeJob:
    def __init__(self, rows):
        self.job_id = "duplicate-fixture"
        self.rows = rows
        self.choices = {}
        self.state = {"job_id": self.job_id, "phase": "waiting_duplicates", "active": True}
        self.submitted = 0
        self.cancelled = 0

    def duplicate_rows(self):
        return self.rows

    def duplicate_choices(self):
        return dict(self.choices)

    def set_duplicate_choice(self, row_id, action, apply_to_type=False):
        row = next(row for row in self.rows if row["row_id"] == row_id)
        if apply_to_type:
            for candidate in self.rows:
                if candidate["match_type"] == row["match_type"]:
                    self.choices[candidate["row_id"]] = action
        else:
            self.choices[row_id] = action

    def submit_duplicate_choices(self):
        if self.state["phase"] != "waiting_duplicates":
            raise RuntimeError("not waiting")
        self.submitted += 1
        self.state["phase"] = "importing"

    def cancel(self):
        self.cancelled += 1

    def snapshot(self):
        return dict(self.state)


class UniformDuplicateReviewUITests(unittest.TestCase):
    def setUp(self):
        gc.collect()
        self.temp = tempfile.TemporaryDirectory()
        self.root = WorkspaceWindow()
        self.root.geometry("1100x850")
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.paths = []
        for index in range(12):
            path = Path(self.temp.name) / f"photo-{index}.png"
            Image.new("RGB", (80, 80), (index * 15, 50, 100)).save(path)
            self.paths.append(path)
        self.rows = []
        types = ("exact", "similar", "conflict")
        defaults = {"exact": "skip", "similar": "keep_both", "conflict": "use_existing"}
        for i in range(9):
            match_type = types[i % 3]
            self.rows.append({"row_id": f"row-{i}", "match_type": match_type,
                "incoming_category": "Campus Uniform", "incoming_source": f"batch-{i}.png",
                "occurrences": i + 1, "incoming_path": str(self.paths[i]),
                "matches": [{"image_id": f"id-{i}", "category": "Campus Uniform",
                    "source": f"existing-{i}.png", "path": str(self.paths[i + 1]), "within_batch": False},
                    {"image_id": f"id-x-{i}", "category": "Wrong Uniform", "source": "second source",
                    "path": str(self.paths[i + 2]), "within_batch": True}], "default_action": defaults[match_type]})
        self.job = FakeJob(self.rows)
        self.window = DuplicateReviewWindow(self.root, self.job)
        self.window.update_idletasks()
        self.threads = []

    def tearDown(self):
        for thread in self.threads:
            thread.join(2)
        if self.window.winfo_exists():
            self.window.destroy()
        self.root.destroy()
        self.temp.cleanup()
        self.assertEqual(self.errors, [])

    def pump(self, ms=40):
        self.root.after(ms, self.root.quit)
        self.root.mainloop()

    def wait(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.pump(25)
        self.assertTrue(predicate(), "native duplicate review condition timed out")

    def descendants(self, widget):
        for child in widget.winfo_children():
            yield child
            yield from self.descendants(child)

    def test_defaults_choices_apply_type_and_occurrence_weighted_summary(self):
        self.assertEqual(self.job.duplicate_choices(), {})
        self.assertEqual(self.window.page_label.cget("text").split("  •")[0], "Groups 1–4 of 9")
        from ui.uniform_duplicate_review import _ACTIONS
        def menus():
            return [w for w in self.descendants(self.window.groups)
                    if isinstance(w, ctk.CTkOptionMenu) and w.cget('values')[0] in
                    {'Skip', 'Keep both', 'Use existing category'}]
        self.assertEqual([w.get() for w in menus()], ['Skip', 'Keep both', 'Use existing category', 'Skip'])
        for index, match_type in enumerate(('exact', 'similar', 'conflict')):
            for action, label in _ACTIONS[match_type]:
                menus()[index]._dropdown_callback(label)
                self.assertEqual(self.job.choices[f'row-{index}'], action)
        menus()[0]._dropdown_callback('Add source anyway')
        checkbox = next(w for w in self.descendants(self.window.groups)
                        if isinstance(w, ctk.CTkCheckBox))
        checkbox.toggle()
        self.assertEqual(self.job.choices['row-3'], 'add_source')
        self.assertEqual(menus()[3].get(), 'Add source anyway')
        self.assertIn('Add source anyway: 12', self.window.summary.cget('text'))
        self.assertIn('Skip:', self.window.summary.cget('text'))

    def test_pagination_and_match_source_selector_preserve_every_match(self):
        self.assertEqual(len(self.window._page_widgets) > 0, True)
        selectors = [w for w in self.descendants(self.window.groups)
                     if isinstance(w, ctk.CTkOptionMenu) and w.cget('values')[0].startswith('1:')]
        selectors[0]._dropdown_callback(selectors[0].cget('values')[1])
        self.assertEqual(self.window._match_indices["row-0"], 1)
        self.window.next_page()
        self.assertEqual(self.window._page, 1)
        self.assertIn("Groups 5–8 of 9", self.window.page_label.cget("text"))
        self.window.next_page()
        self.assertEqual(self.window._page, 2)
        self.assertIn("Groups 9–9 of 9", self.window.page_label.cget("text"))
        self.window.previous_page()
        self.assertEqual(self.window._page, 1)

    def test_thumbnails_are_loaded_off_tk_and_attached_on_ui_thread(self):
        main = threading.get_ident()
        worker_ids, image_ids = [], []
        original = self.window._thumbnail_task.run
        def record_run(operation, done, error):
            def wrapped():
                worker_ids.append(threading.get_ident())
                return operation()
            def completed(result):
                image_ids.append(threading.get_ident())
                done(result)
            return original(wrapped, completed, error)
        self.window._thumbnail_task.run = record_run
        self.window._load_thumbnails(0, self.rows[:4])
        self.wait(lambda: not self.window._thumbnail_busy)
        self.assertTrue(worker_ids)
        self.assertNotIn(main, worker_ids)
        self.assertEqual(set(image_ids), {main})
        self.assertTrue(self.window._images)

    def test_close_reopen_preserves_job_choices_and_never_cancels(self):
        self.job.set_duplicate_choice("row-0", "add_source")
        self.window.close()
        self.assertEqual(self.job.cancelled, 0)
        reopened = DuplicateReviewWindow(self.root, self.job)
        self.assertIsNot(reopened, self.window)
        self.assertEqual(self.job.duplicate_choices()["row-0"], "add_source")
        self.window = reopened
        self.window.update_idletasks()
        self.assertTrue(reopened.winfo_viewable())

    def test_submit_and_cancel_are_gated_by_job_id_and_waiting_phase(self):
        self.window.submit()
        self.assertEqual(self.job.submitted, 1)
        self.job.state["phase"] = "waiting_duplicates"
        self.job.state["job_id"] = "newer-job"
        self.window = DuplicateReviewWindow(self.root, self.job)
        self.window.submit()
        self.window.cancel_import()
        self.assertEqual(self.job.submitted, 1)
        self.assertEqual(self.job.cancelled, 0)


if __name__ == "__main__":
    unittest.main()
