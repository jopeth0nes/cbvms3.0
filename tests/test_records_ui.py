"""Desktop records checks using temporary data, without cameras or model loading."""
import io
import tempfile
import unittest
from pathlib import Path

import customtkinter as ctk
from PIL import Image

from database.db_manager import CBVMSDatabase
from ui.records_panel import RecordsPanel


class RecordsUITests(unittest.TestCase):
    def test_resizable_evidence_selection_and_filtered_exports(self):
        root = ctk.CTk()
        self.addCleanup(root.destroy)
        root.geometry("700x720")
        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(0, weight=1)
        with tempfile.TemporaryDirectory() as tmp:
            db = CBVMSDatabase(Path(tmp) / "records.db")
            db.initialize()
            db.insert_student("QA-1", "Alexandra Rivera", "BSIT", "3A", b"", b"")
            stream = io.BytesIO()
            Image.new("RGB", (400, 800), "steelblue").save(stream, format="PNG")
            vid = db.log_violation("QA-1", "Alexandra Rivera", "Wrong Uniform (98%)",
                                   snapshot_jpeg=stream.getvalue())
            missing = db.log_violation("unknown", "Unknown", "Unknown Person")
            corrupt = db.log_violation("QA-1", "Alexandra Rivera", "wrong_uniform",
                                       snapshot_jpeg=b"invalid image")
            panel = RecordsPanel(root, database=db)
            panel.grid(row=0, column=0, sticky="nsew")
            root.update()
            self.assertEqual(panel._vd_enlarge.cget("state"), "disabled")
            sash_x, _ = panel._viol_split.sash_coord(0)
            self.assertAlmostEqual(panel._viol_split.winfo_width() - sash_x - 8,
                                   panel._apply_widget_scaling(340), delta=3)
            self.assertLess(panel._tab_btns["violations"].winfo_rooty() - root.winfo_rooty(), 120)
            panel._viol_tree.selection_set(str(vid))
            panel._on_viol_select()
            root.update()
            self.assertEqual(panel._vd_student.cget("text"), "Alexandra Rivera")
            self.assertEqual(panel._vd_enlarge.cget("state"), "normal")
            image = panel._vd_snapshot._ref
            self.assertAlmostEqual(image.height() / image.width(), 2, delta=0.03)
            for width in (700, 1040):
                root.geometry(f"{width}x720")
                root.update()
                for fraction in (0.4, 0.55):
                    panel._viol_split.sash_place(0, int(panel._viol_split.winfo_width() * fraction), 0)
                    root.update()
                    canvas = panel._vd_snapshot
                    self.assertLessEqual(canvas._ref.width(), canvas.winfo_width())
                    self.assertLessEqual(canvas._ref.height(), canvas.winfo_height())
                    self.assertLessEqual(panel._vd_enlarge.winfo_rootx() + panel._vd_enlarge.winfo_width(),
                                         root.winfo_rootx() + root.winfo_width())
            panel._remember_violation_split(None)
            root.update()
            remembered = panel._evidence_width
            root.geometry("1100x720")
            root.update()
            sash_x, _ = panel._viol_split.sash_coord(0)
            self.assertAlmostEqual(panel._viol_split.winfo_width() - sash_x - 8,
                                   panel._apply_widget_scaling(remembered), delta=3)
            panel.refresh()
            self.assertEqual(panel._viol_tree.selection(), (str(vid),))
            panel._open_violation_evidence()
            root.update()
            for child in panel.winfo_children():
                if isinstance(child, ctk.CTkToplevel):
                    child.destroy()
            panel._viol_search.set("Unknown")
            root.update()
            self.assertIsNone(panel._snapshot_source)
            self.assertIsNone(panel._vd_snapshot._ref)
            self.assertEqual(panel._vd_enlarge.cget("state"), "disabled")
            self.assertEqual([r["id"] for r in panel._report_violations()], [missing])
            panel._viol_tree.selection_set(str(missing))
            panel._on_viol_select()
            self.assertEqual(panel._snapshot_message, "Original evidence unavailable")
            panel._viol_search.set("")
            panel._viol_tree.selection_set(str(corrupt))
            panel._on_viol_select()
            self.assertEqual(panel._snapshot_message, "Evidence unavailable: image is unreadable.")
            self.assertEqual(panel._vd_enlarge.cget("state"), "disabled")
            for tab in ("attendance", "appeals", "evidence", "history", "violations"):
                panel._switch_tab(tab)
                root.update_idletasks()
            panel.destroy()


if __name__ == "__main__":
    unittest.main()
