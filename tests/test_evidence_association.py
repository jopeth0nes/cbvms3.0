"""Deterministic association proofs; no live DB or physical camera is used."""
import gc
import io
import json
import os
import threading
import time
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import customtkinter as ctk
import cv2
from PIL import Image

from core.camera import CameraCapture, CameraSample
from core.discipline import display_local_datetime
from core.evidence_integrity import digest, original_evidence, supporting_evidence
from core.portal_state import page_snapshot
from database.db_manager import CBVMSDatabase
from tests import test_appeal_flow_completion as flow
from tests.test_appeal_flow_completion import widgets
from tests.test_live_pipeline import PipelineFixture, detection
from ui.appeals_panel import AppealsPanel


def pixels(color):
    data = io.BytesIO()
    Image.new('RGB', (80, 60), color).save(data, 'PNG')
    return data.getvalue()


class AssociationFixture(flow.AppealFlowFixture):
    def setUp(self):
        super().setUp()
        self.confirmed = datetime(2099,10,1,8,8,45,tzinfo=timezone.utc)
        self.clock_mock.return_value = self.confirmed
        self.a, self.b = pixels('red'), pixels('blue')
        self.old = self.db.log_violation('S1', 'Student One', 'wrong_uniform', self.a,
                                       detected_at='2099-09-26 01:04:19')
        self.new = self.db.log_violation('S1', 'Student One', 'wrong_uniform', self.b,
                                       detected_at='2099-10-01 08:01:16')


