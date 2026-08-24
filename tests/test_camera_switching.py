"""Regression tests for non-blocking camera selection and switching."""

from __future__ import annotations

import queue
import threading
import time
import types
import unittest
from unittest.mock import patch

from api.camera_manager import CameraManager
from core.camera import CAMERA_DEVICE_LOCK, CameraCapture
from ui.camera_configuration import CameraConfigurationSection
from ui.dashboard import CBVMSDashboard


class _FakeCapture:
    instances: list["_FakeCapture"] = []

    def __init__(self, camera_index=None, *, source_url=None, **_kwargs) -> None:
        self.camera_index = camera_index
        self.source_url = source_url
        self.is_open = False
        self.last_error = None
        self.release_count = 0
        self.__class__.instances.append(self)

    def open(self) -> bool:
        self.is_open = True
        return True

    def read(self):
        return None

    def release(self) -> None:
        self.release_count += 1
        self.is_open = False


class CameraManagerRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        _FakeCapture.instances.clear()

    @patch("api.camera_manager.save_camera_preference")
    @patch("api.camera_manager.CameraCapture", _FakeCapture)
    def test_successful_select_returns_instead_of_reentering_lock(self, _save) -> None:
        manager = CameraManager()
        result: dict = {}

        def _select() -> None:
            result.update(
                manager.select(
                    {"id": "usb_0", "type": "usb", "index": 0, "label": "Camera 0"}
                )
            )

        worker = threading.Thread(target=_select, daemon=True)
        worker.start()
        worker.join(timeout=0.5)

        self.assertFalse(worker.is_alive(), "camera selection deadlocked")
        self.assertTrue(result["success"])
        self.assertEqual(result["active"]["id"], "usb_0")

    @patch("api.camera_manager.save_camera_preference")
    @patch("api.camera_manager.CameraCapture", _FakeCapture)
    def test_two_hundred_usb_ip_switches_complete_and_keep_one_owner(self, _save) -> None:
        manager = CameraManager()
        started = time.perf_counter()
        failures: list[BaseException] = []

        def _switch_loop() -> None:
            try:
                for index in range(200):
                    if index % 2:
                        payload = {
                            "id": "ip_gate",
                            "type": "rj45",
                            "label": "Gate",
                            "url": "rtsp://example.invalid/live",
                        }
                    else:
                        payload = {
                            "id": "usb_1",
                            "type": "usb",
                            "index": 1,
                            "label": "USB Camera 1",
                        }
                    result = manager.select(payload)
                    if not result["success"] or result["active"]["id"] != payload["id"]:
                        raise AssertionError(f"bad selection result: {result}")

                    open_handles = sum(cap.is_open for cap in _FakeCapture.instances)
                    if open_handles != 1:
                        raise AssertionError(f"expected one owner, found {open_handles}")
            except BaseException as exc:
                failures.append(exc)

        worker = threading.Thread(target=_switch_loop, daemon=True)
        worker.start()
        worker.join(timeout=2.0)

        self.assertFalse(worker.is_alive(), "stress loop deadlocked")
        if failures:
            raise failures[0]

        self.assertLess(time.perf_counter() - started, 1.0)
        self.assertTrue(all(cap.release_count == 1 for cap in _FakeCapture.instances[:-1]))


