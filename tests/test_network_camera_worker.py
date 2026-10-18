"""Run the real worker/pipe against an instrumented decoder, without a camera."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from core.network_capture import NetworkStream


DECODER = '''
import os, time
import numpy as np
CAP_PROP_OPEN_TIMEOUT_MSEC=1
CAP_PROP_READ_TIMEOUT_MSEC=2
CAP_PROP_FOURCC=3
CAP_PROP_FRAME_WIDTH=4
CAP_PROP_FRAME_HEIGHT=5
CAP_PROP_FPS=6
CAP_FFMPEG=7
def setNumThreads(n): pass
class VideoCapture:
    def __init__(self, url, backend, params):
        transport, self.value = url.split(':')
        self.value = int(self.value)
        assert os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] == (
            'rtsp_transport;' + transport + '|fflags;discardcorrupt')
        assert backend == CAP_FFMPEG
        assert params == [1, 5000, 2, 2500]
    def isOpened(self): return True
    def get(self, prop): return {3:0, 4:16, 5:12, 6:20}[prop]
    def getBackendName(self): return 'instrumented'
    def read(self):
        time.sleep(.01)
        return True, np.full((12,16,3), self.value, np.uint8)
    def release(self): pass
'''


class WorkerPolicyTests(unittest.TestCase):
    def test_conservative_options_preserve_transport_and_legitimate_flat_frames(self):
        original = subprocess.Popen
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'cv2.py').write_text(DECODER)
            def launch(*args, **kwargs):
                kwargs['env']['PYTHONPATH'] = folder + os.pathsep + kwargs['env']['PYTHONPATH']
                return original(*args, **kwargs)
            with patch('core.network_capture.subprocess.Popen', side_effect=launch):
                for transport, value in [('tcp', 128), ('udp', 0)]:
                    with self.subTest(transport=transport, value=value):
                        cap = NetworkStream(f'{transport}:{value}', transport=transport)
                        try:
                            self.assertTrue(cap.open(), cap.last_error)
                            ok, frame = cap.read()
                            self.assertTrue(ok)
                            self.assertEqual(frame.shape, (12,16,3))
                            self.assertTrue(np.array_equal(frame, np.full((12,16,3), value, np.uint8)))
                        finally:
                            cap.release()
                        self.assertIsNotNone(cap._proc.poll())
                        self.assertFalse(any(t.is_alive() for t in cap._threads))
