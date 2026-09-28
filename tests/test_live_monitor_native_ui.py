"""Native Tk smoke checks; require a working desktop session.

No camera, models, or database is opened. Run these separately with GUI access:
    .venv/bin/python -m unittest tests.test_live_monitor_native_ui
On this macOS sandbox Tk aborts before Python can catch an exception; the parent
verification run uses approved desktop execution rather than masking that abort.
"""

import unittest

import customtkinter as ctk
import numpy as np

from ui.camera_feed import CameraFeed
from ui.live_alerts import LiveAlerts


class LiveMonitorNativeUITests(unittest.TestCase):
    def setUp(self):
        self.root = ctk.CTk()
        self.root.geometry('1000x700+80+80')
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_columnconfigure(1, weight=0)
        self.feed = CameraFeed(self.root)
        self.feed.grid(row=0, column=0, sticky='nsew')
        self.alerts = LiveAlerts(self.root, width=250)
        self.alerts.grid(row=0, column=1, sticky='nsew')
        self.pump()

    def tearDown(self):
        self.feed.cleanup()
        self.root.destroy()

    def pump(self):
        self.root.update_idletasks()
        self.root.update()
        self.root.update_idletasks()

    @staticmethod
    def row(state='Checking uniform', **extra):
        return dict(track_id=1, presence_id='native:1', student_id='S1',
            name='Student With A Very Long Name That Must Wrap Across Several Lines',
            gender='Female', student_status='Enrolled', state=state, observed_at=1800000000,
            detail='The current assessment remains incomplete while uniform evidence is gathered.',
            **extra)

    def test_native_frame_fits_canvas_and_placeholder_reuses_items(self):
        frame = np.zeros((720, 1280, 3), np.uint8)
        frame[:, :, 1] = 160
        self.assertTrue(self.feed.render(frame))
        self.pump()
        x, y, width, height = self.feed._frame_rect
        self.assertLessEqual(width, self.feed.winfo_width())
        self.assertLessEqual(height, self.feed.winfo_height())
        self.assertLess(abs(width / height - 1280 / 720), .01)
        self.assertEqual(self.feed._photo.width(), width)
        self.assertEqual(self.feed._photo.height(), height)
        image_item = self.feed._item
        self.assertTrue(self.feed.show_placeholder('Reconnecting camera…'))
        text_item = self.feed._placeholder_item
        self.assertEqual(self.feed.type(text_item), 'text')
        self.assertFalse(self.feed.show_placeholder('Reconnecting camera…'))
        self.assertEqual(self.feed.itemcget(image_item, 'state'), 'hidden')
        self.assertTrue(self.feed.render(frame))
        self.assertEqual(self.feed._item, image_item)
        self.assertEqual(self.feed._placeholder_item, text_item)
        self.assertEqual(self.feed.itemcget(image_item, 'state'), 'normal')
        self.assertEqual(self.feed.itemcget(text_item, 'state'), 'hidden')
        self.root.geometry('1200x600+80+80')
        self.pump()
        self.assertTrue(self.feed.render(frame))
        self.assertLess(abs(self.feed._frame_rect[2] / self.feed._frame_rect[3] - 1280 / 720), .01)

    def test_native_cards_wrap_update_in_place_and_clear_display_only(self):
        self.alerts.update_assessments([self.row()])
        self.pump()
        card = self.alerts._cards['native:1']
        self.assertGreater(card.winfo_height(), 100)
        self.assertLessEqual(card._labels[0].cget('wraplength'), card.winfo_width())
        self.assertIn('[T1]', card._labels[0].cget('text'))
        self.assertEqual(card._labels[3].cget('text'), 'Checking uniform')
        self.alerts.update_assessments([self.row('Suspected uniform violation', accepted_categories=('wrong_uniform',))])
        self.pump()
        self.assertIs(self.alerts._cards['native:1'], card)
        self.assertEqual(card._labels[3].cget('text'), 'Suspected uniform violation')
        self.assertNotIn('OK', card._labels[3].cget('text'))
        self.alerts.mark_all_inactive()
        self.assertIn('Earlier presence', card._labels[1].cget('text'))
        self.alerts.update_assessments([self.row()])
        self.alerts.clear()
        self.pump()
        self.assertEqual(self.alerts._cards, {})
        self.alerts.update_assessments([self.row()])
        self.assertEqual(self.alerts._cards, {})
        self.alerts.update_assessments([self.row('Uniform compliant')])
        self.pump()
        self.assertEqual(len(self.alerts._cards), 1)
        self.assertEqual(self.alerts._cards['native:1']._labels[3].cget('text'), 'Uniform compliant')


class DashboardNativeLifecycleTests(unittest.TestCase):
    def test_dashboard_start_navigation_and_shutdown_with_synthetic_camera(self):
        import tempfile
        import time
        from itertools import count
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch
        from core.camera import CameraSample
        from database.db_manager import CBVMSDatabase
        from ui.dashboard import CBVMSDashboard

        with tempfile.TemporaryDirectory() as folder:
            database = CBVMSDatabase(Path(folder) / 'dashboard.db')
            database.initialize()
            recognizer = MagicMock()
            recognizer.recognize_faces.return_value = []
            recognizer.last_error = None
            with patch.object(CBVMSDashboard, '_deferred_start_camera'), \
                 patch.object(CBVMSDashboard, '_load_camera_preference'), \
                 patch.object(CBVMSDashboard, '_prewarm_models', lambda panel: panel._models_ready.set()):
                app = CBVMSDashboard(database=database, recognizer=recognizer)
                errors = []
                app.report_callback_exception = lambda *args: errors.append(args)
                try:
                    frame = np.zeros((720, 1280, 3), np.uint8)
                    sequence = count()
                    app._camera = SimpleNamespace(is_open=True, get_latest_sample=lambda:
                        CameraSample(frame, ('synthetic', next(sequence)), time.monotonic()))
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline and (app.camera_feed._photo is None or not recognizer.recognize_faces.called):
                        app.update()
                        time.sleep(.01)
                    self.assertIsNotNone(app.camera_feed._photo)
                    self.assertTrue(recognizer.recognize_faces.called)
                    token = app._monitor_cancel
                    app._on_nav_select('enrollment')
                    self.assertTrue(token.is_set())
                    app._on_nav_select('live')
                    app.update()
                    self.assertEqual(errors, [])
                finally:
                    app._on_close()
                    self.assertTrue(app._live_worker.done.wait(2))
                    deadline = time.monotonic() + 2
                    while app._stats_busy.is_set() and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertFalse(app._stats_busy.is_set())


if __name__ == '__main__':
    unittest.main()
