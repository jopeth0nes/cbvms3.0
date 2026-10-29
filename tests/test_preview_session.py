import threading
import time
import unittest
from types import SimpleNamespace
from core.preview_session import PreviewSession, source_key


class FakeCapture:
    def __init__(self, **kwargs):
        self.stop = kwargs['cancel_event']
        self.is_open = False
        self.calls = []
    def open(self):
        self.calls.append(('open', threading.get_ident()))
        self.is_open = True
        return True
    def read(self):
        self.calls.append(('read', threading.get_ident()))
        self.stop.wait(.005)
        return object()
    def get_latest_sample(self):
        return SimpleNamespace(captured_at=time.monotonic(), frame_id=('test', 1))
    def release(self):
        self.calls.append(('release', threading.get_ident()))
        self.is_open = False


class PreviewTests(unittest.TestCase):
    def test_single_owner_and_cancel(self):
        captures = []
        def factory(**kw):
            captures.append(FakeCapture(**kw))
            return captures[-1]
        lock = threading.Lock()
        session = PreviewSession(dict(type='usb', index=0), capture_factory=factory, device_lock=lock)
        session.start()
        deadline = time.monotonic() + 1
        while session.sample() is None and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertIsNotNone(session.sample())
        self.assertTrue(lock.locked())
        self.assertTrue(session.stop().wait(1))
        self.assertIsNone(session.sample())
        self.assertFalse(lock.locked())
        self.assertEqual(1, len(captures))
        self.assertEqual(1, len({tid for _, tid in captures[0].calls}))
        self.assertEqual('release', captures[0].calls[-1][0])

    def test_resolver_failure_sanitized(self):
        def resolver(*args, **kw):
            raise ValueError('rtsp://secret:password@camera')
        session = PreviewSession(dict(type='ip', id='gate'), capture_factory=FakeCapture, resolver=resolver)
        session.start()
        self.assertTrue(session.done.wait(1))
        self.assertNotIn('password', session.status)
        self.assertIsNone(session.sample())

    def test_pre_cancel_never_opens(self):
        def factory(**kw):
            self.fail('must not open')
        session = PreviewSession(dict(type='usb', index=0), capture_factory=factory)
        session.stop()
        session.start()
        self.assertTrue(session.done.wait(1))

    def test_source_identity_ignores_label(self):
        self.assertEqual(source_key(dict(type='usb', index=0, label='Mac')),
                         source_key(dict(type='usb', index=0, label='renamed')))
        self.assertNotEqual(source_key(dict(type='usb', index=0)),
                            source_key(dict(type='ip', id='gate')))

    def test_failed_open_releases(self):
        cap = FakeCapture(cancel_event=threading.Event())
        cap.open = lambda: False
        session = PreviewSession(dict(type='usb'), capture_factory=lambda **kw: cap)
        session.start()
        self.assertTrue(session.done.wait(1))
        self.assertEqual('release', cap.calls[-1][0])

if __name__ == '__main__':
    unittest.main()
