"""Diagnostic tests only; these do not reproduce native dropdown interaction."""
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from scripts.camera_freeze_watchdog import sanitized_sample, supervise


class FreezeWatchdogTests(unittest.TestCase):
    def test_sample_retains_stacks_without_process_metadata(self):
        raw = ('Process: secret-process\nArguments: rtsp://user:secret@host\n'
               'Call graph:\n    10 Tcl_DoOneEvent\n    10 AVFoundation\n'
               'Total number in stack (recursive counted multiple):\n'
               'Binary Images:\nprivate paths\n')
        result = sanitized_sample(raw)
        self.assertIn('Tcl_DoOneEvent', result)
        self.assertIn('AVFoundation', result)
        self.assertNotIn('secret', result)
        self.assertNotIn('private paths', result)
        self.assertEqual(sanitized_sample('permission denied'), 'No call graph returned.\n')

    def test_stale_heartbeat_signals_parent_and_collects_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder/'heartbeat').write_text('1')
            def stop(_):
                (folder/'finished').touch()
            with patch('scripts.camera_freeze_watchdog.time.monotonic', return_value=30), \
                 patch('scripts.camera_freeze_watchdog.time.sleep', side_effect=stop), \
                 patch('scripts.camera_freeze_watchdog.os.kill') as kill, \
                 patch('scripts.camera_freeze_watchdog.sys.platform', 'darwin'), \
                 patch('scripts.camera_freeze_watchdog.subprocess.run', return_value=types.SimpleNamespace(
                     stdout='Call graph:\n    5 Tcl_DoOneEvent\nBinary Images:\n', returncode=0)) as sample:
                supervise(123, folder)
            self.assertEqual(kill.call_count, 2)
            sample.assert_called_once()
            rows = [json.loads(s) for s in (folder/'watchdog.jsonl').read_text().splitlines()]
            self.assertEqual(rows[0]['event'], 'heartbeat_stalled')
            self.assertEqual(rows[0]['gap_seconds'], 29)
            self.assertIn('Tcl_DoOneEvent', (folder/'native-sample-1.txt').read_text())
