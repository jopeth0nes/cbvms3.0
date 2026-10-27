import unittest
from core.academic_calendar import bounds
from ui.enrollment import enrollment_term_options, REGISTER_WITHOUT_TERM


class EnrollmentTermDefaultTests(unittest.TestCase):
    def term(self, identity=1, start='2026-06-01', end='2026-12-31', state='published'):
        first, last = bounds(start, end)
        return dict(id=identity, semester_name='First Semester', school_year='2026-2027',
                    starts_at=first, ends_before=last, publication_state=state)

    def test_current_semester_is_selected_for_new_enrollment(self):
        options, default = enrollment_term_options([self.term()], '2026-10-27T00:00:00Z')
        self.assertEqual(options[default], 1)

    def test_inclusive_manila_end_and_expiration(self):
        term = self.term()
        options, default = enrollment_term_options([term], '2026-12-31T15:59:59Z')
        self.assertEqual(options[default], 1)
        self.assertEqual(enrollment_term_options([term], '2026-12-31T16:00:00Z'),
                         ({}, REGISTER_WITHOUT_TERM))

    def test_draft_and_upcoming_do_not_become_current_default(self):
        options, default = enrollment_term_options([self.term(state='draft'),
            self.term(2, '2027-01-01', '2027-05-31')], '2026-10-27T00:00:00Z')
        self.assertEqual(list(options.values()), [2])
        self.assertEqual(default, REGISTER_WITHOUT_TERM)

    def test_ambiguous_calendar_is_rejected(self):
        with self.assertRaises(ValueError):
            enrollment_term_options([self.term(), self.term(2)], '2026-10-27T00:00:00Z')
