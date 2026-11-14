"""Native process exclusion and Windows adapter contract checks."""
import errno
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core import file_lock


class UniformFileLockTests(unittest.TestCase):
    def test_catalog_and_processing_exceptions_release_process_locks(self):
        from core.uniform_dataset import UniformDataset
        code = """
import sys
from core.file_lock import lock_exclusive
with open(sys.argv[1], 'a+') as handle:
    lock_exclusive(handle, blocking=False)
"""
        with tempfile.TemporaryDirectory() as temporary:
            dataset = UniformDataset(Path(temporary) / 'dataset', auto_process=False)
            with self.assertRaisesRegex(RuntimeError, 'fixture'):
                with dataset._locked():
                    raise RuntimeError('fixture')
            result = subprocess.run([sys.executable, '-c', code, str(dataset.lock_path)], timeout=5)
            self.assertEqual(result.returncode, 0)
            with patch.object(dataset, '_load', side_effect=RuntimeError('fixture')):
                with self.assertRaisesRegex(RuntimeError, 'fixture'):
                    dataset.process_pending()
            for path in (dataset.lock_path, dataset.root / '.processing.lock'):
                result = subprocess.run([sys.executable, '-c', code, str(path)], timeout=5)
                self.assertEqual(result.returncode, 0)

    def test_native_lock_excludes_other_process_and_releases(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'lock'
            code = """
import sys
from core.file_lock import lock_exclusive
with open(sys.argv[1], 'a+') as handle:
    try:
        lock_exclusive(handle, blocking=False)
    except BlockingIOError:
        sys.exit(2)
"""
            with path.open('a+') as handle:
                file_lock.lock_exclusive(handle)
                result = subprocess.run([sys.executable, '-c', code, str(path)], timeout=5)
                self.assertEqual(result.returncode, 2)
                file_lock.unlock(handle)
                result = subprocess.run([sys.executable, '-c', code, str(path)], timeout=5)
                self.assertEqual(result.returncode, 0)

    def test_windows_lock_busy_retry_and_unlock_use_first_byte(self):
        windows = Mock(LK_NBLCK=1, LK_UNLCK=0)
        windows.locking.side_effect = [OSError(errno.EACCES, 'busy'), None, None]
        with tempfile.TemporaryFile('w+') as handle, patch.object(file_lock, 'WINDOWS', True), \
                patch.dict(sys.modules, msvcrt=windows), patch.object(file_lock.time, 'sleep') as sleep:
            file_lock.lock_exclusive(handle)
            sleep.assert_called_once_with(.05)
            handle.seek(0)
            self.assertEqual(handle.read(), '0')
            file_lock.unlock(handle)
            self.assertEqual(handle.tell(), 0)
            windows.locking.assert_called_with(handle.fileno(), 0, 1)

    def test_windows_nonblocking_and_unexpected_errors_are_not_swallowed(self):
        windows = Mock(LK_NBLCK=1)
        with tempfile.TemporaryFile('w+') as handle, patch.object(file_lock, 'WINDOWS', True), \
                patch.dict(sys.modules, msvcrt=windows):
            windows.locking.side_effect = OSError(errno.EACCES, 'busy')
            with self.assertRaises(BlockingIOError):
                file_lock.lock_exclusive(handle, blocking=False)
            windows.locking.side_effect = OSError(errno.EBADF, 'bad file')
            with self.assertRaises(OSError) as raised:
                file_lock.lock_exclusive(handle)
            self.assertEqual(raised.exception.errno, errno.EBADF)
