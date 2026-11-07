"""Student-status audio uses synthetic accepted assessments and silent backends."""
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import wave

import numpy as np

from core.detection_audio import (AudioDispatcher, ASSETS, LocalAudioBackend,
                                  SoundKind as K, select_sound)
from core.live_state import LiveState, FrameContext, UniformAssessment as U
from core.notifier import Notifier
from test_live_pipeline import PipelineFixture


def assessment(**changes):
    row = dict(box=(80,20,130,90), matched=True, student_id='S1', name='Student',
               embedding=(1.,0.), discipline_eligible=True, association_valid=True,
               body_box=(50,0,160,300), torso_valid=True, torso_box=(65,100,145,210),
               uniform_available=True, uniform_label='correct_uniform', uniform_conf=.95)
    state = LiveState()
    for i in range(4):
        a = state.update(FrameContext(1,i,100+i*.1), [row], now=100+i*.1)[0]
    return replace(a, **changes)


class Backend:
    def __init__(self):
        self.calls = []
        self.started = threading.Event()
        self.release = threading.Event()
        self.block = False
        self.failure = False
        self.concurrent = 0
        self.maximum = 0

    def play(self, path, valid):
        self.concurrent += 1
        self.maximum = max(self.maximum, self.concurrent)
        try:
            if valid():
                self.calls.append(path.stem)
            self.started.set()
            if self.block:
                self.release.wait(2)
            if self.failure:
                raise RuntimeError('synthetic device failure')
        finally:
            self.concurrent -= 1


