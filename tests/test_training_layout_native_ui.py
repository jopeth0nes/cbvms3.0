"""Normal desktop navigation/layout, with disposable DB/catalog and no devices."""
import gc
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import customtkinter as ctk
import numpy as np
from PIL import Image
from core.uniform_dataset import UniformDataset
from core.uniform_candidate import CandidateService
from database.db_manager import CBVMSDatabase
from ui.dashboard import CBVMSDashboard
from ui.uniform_workspace import UniformWorkspace


class TrainingLayoutTests(unittest.TestCase):
    def test_dashboard_training_controls_fit_and_touchpad_scrolls(self):
        gc.collect()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            dataset = UniformDataset(root/'catalog')
            # A full gallery page gives the real scroll container overflow.
            for index in range(12):
                path = root/f'fixture-{index}.png'
                Image.fromarray(np.random.default_rng(index).integers(
                    0, 256, (180, 200, 3), dtype=np.uint8)).save(path)
                dataset.import_paths([path], 'other_clothing')
            candidate = CandidateService(dataset, candidates_root=root/'candidates')
            db = CBVMSDatabase(root/'desktop.sqlite')
            db.initialize()
            with db.connect() as conn:
                conn.execute("INSERT INTO users (username,password_hash,role) VALUES (?,?,?)",
                             ('layout-reviewer', 'fixture-no-login', 'superadmin'))
            class TestWorkspace(UniformWorkspace):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, dataset=dataset, candidate_service=candidate, **kwargs)
            with patch('ui.uniform_workspace.UniformWorkspace', TestWorkspace), \
                 patch.object(CBVMSDashboard, '_prewarm_models'), \
                 patch.object(CBVMSDashboard, '_deferred_start_camera'), \
                 patch.object(CBVMSDashboard, '_acquire_camera'), \
                 patch('api.camera_store.get_camera_preference', return_value=None), \
                 patch('api.camera_store.get_saved_ip_cameras', return_value=[]):
                app = CBVMSDashboard('layout-reviewer', database=db)
                errors = []
                app.report_callback_exception = lambda *args: errors.append(args)
                def pump():
                    app.after(40, app.quit)
                    app.mainloop()
                def wait(predicate):
                    deadline = time.monotonic()+10
                    while not predicate() and time.monotonic()<deadline:
                        pump()
                    self.assertTrue(predicate())
                    pump()
                try:
                    pump()
                    app._nav_buttons['training'].invoke()
                    wait(lambda: app._training_panel is not None)
                    panel = app._training_panel
                    view = panel._uniform_workspace
                    wait(lambda: not view.task.busy)
                    view.tabs._segmented_button._buttons_dict['Wrong Uniform'].invoke()
                    wait(lambda: not view.task.busy)
                    self.assertEqual(view.category(), 'other_clothing')
                    self.assertEqual((app.winfo_width(),app.winfo_height()), (1280,800))
                    def descendants(w):
                        for child in w.winfo_children():
                            yield child
                            yield from descendants(child)
                    required={'Upload Photos','Import Folder','Import ZIP','Capture from Camera','Review multiple'}
                    visible={w.cget('text'):w for w in descendants(view)
                             if isinstance(w,ctk.CTkButton) and w.winfo_ismapped()}
                    self.assertTrue(required <= visible.keys())
                    for button in [visible[t] for t in required] + list(view.tabs._segmented_button._buttons_dict.values()):
                        self.assertGreaterEqual(button.winfo_rootx(),app.winfo_rootx())
                        self.assertLessEqual(button.winfo_rootx()+button.winfo_width(),app.winfo_rootx()+1280)
                        self.assertLessEqual(button.winfo_rooty()+button.winfo_height(),app.winfo_rooty()+800)
                    visible['Review multiple'].invoke()
                    wait(lambda: not view.task.busy)
                    approve = view.views['other_clothing'][4]
                    self.assertTrue(approve.winfo_ismapped())
                    self.assertLessEqual(approve.winfo_rootx()+approve.winfo_width(), app.winfo_rootx()+1280)
                    self.assertLessEqual(approve.winfo_rooty()+approve.winfo_height(), app.winfo_rooty()+800)
                    gallery=view.views['other_clothing'][0]
                    canvas=gallery._parent_canvas
                    before=canvas.yview()[0]
                    canvas.event_generate('<TouchpadScroll>',delta=65535,
                        rootx=canvas.winfo_rootx()+20,rooty=canvas.winfo_rooty()+20)
                    pump()
                    self.assertGreater(canvas.yview()[0],before)
                    for title, category in [('Campus Uniform','campus_uniform'),('Organizational Shirt','organizational_shirt')]:
                        view.tabs._segmented_button._buttons_dict[title].invoke()
                        wait(lambda:not view.task.busy)
                        self.assertEqual(view.category(),category)
                    panel._tabview._segmented_button._buttons_dict['DYCI Logo & Digital Shirts'].invoke()
                    pump()
                    self.assertEqual(panel._tabview.get(),'DYCI Logo & Digital Shirts')
                    self.assertNotIn('earring',app._readiness.snapshot())
                    self.assertEqual(errors,[])
                finally:
                    app._on_close()
                    gc.collect()
