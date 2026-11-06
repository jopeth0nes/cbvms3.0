"""Uniform detection no longer requires semester enrollment; other gates remain."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from core.student_status import uniform_monitor_eligible
from database.db_manager import CBVMSDatabase
from tests.test_live_pipeline import PipelineFixture


class UniformWithoutTermTests(unittest.TestCase):
    def test_live_pipeline_assesses_and_records_verified_student_without_term(self):
        fixture = PipelineFixture()
        fixture.database.enrollment_context.return_value = dict(eligible=False,status='Outside academic term',enrollment=None,term={})
        result = fixture.confirmed()
        assessment = result.assessments[0]
        self.assertTrue(assessment.discipline_eligible)
        self.assertEqual(assessment.accepted_categories, ('wrong_uniform',))
        fixture.persist(result)
        fixture.database.log_violation.assert_called_once()
        fixture.database.record_enrollment_review.assert_not_called()
        fixture.trainer.predict.assert_not_called()  # Term exception is uniform-only.

    def test_pending_archived_and_non_enrolled_standing_are_not_allowed(self):
        self.assertTrue(uniform_monitor_eligible(dict(student_status='Enrolled')))
        for changes in (dict(registration_pending=True),dict(is_archived=True),
                        dict(student_status='Graduate'),dict(student_status='Unenrolled')):
            self.assertFalse(uniform_monitor_eligible(dict(student_status='Enrolled') | changes))

    def test_uniform_record_is_visible_and_does_not_invent_term_or_strikes(self):
        with tempfile.TemporaryDirectory() as temporary:
            db = CBVMSDatabase(Path(temporary) / 'test.db')
            db.initialize(process_deadlines=False)
            db.insert_student('S1','Synthetic','BSIT','3A',b'',b'')
            vid = db.log_violation('S1','Synthetic','Wrong uniform',violation_code='wrong_uniform')
            self.assertIsNotNone(vid)
            rows = db.get_visible_violations_for_student('S1')
            self.assertEqual([r['id'] for r in rows], [vid])
            self.assertIsNone(rows[0]['semester_id'])
            db.confirm_violation(vid)
            with db.connect() as conn:
                self.assertEqual(conn.execute('SELECT count(*) FROM strikes').fetchone()[0],0)
                self.assertEqual(conn.execute('SELECT count(*) FROM enrollment_reviews').fetchone()[0],0)
                self.assertIsNotNone(conn.execute('SELECT appeal_opened_at FROM violations WHERE id=?',(vid,)).fetchone()[0])

    def test_successful_training_notifies_monitor_but_failure_does_not(self):
        from ui.training_panel import TrainingPanel
        callback = MagicMock()
        panel = SimpleNamespace(_training={},_ui={'uniform':{'controls':{
            'progress_bar':MagicMock(),'progress_label':MagicMock()}}},
            _set_buttons_state=MagicMock(),_refresh_counts=MagicMock(),on_model_trained=callback)
        with patch('ui.training_panel.show_toast'):
            TrainingPanel._on_train_done(panel,'uniform',False,'failed')
            callback.assert_not_called()
            TrainingPanel._on_train_done(panel,'uniform',True,'saved')
            callback.assert_called_once_with('uniform')
