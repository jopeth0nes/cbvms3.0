# Independent GIL-held stall self-test

This is a deliberately stalled disposable process, not a CBVMS freeze. The final
probe preloads the library, then keeps the GIL during a native nanosleep loop.
SIGUSR1 records the Python callsite; macOS sample shows hold_gil/nanosleep.
The watchdog detected a 0.550-second gap and sample returned 0. No camera or DB.

Original successful command (from repository root, desktop execution):
`.venv/bin/python /private/tmp/cbvms-gil-watchdog-20261024/probe.py`

For a fresh reproduction, copy hold.c and probe.py to a NEW temporary directory,
compile `cc -dynamiclib <directory>/hold.c -o <directory>/hold.dylib`, then run
`.venv/bin/python <directory>/probe.py` from the repository root. Do not reuse
old heartbeat/finished files. Captured stacks and call graph omit process metadata.
