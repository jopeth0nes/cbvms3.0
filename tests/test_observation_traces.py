"""Trace bounds and isolation, without models, biometric data or camera access."""
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import unittest
from core.performance import ObservationTraces


def task(index):
    return SimpleNamespace(source_id='camera', camera_generation=1,
        context=SimpleNamespace(generation=2, frame_id=index, captured_at=10.),
        observations=(SimpleNamespace(presence_id='local-passage'),),
        candidate_selected_at=11.)


class ObservationTraceTests(unittest.TestCase):
    def test_bounded_and_no_raw_identifiers(self):
        traces = ObservationTraces(True, capacity=2)
        for index in range(3):
            with traces.observation(task(index), 'assessment', 11.):
                traces.span('face_embedding', 12., 13.)
                traces.result(None)
        report = traces.snapshot()
        self.assertEqual(len(report['rows']), 2)
        self.assertEqual(report['dropped'], 1)
        self.assertNotIn('local-passage', str(report))
        self.assertFalse(report['rows'][0]['completed'])
        self.assertEqual(report['rows'][0]['spans'][0]['stage'], 'face_embedding')

    def test_thread_scopes_and_exception_cleanup(self):
        traces = ObservationTraces(True, capacity=20)
        def record(index):
            with traces.observation(task(index), str(index), 11.):
                traces.span(str(index), 12., 13.)
        with ThreadPoolExecutor(2) as pool:
            list(pool.map(record, range(10)))
        for row in traces.snapshot()['rows']:
            self.assertEqual(row['kind'], row['spans'][0]['stage'])
        with self.assertRaises(ValueError):
            with traces.observation(task(20), 'failure', None):
                raise ValueError('fixture')
        traces.span('outside', 1., 2.)
        self.assertEqual(traces.snapshot()['rows'][-1]['spans'], [])

    def test_disabled_does_not_retain(self):
        traces = ObservationTraces(False)
        with traces.observation(task(1), 'assessment', None):
            traces.span('anything', 1., 2.)
        self.assertEqual(traces.snapshot()['rows'], [])
