"""Native Tk heartbeat under delayed camera I/O; no physical devices or records."""
import queue
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
from core.camera import CameraSample
from core.model_readiness import ModelReadiness, ComponentStatus
from database.db_manager import CBVMSDatabase
from ui.dashboard import CBVMSDashboard


class NativeTransitionTests(unittest.TestCase):
    def test_delayed_open_read_release_and_navigation_keep_heartbeat_alive(self):
        owners = []
        class Capture:
            def __init__(self, *, cancel_event, **kwargs):
                self.cancel = cancel_event
                self.is_open = False
                self.last_error = None
                self.sample = None
                self.sequence = 0
                self.token = str(len(owners))
                self.threads = []
                self.released = threading.Event()
                owners.append(self)
            def open(self):
                self.threads.append(threading.get_ident())
                time.sleep(.3)  # Intentionally ignores cancellation while opening.
                self.is_open = True
                self.sample = CameraSample(np.zeros((120,160,3),np.uint8), (self.token,0),time.monotonic())
                return True
            def read(self):
                self.threads.append(threading.get_ident())
                time.sleep(.1)
                self.sequence += 1
                self.sample = CameraSample(self.sample.frame,(self.token,self.sequence),time.monotonic())
                return self.sample.frame
            def get_latest_sample(self):
                return self.sample
            def get_settings(self):
                return {}
            def release(self):
                self.threads.append(threading.get_ident())
                time.sleep(.3)
                self.is_open = False
                self.released.set()
        with tempfile.TemporaryDirectory() as folder:
            database = CBVMSDatabase(Path(folder)/'native.db')
            database.initialize()
            recognizer = MagicMock()
            recognizer.readiness = ModelReadiness()
            recognizer.readiness._status = {k:ComponentStatus('ready') for k in ('face','recognition')}
            recognizer.detect_faces.return_value = []
            recognizer.recognize_faces.return_value = []
            recognizer.last_error = None
            gate = dict(id='gate',label='front gate',url='rtsp://fake/live')
            with patch('ui.dashboard.CameraCapture',Capture), \
                 patch('api.camera_store.get_saved_ip_cameras',return_value=[gate]), \
                 patch('api.camera_store.resolve_camera_source',side_effect=lambda p,**k:p), \
                 patch('api.camera_store.save_camera_preference') as save, \
                 patch.object(CBVMSDashboard,'_load_camera_preference'), \
                 patch.object(CBVMSDashboard,'_prewarm_models',lambda self:None):
                app = CBVMSDashboard(database=database,recognizer=recognizer)
                errors, ticks, requested = [], [], []
                app.report_callback_exception = lambda *args: errors.append(args)
                def heartbeat():
                    ticks.append(time.monotonic())
                    app.after(20,heartbeat)
                def switch(label):
                    start = time.monotonic()
                    app._cam_switcher.open()
                    app._cam_switcher.select(label)
                    requested.append(time.monotonic()-start)
                app.after(20,heartbeat)
                app.after(700,lambda:switch('front gate'))
                app.after(800,lambda:switch('MacBook Camera'))
                app.after(1600,lambda:switch('front gate'))
                # Navigation during that pending switch must cancel it safely.
                app.after(1700,lambda:app._on_nav_select('enrollment'))
                app.after(2000,lambda:app._on_nav_select('live'))
                app.after(3200,app.quit)
                try:
                    app.mainloop()
                    self.assertEqual(errors,[])
                    self.assertGreater(len(ticks),60)
                    self.assertLess(max(b-a for a,b in zip(ticks,ticks[1:])),.3)
                    self.assertLess(max(requested),.05)
                    self.assertEqual(app._camera_state,'STREAMING')
                    self.assertEqual(app._camera_active_source['label'],'front gate')
                    self.assertTrue(save.called)
                finally:
                    start=time.monotonic()
                    app._on_close()
                    self.assertLess(time.monotonic()-start,.2)
                    self.assertTrue(app._camera_worker_done.wait(1))
                    self.assertTrue(app._live_worker.done.wait(2))
                    self.assertTrue(app._tracking_worker.done.wait(2))
                self.assertTrue(all(c.released.is_set() for c in owners))
                self.assertTrue(all(len(set(c.threads))==1 for c in owners))
                self.assertTrue(all(c.threads[0]!=threading.get_ident() for c in owners))
