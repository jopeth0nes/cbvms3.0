"""Exact session display dismissal and reconciliation without Tk/business writes."""
from collections import OrderedDict
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock

from PIL import Image
from ui.activity_dismissals import ActivityDismissals
from ui.live_alerts import LiveAlerts, LiveAlertsModel


class SessionDismissalTests(unittest.TestCase):
    def setUp(self):
        ledger = ActivityDismissals()
        self.addCleanup(ledger.close)
        self.panel = SimpleNamespace(_monitor_session_id='session-A',
            _session_dismissals=ledger, _live_session_rows=OrderedDict(),
            _persisted_session_rows=OrderedDict(), _current_monitor_generation=1,
            model=LiveAlertsModel(50), _refresh_session_activity=MagicMock())
        for name in ('update_session_assessments', 'update_persisted_session_activity',
                     'clear_session_activity', '_trim_session_rows'):
            setattr(self.panel, name, getattr(LiveAlerts, name).__get__(self.panel))

    def live(self, sequence=1, **changes):
        row = dict(monitor_session_id='session-A', source_id='gate', source_label='Gate',
            monitor_generation=1, camera_generation=1, presence_id=f'person:1:{sequence}',
            student_id=f'S{sequence}', name=f'Student {sequence}', reliable_identity=True,
            state='Uniform compliant', observed_at=1800000000, track_id=sequence)
        row.update(changes)
        return row

    def saved(self, sequence=1, **changes):
        return dict(self.live(sequence), id=sequence, event_type='attendance', **changes)

    def test_clear_only_displayed_live_keeps_unseen_and_delayed_event_distinct(self):
        p = self.panel
        p.update_session_assessments([self.live(2), self.live(1, reliable_identity=False)])
        p.clear_session_activity()
        p.update_session_assessments([] ,monitor_generation=2)
        # Older generation completion of the displayed event stays dismissed.
        # A lower, never-displayed sequence still qualifies; no range guesses.
        p.update_persisted_session_activity([self.saved(2), self.saved(1)])
        self.assertEqual(list(p._persisted_session_rows), ['attendance:1'])
        p.update_session_assessments([self.live(2)])
        self.assertFalse(p._live_session_rows)

    def test_clear_saved_type_does_not_suppress_distinct_later_type(self):
        p = self.panel
        p.update_persisted_session_activity([self.saved(1)])
        p.clear_session_activity()
        p.update_session_assessments([self.live(1)])
        self.assertFalse(p._live_session_rows)
        violation = self.saved(1)
        violation['event_type'] = 'violation'
        p.update_persisted_session_activity([self.saved(1), violation, self.saved(2)])
        self.assertEqual(set(p._persisted_session_rows), {'violation:1', 'attendance:2'})

    def test_reconciliation_every_frame_and_after_saved_row_leaves_window(self):
        p = self.panel
        p.update_session_assessments([self.live()])
        p.update_persisted_session_activity([self.saved()])
        for _ in range(3):
            p.update_session_assessments([self.live()])
            self.assertFalse(p._live_session_rows)
        p.update_persisted_session_activity([])
        p.update_session_assessments([self.live()])
        self.assertFalse(p._live_session_rows)
        p.update_session_assessments([self.live(2)])
        self.assertEqual(len(p._live_session_rows), 1)

    def test_photo_keeps_worker_decoded_image_and_raw_bytes_never_used(self):
        photo = Image.new('RGB', (80, 40), 'red')
        self.panel.update_persisted_session_activity([self.saved(thumbnail=photo, snapshot=b'raw')])
        self.assertIs(self.panel._persisted_session_rows['attendance:1']['thumbnail'], photo)
        self.panel.update_persisted_session_activity([self.saved(snapshot=b'untrusted')])
        self.assertIsNone(self.panel._persisted_session_rows['attendance:1']['thumbnail'])

    def test_full_provenance_separates_new_visit_reused_track_and_other_session(self):
        p = self.panel
        p.update_session_assessments([self.live()])
        p.clear_session_activity()
        p.update_session_assessments([self.live(), self.live(2), self.live(3, monitor_session_id='other'),
            self.live(monitor_generation=2, camera_generation=2, presence_id='person:2:1')])
        self.assertEqual(len(p._live_session_rows), 2)
        p.update_persisted_session_activity([dict(self.saved(), monitor_session_id='other')])
        self.assertFalse(p._persisted_session_rows)

    def test_exact_backing_never_evicts_old_dismissals_and_cache_is_bounded(self):
        ledger = self.panel._session_dismissals
        ledger.add(f'event:{i}' for i in range(10000))
        self.assertTrue(ledger.contains('event:0'))
        self.assertTrue(ledger.contains('event:9999'))
        self.assertFalse(ledger.contains('event:10000'))
        self.assertEqual(ledger.connection.execute('PRAGMA cache_size').fetchone()[0], -64)
        ledger.close()
        fresh = ActivityDismissals()
        self.addCleanup(fresh.close)
        self.assertFalse(fresh.contains('event:0'))