class DashboardSwitchRegressionTests(unittest.TestCase):
    @staticmethod
    def _switch_harness(*, current_index: int = 0, healthy: bool = True):
        harness = types.SimpleNamespace()
        harness._cam_sources = {
            "USB Camera 0": {"id": "usb_0", "type": "usb", "index": 0},
            "USB Camera 1": {"id": "usb_1", "type": "usb", "index": 1},
        }
        harness._camera_source_url = None
        harness._camera_index_setting = current_index
        harness._camera = types.SimpleNamespace(is_open=healthy)
        harness._camera_switch_job = None
        harness.scheduled = []
        harness.cancelled = []
        harness.connected = []

        def _after(_self, delay, callback):
            _self.scheduled.append((delay, callback))
            return f"job-{len(_self.scheduled)}"

        def _after_cancel(_self, job):
            _self.cancelled.append(job)

        def _connected(_self, pref):
            _self.connected.append(pref)

        harness.after = types.MethodType(_after, harness)
        harness.after_cancel = types.MethodType(_after_cancel, harness)
        harness._on_camera_source_connected = types.MethodType(_connected, harness)
        harness._commit_camera_switch = types.MethodType(
            CBVMSDashboard._commit_camera_switch, harness
        )
        return harness

    def test_dropdown_callback_returns_before_reconfiguring_or_opening(self) -> None:
        harness = self._switch_harness(current_index=0, healthy=True)
        started = time.perf_counter()
        CBVMSDashboard._on_switch_camera(harness, "USB Camera 1")
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 0.05)
        self.assertEqual(harness.connected, [])
        self.assertEqual(len(harness.scheduled), 1)
        self.assertEqual(harness.scheduled[0][0], 1)

        with patch("api.camera_store.save_camera_preference"), patch(
            "ui.dashboard.show_toast"
        ):
            harness.scheduled[0][1]()
        self.assertEqual(harness.connected[0]["index"], 1)

    def test_different_usb_index_is_not_treated_as_same_camera(self) -> None:
        harness = self._switch_harness(current_index=0, healthy=True)
        CBVMSDashboard._on_switch_camera(harness, "USB Camera 1")
        self.assertEqual(len(harness.scheduled), 1)

    def test_failed_camera_can_be_reselected_to_retry(self) -> None:
        harness = self._switch_harness(current_index=0, healthy=False)
        CBVMSDashboard._on_switch_camera(harness, "USB Camera 0")
        self.assertEqual(len(harness.scheduled), 1)

    def test_discovered_usb_camera_is_added_to_quick_switcher(self) -> None:
        configured = {}
        selected = []
        harness = types.SimpleNamespace(
            _discovered_usb_sources=[
                {
                    "id": "usb_2",
                    "type": "usb",
                    "index": 2,
                    "label": "Document Camera",
                    "status": "available",
                }
            ],
            _camera_source_url=None,
            _camera_index_setting=2,
            _cam_switcher=types.SimpleNamespace(
                configure=lambda **kwargs: configured.update(kwargs)
            ),
            _cam_source_var=types.SimpleNamespace(set=lambda value: selected.append(value)),
        )

        with patch("api.camera_store.get_saved_ip_cameras", return_value=[]):
            CBVMSDashboard._refresh_camera_switcher(harness)

        self.assertIn("Document Camera", configured["values"])
        self.assertEqual(harness._cam_sources["Document Camera"]["index"], 2)
        self.assertEqual(selected[-1], "Document Camera")

    def test_stopping_camera_only_signals_worker_and_never_joins_ui_thread(self) -> None:
        harness = types.SimpleNamespace()
        stop = threading.Event()
        done = threading.Event()
        harness._camera = object()
        harness._camera_reader = object()
        harness._camera_reader_stop = stop
        harness._camera_worker_done = done

        started = time.perf_counter()
        returned_done = CBVMSDashboard._stop_camera(harness)
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 0.05)
        self.assertIs(returned_done, done)
        self.assertTrue(stop.is_set())
        self.assertIsNone(harness._camera)
        self.assertIsNone(harness._camera_reader)

    def test_stale_open_event_never_releases_capture_on_ui_thread(self) -> None:
        class BlockingRelease:
            last_error = None
            release_calls = 0

            def release(self):
                self.release_calls += 1
                time.sleep(0.25)

        stale = BlockingRelease()
        harness = types.SimpleNamespace(_camera_generation=2, _camera=object())

        started = time.perf_counter()
        CBVMSDashboard._on_camera_opened(harness, stale, True, 1)
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 0.05)
        self.assertEqual(stale.release_calls, 0)

    def test_new_open_waits_for_previous_worker_without_blocking_ui(self) -> None:
        previous_done = threading.Event()
        harness = types.SimpleNamespace()
        harness._camera_generation = 0
        harness._last_frame_request = 0.0
        harness._face_frame_counter = 99
        harness._camera_spinner = types.SimpleNamespace(pack=lambda **_k: None, start=lambda: None)
        harness._status_camera = types.SimpleNamespace(configure=lambda **_k: None)
        harness.callbacks = []
        harness.started_generations = []

        def _stop(_self):
            return previous_done

        def _after(_self, _delay, callback):
            _self.callbacks.append(callback)
            return f"job-{len(_self.callbacks)}"

        def _start(_self, generation):
            _self.started_generations.append(generation)

        harness._stop_camera = types.MethodType(_stop, harness)
        harness.after = types.MethodType(_after, harness)
        harness._start_camera_worker = types.MethodType(_start, harness)

        started = time.perf_counter()
        CBVMSDashboard._deferred_start_camera(harness)
        self.assertLess(time.perf_counter() - started, 0.05)

        first_poll = harness.callbacks.pop(0)
        first_poll()
        self.assertEqual(harness.started_generations, [])

        previous_done.set()
        second_poll = harness.callbacks.pop(0)
        second_poll()
        self.assertEqual(harness.started_generations, [1])

    def test_intentional_halt_invalidates_pending_start_and_retry(self) -> None:
        previous_done = threading.Event()
        harness = types.SimpleNamespace()
        harness._camera_generation = 0
        harness._last_frame_request = 0.0
        harness._face_frame_counter = 0
        harness._camera_spinner = types.SimpleNamespace(pack=lambda **_k: None, start=lambda: None)
        harness._status_camera = types.SimpleNamespace(configure=lambda **_k: None)
        harness.callbacks = []
        harness.started_generations = []
        harness.retry_starts = 0

        harness.after = types.MethodType(
            lambda self, _delay, callback: self.callbacks.append(callback) or "job", harness
        )
        harness._stop_camera = types.MethodType(lambda _self: previous_done, harness)
        harness._start_camera_worker = types.MethodType(
            lambda self, generation: self.started_generations.append(generation), harness
        )

        CBVMSDashboard._deferred_start_camera(harness)
        pending_start = harness.callbacks.pop(0)
        harness._stop_camera = types.MethodType(lambda _self: previous_done, harness)
        CBVMSDashboard._halt_camera(harness)
        previous_done.set()
        pending_start()
        self.assertEqual(harness.started_generations, [])

        harness._camera_needed = types.MethodType(lambda _self: True, harness)
        harness._deferred_start_camera = types.MethodType(
            lambda self: setattr(self, "retry_starts", self.retry_starts + 1), harness
        )
        CBVMSDashboard._retry_camera(harness, generation=1)
        self.assertEqual(harness.retry_starts, 0)

    @patch("ui.dashboard.CameraCapture")
    def test_worker_owns_open_read_and_release_on_one_non_ui_thread(self, capture_cls) -> None:
        calls: dict[str, int] = {}
        fake = types.SimpleNamespace(is_open=False, last_error=None)

        def _open():
            calls["open"] = threading.get_ident()
            fake.is_open = True
            return True

        def _read():
            calls["read"] = threading.get_ident()
            fake.is_open = False
            return None

        def _release():
            calls["release"] = threading.get_ident()

        fake.open = _open
        fake.read = _read
        fake.release = _release
        capture_cls.return_value = fake

        harness = types.SimpleNamespace(
            _camera_generation=3,
            _camera_source_url=None,
            _camera_index_setting=0,
            _camera_resolution_setting=(640, 480),
            _fps_cap_setting=30,
            _camera=None,
            _camera_reader=None,
            _camera_reader_stop=None,
            _camera_worker_done=threading.Event(),
            _camera_events=__import__("queue").Queue(),
        )
        ui_thread = threading.get_ident()

        CBVMSDashboard._start_camera_worker(harness, 3)
        harness._camera_reader.join(timeout=1.0)

        self.assertFalse(harness._camera_reader.is_alive())
        self.assertEqual(calls["open"], calls["read"])
        self.assertEqual(calls["read"], calls["release"])
        self.assertNotEqual(calls["open"], ui_thread)
        self.assertTrue(harness._camera_worker_done.is_set())

    @patch("ui.dashboard.CameraCapture")
    def test_release_error_still_completes_worker(self, capture_cls) -> None:
        fake = types.SimpleNamespace(is_open=False, last_error=None)
        fake.open = lambda: False
        fake.read = lambda: None
        fake.release = lambda: (_ for _ in ()).throw(RuntimeError("release failed"))
        capture_cls.return_value = fake
        harness = types.SimpleNamespace(
            _camera_generation=4,
            _camera_source_url=None,
            _camera_index_setting=0,
            _camera_resolution_setting=(640, 480),
            _fps_cap_setting=30,
            _camera=None,
            _camera_reader=None,
            _camera_reader_stop=None,
            _camera_worker_done=threading.Event(),
            _camera_events=__import__("queue").Queue(),
        )

        CBVMSDashboard._start_camera_worker(harness, 4)
        harness._camera_reader.join(timeout=1.0)

        self.assertFalse(harness._camera_reader.is_alive())
        self.assertTrue(harness._camera_worker_done.is_set())

    @patch("ui.dashboard.CameraCapture")
    def test_superseded_worker_does_not_open_after_waiting_for_device(self, capture_cls) -> None:
        open_calls = []
        fake = types.SimpleNamespace(is_open=False, last_error=None)
        fake.open = lambda: open_calls.append(threading.get_ident()) or True
        fake.read = lambda: None
        fake.release = lambda: None
        capture_cls.return_value = fake
        harness = types.SimpleNamespace(
            _camera_generation=7,
            _camera_source_url=None,
            _camera_index_setting=0,
            _camera_resolution_setting=(640, 480),
            _fps_cap_setting=30,
            _camera=None,
            _camera_reader=None,
            _camera_reader_stop=None,
            _camera_worker_done=threading.Event(),
            _camera_events=__import__("queue").Queue(),
        )

        with CAMERA_DEVICE_LOCK:
            CBVMSDashboard._start_camera_worker(harness, 7)
            time.sleep(0.01)
            done = CBVMSDashboard._stop_camera(harness)
            self.assertFalse(done.is_set())

        done.wait(timeout=1.0)
        self.assertTrue(done.is_set())
        self.assertEqual(open_calls, [])

    def test_frame_consumers_never_call_blocking_read(self) -> None:
        sentinel = object()

        class Capture:
            is_open = True

            def read(self):
                raise AssertionError("read() must remain on the camera worker")

            def get_latest_frame(self):
                return sentinel

        harness = types.SimpleNamespace(
            _last_frame_request=0.0,
            _camera=Capture(),
            _acquire_camera=lambda: None,
        )
        self.assertIs(CBVMSDashboard._get_camera_frame(harness), sentinel)

    def test_live_feed_tick_uses_cached_frame_not_blocking_read(self) -> None:
        class Capture:
            is_open = True

            def read(self):
                raise AssertionError("Tk feed loop must not call read()")

            def get_latest_frame(self):
                return None

        placeholders = []
        harness = types.SimpleNamespace(
            winfo_exists=lambda: True,
            _drain_camera_events=lambda: None,
            _notification_out=queue.Queue(),
            _on_notification=lambda _notification: None,
            _violation_dirty=False,
            _camera=Capture(),
            _camera_needed=lambda: True,
            _active_nav="live",
            camera_feed=types.SimpleNamespace(
                show_placeholder=lambda: placeholders.append(True)
            ),
            _feed_interval_ms=33,
            after=lambda _delay, _callback: "feed-job",
            _feed_job=None,
            _update_feed=lambda: None,
        )

        CBVMSDashboard._update_feed(harness)
        self.assertEqual(placeholders, [True])
        self.assertEqual(harness._feed_job, "feed-job")


class CameraPreferenceTests(unittest.TestCase):
    def test_usb_preference_preserves_discovered_index(self) -> None:
        pref = CameraConfigurationSection._preference_for(
            {"id": "usb_3", "type": "usb", "index": 3, "label": "Document Camera"}
        )
        self.assertEqual(pref["index"], 3)
        self.assertEqual(pref["id"], "usb_3")

    def test_explicit_usb_selection_never_falls_back_to_another_index(self) -> None:
        capture = CameraCapture(camera_index=2)
        attempts: list[int] = []

        def _try(index: int):
            attempts.append(index)
            return None

        with patch.object(capture, "_try_open_index", side_effect=_try):
            self.assertFalse(capture.open())

        self.assertEqual(attempts, [2])
        self.assertIn("2", capture.last_error or "")


if __name__ == "__main__":
    unittest.main()