class AudioTests(unittest.TestCase):
    def setUp(self):
        self.backend = Backend()
        self.now = 100.
        self.audio = AudioDispatcher(self.backend, clock=lambda: self.now)
        def cleanup():
            self.audio.close()
            self.backend.release.set()
            if self.audio._thread:
                self.audio._thread.join(2)
                self.assertFalse(self.audio._thread.is_alive())
        self.addCleanup(cleanup)

    def offer(self, *items, reader=None, guard=lambda: True):
        self.audio.observe_batch([(a, guard, reader or (lambda a=a: a.active_suspension))
                                  for a in items], scope=(1,1,'camera'))

    def drain(self):
        with self.audio._cv:
            self.assertTrue(self.audio._cv.wait_for(
                lambda: not self.audio._pending and self.audio._active is None, timeout=2))

    def block(self):
        self.backend.block = True
        self.audio.preview(K.GENERIC)
        self.assertTrue(self.backend.started.wait(1))

    def test_three_mappings_and_priority(self):
        good = assessment()
        self.assertIs(good.uniform_assessment, U.CORRECT)
        wrong = replace(good, accepted_categories=('wrong_uniform',), uniform_assessment=U.WRONG)
        for a, suspended, expected in ((good,False,K.POSITIVE), (wrong,False,K.VIOLATION),
                (good,True,K.SUSPENSION), (wrong,True,K.SUSPENSION),
                (replace(good, accepted_categories=('earring',)),False,K.VIOLATION)):
            self.assertEqual(select_sound(a,suspended), expected)
        for a in (replace(good,reliable_identity=False), replace(good,student_id='')):
            self.assertIsNone(select_sound(a,True))

    def test_display_text_and_no_violation_are_not_positive(self):
        for state in ('Uniform compliant', 'No violation recorded', 'Checking uniform',
                      'Uniform not assessed', 'Unknown person'):
            a = assessment(state=state, uniform_assessment=U.UNASSESSED)
            self.assertIsNone(select_sound(a,False))
        self.assertIsNone(select_sound(assessment(accepted_categories=('unsupported',)),False))

    def test_pipeline_confirmation_and_disabled_unavailable_obscured_gates(self):
        fx = PipelineFixture()
        fx.trainer.predict_proba.return_value = {'correct_uniform': .95}
        for seq in range(1,4):
            self.assertIs(fx.analyze(seq).assessments[0].uniform_assessment, U.UNASSESSED)
        self.assertIs(fx.analyze(4).assessments[0].uniform_assessment, U.CORRECT)
        self.assertIs(fx.analyze(5,uniform_enabled=False).assessments[0].uniform_assessment,U.UNASSESSED)
        fx.trainer.is_trained.return_value = False
        fx.trainer.is_trained.side_effect = None
        self.assertIs(fx.analyze(6).assessments[0].uniform_assessment,U.UNASSESSED)
        fx.detector.detect_persons.return_value = []
        self.assertIs(fx.analyze(7).assessments[0].uniform_assessment,U.UNASSESSED)

    def test_each_mapping_plays_local_asset(self):
        for a, expected in ((assessment(), 'positive'),
            (assessment(accepted_categories=('earring',)), 'violation'),
            (assessment(active_suspension=True), 'suspension')):
            self.audio.cancel_detection()
            self.offer(a)
            self.drain()
            self.assertEqual(self.backend.calls[-1], expected)

    def test_duplicate_frames_and_held_state_never_repeat(self):
        a = assessment()
        self.offer(a); self.drain()
        self.now += 100
        for _ in range(100): self.offer(a)
        self.drain()
        self.assertEqual(self.backend.calls,['positive'])

    def test_higher_priority_bypasses_separate_person_cooldown(self):
        a = assessment()
        self.offer(a); self.drain()
        self.offer(replace(a,accepted_categories=('wrong_uniform',))); self.drain()
        self.offer(replace(a,active_suspension=True)); self.drain()
        self.offer(a); self.drain()
        self.assertEqual(self.backend.calls,['positive','violation','suspension'])

    def test_same_person_new_track_cannot_bypass_cooldown(self):
        self.offer(assessment()); self.drain()
        self.offer(assessment(presence_id='new')); self.drain()
        self.assertEqual(self.backend.calls,['positive'])
        self.offer()
        self.now += 16
        self.offer(assessment(presence_id='third')); self.drain()
        self.assertEqual(self.backend.calls,['positive','positive'])

    def test_crowd_bounded_coalesced_and_urgent_first(self):
        self.block()
        crowd = [assessment(student_id=f'S{i}',presence_id=f'p{i}') for i in range(80)]
        crowd.append(assessment(student_id='urgent',presence_id='urgent',active_suspension=True))
        self.offer(*crowd)
        self.offer(*crowd)
        self.assertLessEqual(len(self.audio._pending), self.audio.MAX_PENDING)
        self.backend.release.set(); self.drain()
        self.assertEqual(self.backend.calls[1],'suspension')
        self.assertEqual(self.backend.maximum,1)
        self.assertLessEqual(len(self.backend.calls),25)

    def test_pending_positive_replaced_by_suspension_only(self):
        self.block()
        self.offer(assessment())
        self.offer(assessment(active_suspension=True))
        self.backend.release.set(); self.drain()
        self.assertEqual(self.backend.calls,['generic','suspension'])

    def test_queued_positive_expires(self):
        self.block(); self.offer(assessment())
        self.now += 1.6
        self.backend.release.set(); self.drain()
        self.assertEqual(self.backend.calls,['generic'])

    def test_absent_ambiguous_or_unassessed_invalidates_pending(self):
        for replacement in ((), (assessment(reliable_identity=False),),
                            (assessment(uniform_assessment=U.UNASSESSED),)):
            self.audio.cancel_detection()
            self.backend.release.clear(); self.backend.started.clear()
            self.block(); self.offer(assessment()); self.offer(*replacement)
            self.backend.release.set(); self.drain()
        self.assertNotIn('positive', self.backend.calls)

    def test_authoritative_suspension_rechecked_before_positive(self):
        self.block()
        reader = Mock(return_value={'id':1})
        self.offer(assessment(),reader=reader)
        self.backend.release.set(); self.drain()
        reader.assert_called_once()
        self.assertNotIn('positive',self.backend.calls)
        self.offer(assessment(active_suspension=True),reader=reader); self.drain()
        self.assertEqual(self.backend.calls[-1],'suspension')

    def test_lifted_suspension_cannot_play_queued_alert(self):
        self.offer(assessment(active_suspension=True),reader=lambda:None); self.drain()
        self.assertFalse(self.backend.calls)

    def test_suspension_lookup_failure_fails_closed(self):
        with self.assertLogs('cbvms.audio',level='WARNING'):
            self.offer(assessment(),reader=Mock(side_effect=RuntimeError('database locked')))
            self.drain()
        self.assertFalse(self.backend.calls)
        self.assertIn('database locked',self.audio.last_error)

    def test_cancellation_and_camera_generation_discard_pending(self):
        self.block(); self.offer(assessment()); self.audio.cancel_detection()
        self.backend.release.set(); self.drain()
        self.assertEqual(self.backend.calls,['generic'])
        self.offer(assessment(),guard=lambda:False); self.drain()
        self.assertEqual(self.backend.calls,['generic'])

    def test_master_mute_and_individual_settings_discard_without_replay(self):
        for mute in (lambda:setattr(self.audio,'enabled',False),
                     lambda:self.audio.set_kind_enabled(K.POSITIVE,False)):
            self.audio.cancel_detection(); self.audio.enabled=True
            self.audio.set_kind_enabled(K.POSITIVE,True)
            self.backend.release.clear(); self.backend.started.clear()
            self.block(); self.offer(assessment()); mute()
            self.audio.enabled=True; self.audio.set_kind_enabled(K.POSITIVE,True)
            self.backend.release.set(); self.drain()
        self.assertNotIn('positive',self.backend.calls)

    def test_preview_only_respects_switches_and_does_not_change_history(self):
        from ui.settings import SettingsPanel
        notifier = Notifier(audio=self.audio)
        panel = Mock(notifier=notifier)
        for kind in (K.POSITIVE,K.VIOLATION,K.SUSPENSION):
            SettingsPanel._preview_sound(panel,kind); self.drain()
        self.assertEqual(self.backend.calls,['positive','violation','suspension'])
        self.assertEqual(notifier.get_log(),[])
        self.assertEqual(len(self.audio._states),0)
        self.assertEqual(len(self.audio._cooldowns),0)
        notifier.sound_enabled=False
        SettingsPanel._preview_sound(panel,K.POSITIVE); self.drain()
        self.assertEqual(len(self.backend.calls),3)

    def test_playback_failure_preserves_visual_history_and_worker_recovers(self):
        self.backend.failure=True
        notifier = Notifier(audio=self.audio)
        listener=Mock(); notifier.subscribe(listener)
        with self.assertLogs('cbvms.audio',level='WARNING'):
            notifier.notify('Student','Manual notification'); self.drain()
        self.assertEqual(len(notifier.get_log()),1)
        listener.assert_called_once()
        self.backend.failure=False
        self.audio.preview(K.POSITIVE); self.drain()
        self.assertEqual(self.backend.calls[-1],'positive')

    def test_live_notifications_suppress_generic_only(self):
        notifier = Notifier(audio=self.audio)
        listener=Mock(); notifier.subscribe(listener)
        notifier.notify('Student','Violation',play_sound=False)
        self.drain(); self.assertEqual(self.backend.calls,[])
        self.assertEqual(notifier.unread_count(),1); listener.assert_called_once()
        notifier.notify('Unknown','Unknown sighting'); self.drain()
        self.assertEqual(self.backend.calls,['generic'])

    def test_shutdown_is_nonblocking_and_cancels_active_and_pending(self):
        self.block(); self.offer(assessment())
        current=self.audio._active
        started=time.perf_counter(); self.audio.close()
        self.assertLess(time.perf_counter()-started,.1)
        self.assertFalse(self.audio._valid(current))
        self.assertFalse(self.audio.preview(K.SUSPENSION))
        self.backend.release.set()
        self.audio._thread.join(1)
        self.assertFalse(self.audio._thread.is_alive())
        self.assertEqual(self.backend.calls,['generic'])

    def test_motion_relevance_rechecked_after_slow_suspension_lookup(self):
        relevant=Mock(return_value=False)
        self.audio.observe_batch([(assessment(), lambda:True, lambda:None, relevant)],scope=1)
        self.drain(); relevant.assert_called_once()
        self.assertFalse(self.backend.calls)

    def test_missing_asset_is_diagnostic_and_nonfatal(self):
        self.audio.backend=LocalAudioBackend()
        with tempfile.TemporaryDirectory() as folder, patch.dict(ASSETS,{K.POSITIVE:Path(folder)/'missing.wav'}):
            with self.assertLogs('cbvms.audio',level='WARNING'):
                self.audio.preview(K.POSITIVE); self.drain()
        self.assertIn('missing.wav',self.audio.last_error)

    def test_assets_brief_soft_and_controlled(self):
        for path in ASSETS.values():
            with wave.open(str(path),'rb') as wav:
                self.assertEqual((wav.getnchannels(),wav.getsampwidth()),(1,2))
                data=np.frombuffer(wav.readframes(wav.getnframes()),dtype='<i2').astype(float)/32768
                self.assertLess(len(data)/wav.getframerate(),.75)
            self.assertLessEqual(abs(data).max(),10**(-15/20)+.0001)
            self.assertEqual(data[0],0); self.assertEqual(data[-1],0)
            self.assertLess(abs(np.diff(data)).max(),.025)

    def test_platform_missing_player_and_failure(self):
        backend=LocalAudioBackend()
        with patch('core.detection_audio.sys.platform','linux'),patch('core.detection_audio.shutil.which',return_value=None):
            with self.assertRaisesRegex(RuntimeError,'paplay or aplay'):
                backend.play(ASSETS[K.POSITIVE],lambda:True)
        process=Mock(returncode=9); process.poll.return_value=9
        with patch('core.detection_audio.sys.platform','darwin'),patch('core.detection_audio.shutil.which',return_value='/usr/bin/afplay'),patch('core.detection_audio.subprocess.Popen',return_value=process):
            with self.assertRaisesRegex(RuntimeError,'exited 9'):
                backend.play(ASSETS[K.POSITIVE],lambda:True)

    def test_urgent_arrival_during_slow_read_gets_next_playback(self):
        reading=threading.Event(); release=threading.Event()
        def reader():
            reading.set(); release.wait(1); return None
        good=assessment()
        self.offer(good,reader=reader)
        self.assertTrue(reading.wait(1))
        urgent=assessment(student_id='urgent',presence_id='urgent',active_suspension=True)
        self.audio.observe_batch([(good,lambda:True,lambda:None),
                                 (urgent,lambda:True,lambda:True)],scope=(1,1,'camera'))
        release.set(); self.drain()
        self.assertEqual(self.backend.calls,['suspension','positive'])

    def test_mute_during_suspension_read_is_rechecked(self):
        def reader():
            self.audio.enabled=False
            return None
        self.offer(assessment(),reader=reader); self.drain()
        self.assertFalse(self.backend.calls)

    def test_live_audio_consumes_confirmed_result_without_persistence_effects(self):
        fx=PipelineFixture()
        result=fx.confirmed()
        observation=fx.notifier.audio.observe_batch.call_args.args[0][0][0]
        self.assertIs(observation,result.assessments[0])
        self.assertEqual(observation.accepted_categories,('wrong_uniform',))
        fx.database.log_violation.assert_not_called()
        fx.database.record_attendance.assert_not_called()
        self.assertFalse(fx.processor.cooldowns)
        fx.persist(result)
        self.assertTrue(fx.notifier.notify.called)
        self.assertTrue(all(call.kwargs['play_sound'] is False for call in fx.notifier.notify.call_args_list))

    def test_windows_backend_uses_file_no_default_and_stops_on_cancel(self):
        backend=LocalAudioBackend()
        winsound=Mock(SND_FILENAME=1,SND_ASYNC=2,SND_NODEFAULT=4)
        valid=Mock(side_effect=[True,False])
        with patch('core.detection_audio.sys.platform','win32'),patch.dict('sys.modules',{'winsound':winsound}):
            backend.play(ASSETS[K.POSITIVE],valid)
        self.assertEqual(winsound.PlaySound.call_args_list[0].args,(str(ASSETS[K.POSITIVE]),7))
        self.assertEqual(winsound.PlaySound.call_args_list[-1].args,(None,0))

    def test_linux_fallback_terminates_active_process_on_cancel(self):
        backend=LocalAudioBackend()
        process=Mock(); process.poll.return_value=None
        with patch('core.detection_audio.sys.platform','linux'), \
             patch('core.detection_audio.shutil.which',side_effect=lambda name:'/usr/bin/aplay' if name=='aplay' else None), \
             patch('core.detection_audio.subprocess.Popen',return_value=process) as spawn:
            backend.play(ASSETS[K.POSITIVE],Mock(side_effect=[True,True,False]))
        self.assertEqual(spawn.call_args.args[0],['/usr/bin/aplay',str(ASSETS[K.POSITIVE])])
        process.terminate.assert_called_once()
        process.wait.assert_called_once()


