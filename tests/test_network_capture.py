"""Exercise actual process pipes, deadlines and cleanup without a physical camera."""
import os
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

from core.network_capture import NetworkStream

_CHILD = r'''
import json, os, signal, struct, sys, time
request=json.loads(sys.stdin.readline())
mode=request['url'].rsplit('/',1)[-1]
def emit(h, pixels=b''):
    raw=json.dumps(h).encode()
    sys.stdout.buffer.write(struct.pack('!I',len(raw))+raw+pixels)
    sys.stdout.buffer.flush()
if mode == 'auth':
    sys.stderr.write('401 Unauthorized: rtsp://operator:example-secret@camera/live\n')
    sys.stderr.flush()
    emit({'kind':'error','code':'open_failed'})
    sys.exit(0)
if mode == 'hang':
    signal.signal(signal.SIGTERM,signal.SIG_IGN)
else:
    emit({'kind':'opened','settings':{'width':4,'height':3,'fps':25,'codec':'test'}})
if mode in ('frames','stale'):
    for i in range(40):
        now=time.monotonic()-(4 if mode=='stale' else 0)
        emit({'kind':'frame','shape':[3,4,3],'captured_at':now,'wall_time':float(i)},bytes([i])*36)
        time.sleep(.005)
time.sleep(30)
'''


class ProcessCaptureTests(unittest.TestCase):
    def setUp(self):
        self.children=[]
        original=subprocess.Popen
        def launch(*args,**kwargs):
            child=original([sys.executable,'-c',_CHILD],**kwargs)
            self.children.append(child)
            return child
        self.patch=patch('core.network_capture.subprocess.Popen',side_effect=launch)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        for child in self.children:
            if child.poll() is None:
                child.kill();child.wait()
                self.fail('Network child was not reaped')

    def stream(self, mode, **kwargs):
        return NetworkStream('rtsp://camera/'+mode,**kwargs)

    def test_first_frame_and_latest_only_timestamp_pair(self):
        cap=self.stream('frames')
        try:
            self.assertTrue(cap.open())
            # Wait for pipe delivery, not a scheduling assumption: under real ML
            # load the child can need more than .25 s to emit all forty frames.
            deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                with cap._lock:
                    received=cap._sequence
                if received==40:break
                time.sleep(.005)
            self.assertEqual(received,40)
            ok,frame=cap.read()
            self.assertTrue(ok)
            self.assertEqual(int(frame[0,0,0]),39)
            self.assertEqual(cap.captured_wall_time,39.)
            self.assertFalse(cap.read()[0])
            self.assertEqual(cap.get_settings()['codec'],'test')
        finally:cap.release()

    def test_native_open_hang_is_killed_at_deadline(self):
        cap=self.stream('hang',first_frame_timeout=.15)
        started=time.monotonic()
        self.assertFalse(cap.open())
        self.assertLess(time.monotonic()-started,.9)
        self.assertEqual(cap.error_code,'timeout')
        self.assertFalse(any(t.is_alive() for t in cap._threads))

    def test_open_but_no_frame_has_distinct_reason(self):
        cap=self.stream('empty',first_frame_timeout=.25)
        self.assertFalse(cap.open())
        self.assertEqual(cap.error_code,'no_frames')

    def test_authentication_category_never_echoes_native_secret(self):
        cap=self.stream('auth',first_frame_timeout=1)
        self.assertFalse(cap.open())
        self.assertEqual(cap.error_code,'authentication_failed')
        self.assertNotIn('example-secret',cap.last_error)
        self.assertNotIn('rtsp://',cap.last_error)

    def test_cancel_interrupts_open_and_reaps_process(self):
        cancel=threading.Event();cap=self.stream('hang',cancel_event=cancel)
        worker=threading.Thread(target=cap.open);worker.start()
        time.sleep(.1);started=time.monotonic();cancel.set();worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertLess(time.monotonic()-started,.8)
        self.assertEqual(cap.error_code,'cancelled')

    def test_stale_frame_is_never_delivered(self):
        cap=self.stream('stale')
        try:
            self.assertTrue(cap.open())
            self.assertFalse(cap.read()[0])
            self.assertEqual(cap.error_code,'stalled')
        finally:cap.release()

    def test_parent_ffmpeg_environment_is_unchanged(self):
        with patch.dict(os.environ,{'OPENCV_FFMPEG_CAPTURE_OPTIONS':'parent-options'}):
            cap=self.stream('frames')
            try:self.assertTrue(cap.open())
            finally:cap.release()
            self.assertEqual(os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'],'parent-options')
