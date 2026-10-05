"""Guard notification side effects without audio or native UI."""
import threading
import unittest
from unittest.mock import patch
from core.notifier import Notifier


class LiveNotificationTests(unittest.TestCase):
    def test_stale_assessment_creates_no_history_listener_or_sound(self):
        notifier = Notifier()
        received = []
        notifier.subscribe(received.append)
        with patch('core.notifier.play_alert') as sound:
            self.assertIsNone(notifier.notify('Student', 'Suspended', valid_if=lambda: False))
        self.assertFalse(received)
        self.assertFalse(notifier.get_log())
        sound.assert_not_called()

    def test_event_time_and_guard_travel_to_ui(self):
        notifier = Notifier()
        notifier.sound_enabled = False
        guard = lambda: True
        received = []
        notifier.subscribe(received.append)
        notification = notifier.notify('Student', 'Suspected uniform violation',
                                       observed_at=1800000000., valid_if=guard)
        self.assertIs(received[0], notification)
        self.assertEqual(notification.timestamp, 1800000000.)
        self.assertIs(notification.valid_if, guard)
        self.assertEqual(notifier.unread_count(), 1)

    def test_sound_rechecks_cancellation_after_notification_created(self):
        notifier = Notifier()
        cancelled = threading.Event()
        from unittest.mock import Mock
        from core.detection_audio import AudioDispatcher
        backend = Mock()
        audio = AudioDispatcher(backend)
        notifier = Notifier(audio=audio)
        # Defer startup while the notification is delivered, then invalidate it.
        with patch('core.detection_audio.threading.Thread'):
            notifier.notify('Student', 'Suspended', valid_if=lambda: not cancelled.is_set())
        cancelled.set()
        worker = threading.Thread(target=audio._run)
        worker.start()
        try:
            with audio._cv:
                self.assertTrue(audio._cv.wait_for(
                    lambda: not audio._pending and audio._active is None, timeout=1))
            backend.play.assert_not_called()
        finally:
            audio.close()
            worker.join(1)

    def test_legacy_call_keeps_existing_notification_behavior(self):
        notifier = Notifier()
        notifier.sound_enabled = False
        notification = notifier.notify('Student', 'Manual alert')
        self.assertIsNone(notification.valid_if)
        self.assertEqual(notifier.get_log()[0].violation, 'Manual alert')

    def test_legacy_audio_only_entry_uses_the_same_dispatcher_and_master(self):
        from core.notifier import play_alert
        notifier=Notifier()
        with patch.object(notifier.audio,'generic') as generic:
            play_alert()
            generic.assert_called_once()
        other=Notifier()
        self.assertIs(other.audio,notifier.audio)
        notifier.sound_enabled=False
        self.assertFalse(other.sound_enabled)
        notifier.close()
        replacement=Notifier()
        self.assertIsNot(replacement.audio,notifier.audio)
        self.assertFalse(replacement.audio.closed)
        replacement.close()
