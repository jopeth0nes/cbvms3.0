import ctypes, faulthandler, json, os, signal, subprocess, sys, time
from pathlib import Path
root = Path.cwd()  # Run from the CBVMS repository root.
folder = Path(__file__).parent
stacks = (folder/'stacks.txt').open('w')
faulthandler.register(signal.SIGUSR1, file=stacks, all_threads=True)
native_hold = ctypes.PyDLL(str(folder/'hold.dylib')).hold_gil
(folder/'heartbeat').write_text(str(time.monotonic()))
watchdog = subprocess.Popen([sys.executable, '-c', 'import sys; from pathlib import Path; from scripts.camera_freeze_watchdog import supervise; supervise(int(sys.argv[1]), Path(sys.argv[2]), threshold=.3, lifetime=10)', str(os.getpid()), str(folder)], cwd=root)
start = time.monotonic()
native_hold()
(folder/'finished').touch()
watchdog.wait(timeout=10)
print(json.dumps({'native_hold_seconds':time.monotonic()-start,'watchdog_exit':watchdog.returncode,'python_stack':(folder/'stacks.txt').stat().st_size>0}))
