"""Native forensic details and decision isolation on disposable evidence."""
import base64
import io
import threading
import unittest
from unittest.mock import patch

import customtkinter as ctk
from PIL import Image

from core.appeal_categories import BY_CODE
from database.evidence_forensics import claim_next, _finish
from tests import test_appeals_workspace_ui as workspace
from tests.test_appeal_flow_completion import widgets
from ui.appeals_panel import _forensic_previews


class ForensicNativeTests(unittest.TestCase):
    setUp = workspace.AppealsWorkspaceTests.setUp
    tearDown = workspace.AppealsWorkspaceTests.tearDown
    until = workspace.AppealsWorkspaceTests.until
    open = workspace.AppealsWorkspaceTests.open

    def test_hiding_case_cancels_forensic_poll(self):
        self.open(self.ids[0])
        scheduled = self.panel._forensic_poll_job
        self.assertIsNotNone(scheduled)
        self.panel.on_hide()
        self.assertIsNone(self.panel._forensic_poll_job)
        self.assertNotIn(scheduled, self.root.tk.splitlist(self.root.tk.call('after','info')))

    def test_pending_error_inconclusive_and_review_leave_decisions_manual(self):
        aid = self.ids[0]
        self.open(aid)
        self.panel.reason.insert('1.0', 'My manual reason')
        option = BY_CODE['rejection.violation_confirmed'].option
        self.panel.category.set(option)
        self.panel._enable_decisions(True)
        for status, classification in [('pending','inconclusive'), ('analyzing','inconclusive'),
                                       ('error','inconclusive'), ('complete','inconclusive'),
                                       ('complete','review_recommended')]:
            with self.db.connect() as conn:
                conn.execute('UPDATE evidence_forensics_runs SET status=?,classification=? WHERE appeal_id=?',
                             (status, classification, aid))
            self.panel._poll_forensics(aid)
            self.until(lambda: self.panel.case['forensics'][0][1][0]['status'] == status
                       and self.panel.case['forensics'][0][1][0]['classification'] == classification)
            self.assertEqual(self.panel.reason.get('1.0','end-1c'), 'My manual reason')
            self.assertEqual(self.panel.category.get(), option)
            self.assertEqual(self.panel.reject.cget('state'), 'normal')
            self.assertEqual(self.db.get_appeal_case(aid,username='admin')['status'], 'pending')
        with patch('ui.appeals_panel.messagebox.askyesno',return_value=True):
            self.panel.reject.invoke()
            self.until(lambda: self.panel.case.get('status') == 'rejected')

    def test_maps_details_are_background_prepared_and_stale_maps_withheld(self):
        claim = claim_next(self.db)
        run_id, data = claim
        report = self.db.get_evidence_forensics_report(run_id, username='admin')
        artifact = io.BytesIO(); Image.new('L',(40,30),128).save(artifact,'PNG')
        _finish(self.db,run_id,{'sha256':report['sha256'], 'classification':'review_recommended',
                'localization_png':base64.b64encode(artifact.getvalue()).decode(),
                'reliability_png':base64.b64encode(artifact.getvalue()).decode(),
                'model':{'status':'experimental','map_coordinates':'encoded_pixels_before_exif_orientation'}},
                claim_token=data[-1])
        self.open(report['appeal_id'])
        main_thread = threading.get_ident()
        threads = []
        def prepare(*args):
            threads.append(threading.get_ident())
            return _forensic_previews(*args)
        with patch('ui.appeals_panel._forensic_previews',side_effect=prepare):
            self.panel._forensics_details(report['evidence_id'],run_id)
            self.until(lambda: bool(self.panel._case_windows))
        self.assertTrue(threads)
        self.assertNotIn(main_thread, threads)
        window = self.panel._case_windows[-1]
        buttons = {w.cget('text'):w for w in widgets(window) if isinstance(w,ctk.CTkButton)}
        for name in ('Original','Overlay','Map','Reliability'):
            buttons[name].invoke(); self.root.update()
        with self.db.connect() as conn:
            conn.execute('UPDATE evidence_files SET file_data=? WHERE id=?',(b'changed',report['evidence_id']))
        self.panel._forensics_details(report['evidence_id'],run_id)
        self.until(lambda: len(self.panel._case_windows)==2)
        stale = self.panel._case_windows[-1]
        self.assertFalse(any(isinstance(w,ctk.CTkButton) for w in widgets(stale)))
        self.assertTrue(any('withheld' in w.cget('text') for w in widgets(stale) if isinstance(w,ctk.CTkLabel)))
        self.panel._forensics_history(report['evidence_id'])
        self.until(lambda: len(self.panel._case_windows)==3)
        labels=[w.cget('text') for w in widgets(self.panel._case_windows[-1]) if isinstance(w,ctk.CTkButton)]
        self.assertTrue(any('inconclusive' in text for text in labels))
        self.assertFalse(any('review_recommended' in text for text in labels))


class MapOrientationTests(unittest.TestCase):
    def test_exif_rotation_aligns_map_and_bounds_display(self):
        source=Image.new('RGB',(80,40),'white');exif=Image.Exif();exif[274]=6
        image=io.BytesIO();source.save(image,'JPEG',exif=exif)
        mask=Image.new('L',(80,40),0);mask.paste(255,(0,0,40,40))
        encoded=io.BytesIO();mask.save(encoded,'PNG')
        modes=_forensic_previews(image.getvalue(),{'model':{'orientation':6},'localization_png':encoded.getvalue()})
        self.assertEqual(modes['Original'].size,(40,80))
        self.assertEqual(modes['Map'].getpixel((20,10)),(255,101,77))
        self.assertEqual(modes['Map'].getpixel((20,70)),(6,34,55))
