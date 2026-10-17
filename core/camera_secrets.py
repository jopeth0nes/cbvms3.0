"""Read existing macOS camera credentials off Tk, with cancellation and a deadline.

The earlier camera workflow saved complete stream URLs under this Keychain
service. Never put those values in process arguments, logs, or preferences.
"""
import json
import subprocess
import sys
import time

_READ = """
import json, sys
try:
    from keyring.backends.macOS import Keyring
    reference = json.load(sys.stdin)
    value = Keyring().get_password('CBVMS IP Cameras', reference)
    if not value: raise RuntimeError()
    json.dump(value, sys.stdout)
except Exception:
    sys.exit(1)
"""


def get_secret(reference, *, cancel_event=None):
    if sys.platform != 'darwin':
        raise RuntimeError('This saved camera requires its macOS Keychain credentials.')
    process = subprocess.Popen([sys.executable, '-c', _READ], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 8
    try:
        incoming = json.dumps(reference).encode()
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError('Camera credential access cancelled.')
            if time.monotonic() >= deadline:
                raise RuntimeError('Keychain access timed out. Unlock Keychain and retry.')
            try:
                output, _ = process.communicate(input=incoming, timeout=.05)
                break
            except subprocess.TimeoutExpired:
                incoming = None
        if process.returncode:
            raise RuntimeError('Camera credentials unavailable. Unlock Keychain and check camera requirements.')
        return json.loads(output)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=1)
        for stream in (process.stdin, process.stdout):
            stream.close()
