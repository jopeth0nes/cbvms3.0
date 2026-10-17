"""Local HTTP/MJPEG decoding with real OpenCV/FFmpeg; requires loopback access."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
import unittest

import cv2
import numpy as np

from core.network_capture import NetworkStream


class RealDecoderTests(unittest.TestCase):
    def test_mjpeg_preview_authentication_and_cleanup(self):
        stop=threading.Event()
        frame=np.zeros((240,320,3),np.uint8)
        frame[:,:,1]=150
        ok,jpeg=cv2.imencode('.jpg',frame)
        self.assertTrue(ok)
        data=jpeg.tobytes()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                if self.path=='/auth':
                    self.send_response(401);self.send_header('WWW-Authenticate','Basic realm="camera"');self.end_headers();return
                self.send_response(200)
                self.send_header('Content-Type','multipart/x-mixed-replace; boundary=frame')
                self.end_headers()
                try:
                    while not stop.is_set():
                        self.wfile.write(b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: '+str(len(data)).encode()+b'\r\n\r\n'+data+b'\r\n')
                        self.wfile.flush();stop.wait(.05)
                except (BrokenPipeError,ConnectionResetError):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url=f'http://127.0.0.1:{server.server_port}'
        cap=NetworkStream(url+'/video');auth=NetworkStream(url+'/auth')
        try:
            self.assertTrue(cap.open(),cap.last_error)
            count=0;started=time.monotonic();deadline=started+2
            while time.monotonic()<deadline:
                ok,received=cap.read()
                if ok:
                    self.assertEqual(received.shape,(240,320,3));count+=1
                time.sleep(.005)
            settings=cap.get_settings()
            self.assertGreater(count,20)
            print(json.dumps({'local_mjpeg':{'delivered_fps':round(count/(time.monotonic()-started),2),
                                           'first_frame_ms':settings['first_frame_ms'],
                                           'codec':settings['codec'],'width':settings['width'],'height':settings['height']}}))
            self.assertFalse(auth.open())
            self.assertEqual(auth.error_code,'authentication_failed')
        finally:
            cap.release();auth.release();stop.set();server.shutdown();server.server_close();thread.join(2)
        self.assertIsNotNone(cap._proc.poll())
        self.assertIsNotNone(auth._proc.poll())
        self.assertFalse(any(t.is_alive() for t in cap._threads+auth._threads))

    def test_real_native_open_cancels_and_parent_eof_terminates_decoder(self):
        import socket
        import subprocess
        import sys
        from pathlib import Path
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen(2)
        listener.settimeout(.1)
        accepted = threading.Event()
        stopped = threading.Event()
        connections = []
        def serve():
            while not stopped.is_set():
                try:
                    connection, _ = listener.accept()
                    connections.append(connection)
                    accepted.set()
                except TimeoutError:
                    pass
        server = threading.Thread(target=serve)
        server.start()
        url = f'rtsp://127.0.0.1:{listener.getsockname()[1]}/stalled'
        cancel = threading.Event()
        cap = NetworkStream(url, cancel_event=cancel)
        owner = threading.Thread(target=cap.open)
        process = None
        try:
            owner.start()
            self.assertTrue(accepted.wait(3))
            started = time.monotonic()
            cancel.set()
            owner.join(1)
            self.assertFalse(owner.is_alive())
            self.assertIsNotNone(cap._proc.poll())
            print(json.dumps({'native_stalled_open_cancel_ms': (time.monotonic()-started)*1000}))
            accepted.clear()
            process = subprocess.Popen([sys.executable, '-m', 'core.network_camera_worker'],
                cwd=Path(__file__).resolve().parents[1], stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            process.stdin.write(json.dumps({'url': url}).encode()+b'\n')
            process.stdin.flush()
            self.assertTrue(accepted.wait(3))
            process.stdin.close()  # Simulate GUI exit without owner cleanup.
            self.assertEqual(process.wait(timeout=1), 0)
        finally:
            cancel.set()
            owner.join(2)
            cap.release()
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(1)
            stopped.set()
            server.join(1)
            for connection in connections:
                connection.close()
            listener.close()