class AuthoritativeSuspensionTests(unittest.TestCase):
    def test_current_future_expired_and_lifted_use_database_state(self):
        from datetime import timedelta
        from core.discipline import utc_now
        from database.db_manager import CBVMSDatabase
        with tempfile.TemporaryDirectory() as folder:
            db=CBVMSDatabase(Path(folder)/'test.db')
            db.initialize(process_deadlines=False)
            now=utc_now()
            for sid, start, end, lifted, expected in (
                ('active',now-timedelta(days=1),now+timedelta(days=1),False,K.SUSPENSION),
                ('future',now+timedelta(days=1),None,False,K.POSITIVE),
                ('expired',now-timedelta(days=2),now-timedelta(days=1),False,K.POSITIVE),
                ('lifted',now-timedelta(days=1),None,True,K.POSITIVE)):
                db.insert_student(sid,sid,'BSIT','3A',b'encoding',b'photo')
                suspension=db.impose_suspension(sid,reason='Test fixture',starts_at=start,
                                               ends_at=end,imposed_by='test')
                if lifted:
                    db.lift_suspension(suspension,lifted_by='test',reason='Fixture lifted')
                a=assessment(student_id=sid)
                backend=Backend(); audio=AudioDispatcher(backend)
                try:
                    current=db.get_active_suspension(sid)
                    audio.observe_batch([(replace(a,active_suspension=bool(current)),
                        lambda:True,lambda sid=sid:db.get_active_suspension(sid))],scope=1)
                    with audio._cv:
                        self.assertTrue(audio._cv.wait_for(lambda:not audio._pending and audio._active is None,timeout=1))
                    self.assertEqual(backend.calls,[expected.name.lower()])
                    self.assertEqual(db.get_strike_count(sid,'wrong_uniform'),0)
                    with db.connect() as conn:
                        self.assertEqual(conn.execute('SELECT COUNT(*) FROM attendance').fetchone()[0],0)
                        self.assertEqual(conn.execute('SELECT COUNT(*) FROM violations').fetchone()[0],0)
                finally:
                    audio.close()
                    if audio._thread: audio._thread.join(1)