class EvidenceAssociationTests(AssociationFixture):
    def test_exact_ids_hashes_and_owner_survive_reopen(self):
        self.db.insert_student('S2', 'Student Two', 'BSIT', '3A', b'', b'')
        self.assertIsNone(self.db.insert_appeal(self.new, 'S2', 'Please review this explanation.', evidence=('p.png','image',self.a)))
        for vid, blob in ((self.new,self.b),(self.old,self.a)):
            aid = self.submit(vid)
            reopened = CBVMSDatabase(self.db.db_path)
            case = reopened.get_appeal_case(aid, username='admin')
            self.assertEqual((case['id'], case['violation_id'], case['student_id']), (aid, vid, 'S1'))
            self.assertEqual(case['snapshot'], blob)
            self.assertEqual(original_evidence(case)['key'], ('violation',vid,digest(blob)))
            e = case['evidence'][0]
            self.assertEqual(e['uploaded_at'],case['submitted_at'])
            self.assertEqual(e['file_sha256'],digest(e['file_data']))
            with self.assertRaises(ValueError):
                reopened.get_student_original_evidence(vid,'S2')
            self.assertEqual(reopened.get_student_appeal_evidence(aid,'S2'),[])

    def test_exact_focused_route_ignores_page_position_and_filters(self):
        aid = self.submit(self.new)
        for _ in range(12):
            self.submit(self.new_violation())
        result = page_snapshot(self.db, 'S1', 'appeals', offset=100, appeal_id=aid)
        self.assertEqual([(r['id'],r['violation_id']) for r in result['_appeals']],[(aid,self.new)])
        notice = next(n for n in self.db.get_notifications_for_student('S1') if n['violation_id']==self.new)
        self.assertEqual(notice['violation_id'], self.new)
        inbox = self.db.get_appeal_inbox(username='admin', status='pending', search='S1', limit=10, offset=10)
        self.assertIn(aid,[r['id'] for r in inbox['rows']])

    def test_original_missing_corrupt_and_mismatched_never_fall_back(self):
        aid = self.submit(self.new)
        for blob, expected in ((None,None),(b'broken',None),(self.a,digest(self.b)),(None,digest(self.b))):
            with self.subTest(blob=digest(blob)):
                with self.db.connect() as conn:
                    conn.execute('UPDATE violations SET snapshot=?, snapshot_sha256=? WHERE id=?',(blob,expected,self.new))
                case = self.db.get_appeal_case(aid,username='admin')
                source = original_evidence(case)
                self.assertIsNone(source['image'])
                self.assertTrue(source['warning'])
                self.assertEqual(source['blocked'],bool(expected))
                self.assertIsNotNone(supporting_evidence(case['evidence'][0])['image'])
        self.assertFalse(self.db.update_appeal_decision(aid,'rejected','Suspect evidence',decided_by='admin', decision_category_code='rejection.violation_confirmed'))
        self.assertEqual(self.db.get_decision_history(),[])
        self.assertTrue(self.db.update_appeal_decision(aid,'approved','Cannot substantiate with preserved original',decided_by='admin', decision_category_code='approval.detection_error'))
        self.assertEqual(self.db.get_strike_count('S1','wrong_uniform'),0)

    def test_supporting_mismatch_and_changed_record_prevent_rejection(self):
        aid = self.submit(self.new)
        case = self.db.get_appeal_case(aid, username='admin')
        keys = (original_evidence(case)['key'],supporting_evidence(case['evidence'][0])['key'])
        self.assertFalse(self.db.update_appeal_decision(aid,'rejected','reviewed',decided_by='admin',expected_violation_id=self.old, decision_category_code='rejection.violation_confirmed'))
        with self.db.connect() as conn:
            conn.execute('UPDATE evidence_files SET file_data=? WHERE appeal_id=?',(self.a,aid))
        self.assertFalse(self.db.update_appeal_decision(aid,'rejected','reviewed',decided_by='admin',expected_evidence_keys=keys, decision_category_code='rejection.violation_confirmed'))
        self.assertFalse(self.db.update_appeal_decision(aid,'rejected','reviewed',decided_by='admin', decision_category_code='rejection.violation_confirmed'))
        self.assertEqual(self.db.get_appeal_for_violation(self.new)['status'],'pending')

    def test_provenance_mismatch_and_expiry_hold(self):
        provenance = dict(captured_at='2099-10-01T08:01:16+00:00',camera_session='session-A',frame_id=['session-A',1],presence_id='p',track_id=1,face_box=[0,0,10,10])
        vid = self.db.log_violation('S1','One','wrong_uniform',self.b,detected_at='2099-10-01 08:01:16',evidence_provenance=provenance)
        row = self.db.get_student_original_evidence(vid,'S1')
        meta = json.loads(row['snapshot_provenance'])
        self.assertEqual(meta['violation_id'],vid)
        self.assertEqual(meta['sha256'],digest(self.b))
        self.assertIn('captured',original_evidence(row)['label'])
        for change in ({'captured_at':'2099-10-02T08:01:16+00:00'}, {'camera_session':'different-session'}):
            changed = dict(row, snapshot_provenance=json.dumps(dict(meta, **change)))
            self.assertTrue(original_evidence(changed)['blocked'])
        meta['student_id']='S2'
        with self.db.connect() as conn:
            conn.execute('UPDATE violations SET snapshot_provenance=? WHERE id=?',(json.dumps(meta),vid))
        self.assertTrue(original_evidence(self.db.get_student_original_evidence(vid,'S1'))['blocked'])
        self.db.process_expired_deadlines(now=self.confirmed+timedelta(days=6))
        row = self.db.get_student_original_evidence(vid,'S1')
        self.assertIsNone(row['appeal_window_closed_at'])
        with self.db.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM strikes WHERE violation_id=?',(vid,)).fetchone()[0],0)

    def test_bad_provenance_rolls_back_violation_and_publication(self):
        with self.db.connect() as conn:
            before = conn.execute('SELECT COUNT(*) FROM violations').fetchone()[0]
        with self.assertRaises(ValueError):
            self.db.log_violation('S1','One','wrong_uniform',self.b,evidence_provenance={})
        with self.db.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM violations').fetchone()[0],before)

    def test_additive_migration_preserves_legacy_bytes_links_and_timestamps(self):
        aid = self.submit(self.old)
        with self.db.connect() as conn:
            conn.execute('ALTER TABLE violations DROP COLUMN snapshot_sha256')
            conn.execute('ALTER TABLE violations DROP COLUMN snapshot_provenance')
            conn.execute('ALTER TABLE evidence_files DROP COLUMN file_sha256')
            before = {t:[tuple(r) for r in conn.execute(f'SELECT * FROM {t} ORDER BY id')] for t in ('violations','appeals','evidence_files','strikes')}
        self.db.initialize(process_deadlines=False)
        self.db.initialize(process_deadlines=False)
        with self.db.connect() as conn:
            for table,rows in before.items():
                after = [tuple(r)[:len(rows[0])] for r in conn.execute(f'SELECT * FROM {table} ORDER BY id')] if rows else []
                self.assertEqual(after,rows)
        case = self.db.get_appeal_case(aid,username='admin')
        self.assertIsNone(case['snapshot_sha256'])
        self.assertIn('capture time not recorded',original_evidence(case)['label'])
        self.assertEqual(case['snapshot'],self.a)

    def test_explicit_timezone(self):
        with patch.dict(os.environ,{'CBVMS_TIMEZONE':'Asia/Manila'}):
            self.assertEqual(display_local_datetime('2026-10-01 08:01:16'), 'Oct 01, 2026 · 16:01 Asia/Manila (UTC+08:00)')


