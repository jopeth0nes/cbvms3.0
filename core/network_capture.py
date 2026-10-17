"""Cancellable network capture with a killable FFmpeg owner and latest-only frames."""
from collections import deque
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import threading
import time

import cv2
import numpy as np

MAX_FRAME_BYTES=4096*2160*3
MESSAGES={
    'timeout':'Connection timed out; check camera address, subnet and stream port.',
    'unreachable':'Camera host or port is unreachable. Check power, address and Ethernet subnet.',
    'authentication_failed':'Authentication rejected. Check the camera username/password.',
    'invalid_stream':'Stream URI not found. Check the profile/path.',
    'unsupported_stream':'The stream codec or format is unsupported by this decoder.',
    'open_failed':'Stream could not be opened. Check network, credentials and stream URI.',
    'no_frames':'Stream opened but supplied no video frames.',
    'stalled':'The camera stopped delivering frames.',
    'cancelled':'Connection cancelled.',
    'error':'Camera worker failed.',
}


class NetworkStream:
    def __init__(self,url,*,open_timeout_ms=5000,read_timeout_ms=2500,transport='tcp',
                 cancel_event=None,first_frame_timeout=8.0):
        if transport not in ('tcp','udp'):
            raise ValueError('Choose TCP or UDP transport.')
        self.url=url;self.transport=transport
        self.open_timeout_ms=open_timeout_ms;self.read_timeout_ms=read_timeout_ms
        self.cancel_event=cancel_event or threading.Event()
        self.first_frame_timeout=first_frame_timeout
        self.last_error=None;self.error_code=None;self._proc=None
        self._lock=threading.Lock();self._frame=None;self._header={};self._sequence=0
        self._delivered_header={};self._delivered=0;self._settings={};self._times=deque(maxlen=90)
        self._ready=threading.Event();self._closed=threading.Event();self._opened=False
        self._threads=[];self._started=0.;self._native_code=None

    def _error(self,code):
        self.error_code=self._native_code or code
        self.last_error=MESSAGES.get(self.error_code,MESSAGES['error'])

    def open(self):
        try:
            return self._open_owned()
        except Exception:
            self._error('error')
            self.release()
            return False

    def _open_owned(self):
        if self.cancel_event.is_set(): self._error('cancelled');return False
        self._started=time.monotonic()
        env={k:v for k,v in os.environ.items() if k in ('PATH','SYSTEMROOT','WINDIR','TMPDIR')}
        env.update(PYTHONPATH=str(Path(__file__).resolve().parents[1]),PYTHONUNBUFFERED='1')
        self._proc=subprocess.Popen([sys.executable,'-m','core.network_camera_worker'],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            cwd=str(Path(__file__).resolve().parents[1]),env=env)
        self._proc.stdin.write(json.dumps({'url':self.url,'transport':self.transport,
            'open_timeout_ms':self.open_timeout_ms,'read_timeout_ms':self.read_timeout_ms}).encode()+b'\n')
        self._proc.stdin.flush()  # Remains open as a parent-lifetime pipe.
        for target in (self._receive,self._diagnose):
            thread=threading.Thread(target=target,daemon=True,name='network-camera-pipe')
            self._threads.append(thread);thread.start()
        while not self._ready.wait(.02):
            if self.cancel_event.is_set(): self._error('cancelled');break
            if time.monotonic()-self._started>=self.first_frame_timeout:
                self._error('no_frames' if self._opened else 'timeout');break
            if self._proc.poll() is not None:
                self._error('no_frames' if self._opened else 'open_failed');break
        if self.cancel_event.is_set(): self._error('cancelled')
        if self._frame is not None and not self.cancel_event.is_set():
            self._settings['first_frame_ms']=round((time.monotonic()-self._started)*1000,2)
            return True
        if self.error_code is None: self._error('open_failed')
        self.release()
        # Drain native diagnostics before choosing the final public category.
        self._error(self.error_code or 'open_failed')
        return False

    def _exact(self,n):
        data=bytearray()
        while len(data)<n:
            part=self._proc.stdout.read(n-len(data))
            if not part: raise EOFError()
            data.extend(part)
        return data

    def _receive(self):
        try:
            while not self._closed.is_set():
                length=struct.unpack('!I',self._exact(4))[0]
                if length>16384: raise ValueError()
                header=json.loads(self._exact(length))
                if header['kind']=='opened':
                    self._opened=True;self._settings=header['settings']
                elif header['kind']=='frame':
                    h,w,c=header['shape'];size=h*w*c
                    if c!=3 or min(h,w)<=0 or size>MAX_FRAME_BYTES: raise ValueError()
                    frame=np.frombuffer(self._exact(size),dtype=np.uint8).reshape(h,w,c)
                    with self._lock:
                        self._frame=frame;self._header=header;self._sequence+=1
                        self._times.append(header['captured_at'])
                    self._ready.set()
                elif header['kind']=='error':
                    self._error(header.get('code','open_failed'))
        except (EOFError,OSError,ValueError,KeyError,TypeError):
            pass

    def _diagnose(self):
        # Native stderr may contain a password-bearing URL. Never retain or log it;
        # only recognized failure categories cross the process boundary to the UI.
        tail=b''
        try:
            while True:
                part=os.read(self._proc.stderr.fileno(),1024)
                if not part: break
                tail=(tail+part)[-4096:]
                low=tail.lower()
                if b'401' in low or b'unauthorized' in low or b'403 forbidden' in low:
                    self._native_code='authentication_failed'
                elif b'connection refused' in low or b'no route to host' in low:
                    self._native_code='unreachable'
                elif b'connection timed out' in low or b'operation timed out' in low or b'stream timeout' in low:
                    self._native_code='no_frames' if self._opened else 'timeout'
                elif b'404 not found' in low: self._native_code='invalid_stream'
                elif b'unsupported codec' in low or b'decoder not found' in low:
                    self._native_code='unsupported_stream'
        except (OSError,ValueError): pass

    def isOpened(self):
        return self._frame is not None and not self._closed.is_set() and not self.cancel_event.is_set()

    def read(self):
        if not self.isOpened(): return False,None
        with self._lock:
            age=time.monotonic()-self._header.get('captured_at',0)
            if age>3 or self._proc.poll() is not None:
                self._error('stalled');self._closed.set();return False,None
            if self._sequence==self._delivered: return False,None
            self._delivered=self._sequence
            self._delivered_header=dict(self._header)
            return True,self._frame.copy()

    @property
    def captured_at(self): return self._delivered_header.get('captured_at')

    @property
    def captured_wall_time(self): return self._delivered_header.get('wall_time')

    def get(self,prop):
        return self._settings.get({cv2.CAP_PROP_FRAME_WIDTH:'width',cv2.CAP_PROP_FRAME_HEIGHT:'height',
                                   cv2.CAP_PROP_FPS:'fps'}.get(prop,''),0)

    def set(self,*_args): return False  # Remote profile controls format; never pretend to set it.

    def getBackendName(self): return 'FFMPEG (isolated)'

    def get_settings(self):
        settings=dict(self._settings)
        with self._lock:
            times=list(self._times)
        settings['delivered_fps']=(len(times)-1)/(times[-1]-times[0]) if len(times)>1 and times[-1]>times[0] else 0
        return settings

    def release(self):
        self._closed.set()
        proc=self._proc
        if proc is not None:
            if proc.poll() is None:
                try: proc.terminate()
                except ProcessLookupError: pass
                try: proc.wait(timeout=.2)
                except subprocess.TimeoutExpired:
                    proc.kill();proc.wait(timeout=1)
            for thread in self._threads: thread.join(timeout=.2)
            for stream in (proc.stdin,proc.stdout,proc.stderr):
                if stream: stream.close()
        with self._lock: self._frame=None