class CaptureProvenanceTests(unittest.TestCase):
    def test_two_people_have_exact_jpeg_and_geometry_from_analyzed_frame(self):
        fx = PipelineFixture()
        fx.recognizer.recognize_faces.return_value=[detection(),detection('S-2',360,embedding=1)]
        fx.detector.detect_persons.return_value=[[50,0,160,300],[330,0,450,300]]
        result = fx.confirmed()
        fx.persist(result)
        self.assertEqual(fx.database.log_violation.call_count,2)
        for call,assessment in zip(fx.database.log_violation.call_args_list,result.assessments):
            kw = call.kwargs
            x1,y1,x2,y2 = assessment.body_box
            _,jpeg = cv2.imencode('.jpg',result.task.frame[y1:y2,x1:x2],[cv2.IMWRITE_JPEG_QUALITY,85])
            self.assertEqual(kw['snapshot_jpeg'],jpeg.tobytes())
            p = kw['evidence_provenance']
            self.assertEqual(kw['student_id'],assessment.student_id)
            self.assertEqual(p['frame_id'],list(result.task.context.frame_id))
            self.assertEqual(p['face_box'],list(assessment.face_box))
            self.assertEqual(p['torso_box'],list(assessment.torso_box))
            self.assertEqual((p['presence_id'],p['track_id']),(assessment.presence_id,assessment.track_id))

    def test_reused_camera_buffer_cannot_change_persisted_jpeg(self):
        fx = PipelineFixture();result = fx.confirmed()
        buffer = result.task.frame.copy()
        task = replace(result.task,frame=buffer)
        result = replace(result,task=task)
        buffer[:]=255
        fx.persist(result)
        kw = fx.database.log_violation.call_args.kwargs
        x1,y1,x2,y2 = result.assessments[0].body_box
        _,jpeg=cv2.imencode('.jpg',task.frame[y1:y2,x1:x2],[cv2.IMWRITE_JPEG_QUALITY,85])
        self.assertEqual(kw['snapshot_jpeg'],jpeg.tobytes())
        self.assertNotEqual(digest(buffer.tobytes()),digest(task.frame.tobytes()))

    def test_cross_frame_assessment_is_never_persisted(self):
        fx=PipelineFixture();result=fx.confirmed()
        other=fx.task(5)
        fx.persist(replace(result,task=other))
        fx.database.log_violation.assert_not_called()

    def test_new_camera_sessions_cannot_reuse_frame_identity(self):
        first,second=CameraCapture(),CameraCapture()
        self.assertNotEqual(first._source_token,second._source_token)
        token=first._source_token
        with patch.object(first,'_try_open_index',return_value=None):first.open()
        self.assertNotEqual(first._source_token,token)


class EvidenceAssociationUITests(AssociationFixture):
    until = flow.AppealFlowWidgetTests.until
    loaded = flow.AppealFlowWidgetTests.loaded
    navigate = flow.AppealFlowWidgetTests.navigate
    portal = flow.AppealFlowWidgetTests.portal
    button = staticmethod(flow.AppealFlowWidgetTests.button)
    tearDown = flow.AppealFlowWidgetTests.tearDown

    def setUp(self):
        gc.collect();super().setUp()
        self.root=None;self.errors=[]
        ui_clock=patch('ui.student_portal.utc_now',side_effect=lambda:self.clock_mock.return_value)
        ui_clock.start();self.addCleanup(ui_clock.stop)

    def assert_source(self,source,vid,blob,color):
        self.assertEqual(source['key'],('violation',vid,digest(blob)))
        self.assertEqual(source['image'].getpixel((0,0)),color)

    def test_october_card_form_retry_admin_enlarge_reopen_and_older_evidence(self):
        portal=self.portal()
        selected=next(r for r in portal._violations if r['id']==self.new)
        # Invoke the actual card callback, then mutate caller metadata to prove ownership.
        host=ctk.CTkFrame(portal)
        button=portal._make_appeal_button(host,selected);button.invoke()
        modal=next(w for w in portal.winfo_children() if isinstance(w,ctk.CTkToplevel))
        self.assertEqual(modal._violation_id,self.new)
        selected['id']=self.old
        self.until(lambda:any(getattr(w,'_evidence_key',None)==('violation',self.new,digest(self.b)) for w in widgets(modal)))
        preview=next(w for w in widgets(modal) if hasattr(w,'_evidence_key'))
        self.assertEqual(preview._evidence_image.getpixel((0,0)),(0,0,255))
        self.button(modal,'View Detection Evidence').invoke()
        self.until(lambda:portal._action_request is None)
        enlarged=next(w for w in portal.winfo_children() if isinstance(w,ctk.CTkToplevel) and w is not modal)
        self.assertEqual(enlarged._evidence_key,('violation',self.new,digest(self.b)))
        self.assertEqual(enlarged._evidence_image.getpixel((0,0)),(0,0,255))
        enlarged.destroy()
        reason=next(w for w in widgets(modal) if isinstance(w,ctk.CTkTextbox))
        reason.insert('1.0','Please review this October detection and supporting image.')
        path=Path(self.tmp.name)/'support.png';path.write_bytes(b'invalid')
        with patch('ui.student_portal.filedialog.askopenfilename',return_value=str(path)):
            self.button(modal,'Browse…').invoke()
        self.button(modal,'Submit Appeal').invoke();self.until(lambda:portal._action_request is None)
        self.assertIsNone(self.db.get_appeal_for_violation(self.new))
        path.write_bytes(pixels('green'))
        portal._refresh_appeal_buttons()
        with patch('ui.student_portal.analyze_appeal'):
            self.button(modal,'Submit Appeal').invoke()
            self.until(lambda:portal._active=='appeals' and portal._action_request is None)
        self.loaded()
        aid=self.db.get_appeal_for_violation(self.new)['id']
        self.assertIsNone(self.db.get_appeal_for_violation(self.old))
        panel=AppealsPanel(portal,database=self.db,username='admin');panel.grid(row=0,column=1,sticky='nsew')
        try:
            for current,vid,blob,color in ((aid,self.new,self.b,(0,0,255)),(self.submit(self.old),self.old,self.a,(255,0,0)),(aid,self.new,self.b,(0,0,255))):
                panel.open_case(current);self.until(lambda:panel.case.get('id')==current)
                self.assert_source(panel.case['original'],vid,blob,color)
                source=panel.case['original']
                panel._enlarge(source['image'],source['label'],source['key'])
                window=panel._case_windows[-1]
                self.assertEqual(window._evidence_key,source['key'])
                self.assertEqual(window._evidence_image.getpixel((0,0)),color)
                panel.refresh()
                self.assertFalse(window.winfo_exists())
                self.assertEqual(panel.case,{})
                self.until(lambda:panel.case.get('id')==current)
            portal._view_appeal(aid);self.loaded()
            self.assertEqual([(r['id'],r['violation_id']) for r in portal._appeals],[(aid,self.new)])
        finally:panel.destroy()

    def test_stale_payload_wrong_association_and_hidden_workspace_are_discarded(self):
        portal=self.portal();old=self.submit(self.old);new=self.submit(self.new)
        panel=AppealsPanel(portal,database=self.db,username='admin')
        try:
            panel.open_case(old);self.until(lambda:panel.case.get('id')==old)
            stale=dict(panel.case)
            panel.open_case(new)
            self.assertEqual(panel.case,{})
            panel._case_loaded(stale)
            self.assertEqual(panel.case,{})
            self.until(lambda:panel.case.get('id')==new)
            self.assert_source(panel.case['original'],self.new,self.b,(0,0,255))
            wrong=dict(panel.case,violation_id=self.old)
            panel._case_loaded(wrong)
            self.assertEqual(panel.violation_id,self.new)
            panel.on_hide();panel._case_loaded(stale)
            self.assertEqual(panel.case,{})
        finally:panel.destroy()

    def test_late_form_image_is_discarded_on_navigation(self):
        portal=self.portal()
        entered,release=threading.Event(),threading.Event()
        original=CBVMSDatabase.get_student_original_evidence
        def delayed(db,vid,sid):
            entered.set();release.wait(2)
            return original(db,vid,sid)
        with patch.object(CBVMSDatabase,'get_student_original_evidence',delayed):
            portal._open_appeal_form(next(r for r in portal._violations if r['id']==self.new))
            self.assertTrue(entered.wait(1))
            modal=portal._evidence_windows[-1]
            portal._show('notifications')
            self.assertFalse(modal.winfo_exists())
            release.set();self.loaded()
        self.assertEqual(self.errors,[])

    def test_integrity_warning_disables_reject_in_native_case(self):
        portal=self.portal();aid=self.submit(self.new)
        with self.db.connect() as conn:conn.execute('UPDATE violations SET snapshot=? WHERE id=?',(self.a,self.new))
        panel=AppealsPanel(portal,database=self.db,username='admin')
        try:
            panel.open_case(aid);self.until(lambda:panel.case.get('id')==aid)
            self.assertIsNone(panel.case['images'][0])
            self.assertEqual(panel.reject.cget('state'),'disabled')
            panel._enable_decisions(True)
            self.assertEqual(panel.reject.cget('state'),'disabled')
            self.assertEqual(panel.approve.cget('state'),'normal')
        finally:panel.destroy()


if __name__=='__main__':unittest.main()
