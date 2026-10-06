"""Isolated one-shot analyzer process. It receives image bytes only on stdin."""
import base64
import contextlib
import io
import json
import os
import signal
import sys

def _timeout_handler(_signum, _frame):
    raise TimeoutError("analysis_timeout")

def _limits():
    """Apply bounds in the child before importing Pillow or optional ML libraries."""
    if os.name != "nt":
        try:
            import resource
        except Exception:
            resource = None
        if resource is not None:
            for limit, values in (
                (resource.RLIMIT_CPU, (100, 101)),
                (resource.RLIMIT_AS, (4 * 1024**3, 4 * 1024**3)),
                (resource.RLIMIT_NOFILE, (64, 64)),
                (resource.RLIMIT_FSIZE, (2 * 1024 * 1024, 2 * 1024 * 1024)),
            ):
                try:
                    resource.setrlimit(limit, values)
                except Exception:
                    pass
    try:
        os.nice(10)
    except (AttributeError, OSError):
        pass
    timeout = 120 if (os.environ.get("CBVMS_FORENSICS_ENABLE_EXPERIMENTAL") == "1"
                      and os.environ.get("CBVMS_FORENSICS_LICENSE_ACCEPTED") == "1") else 45
    if hasattr(signal, "SIGALRM"):
        signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(timeout)

def main():
    _limits()
    from core.evidence_forensics import analyze_evidence
    raw = sys.stdin.buffer.read(14 * 1024 * 1024 + 1)
    if len(raw) > 14 * 1024 * 1024:
        raise ValueError("input_too_large")
    request = json.loads(raw.decode("utf-8"))
    data = base64.b64decode(request["data"], validate=True)
    try:
        with open(os.devnull, 'w') as quiet, contextlib.redirect_stdout(quiet):
            report = analyze_evidence(data, request.get("filename", "evidence"))
    except TimeoutError:
        sys.stderr.write("analysis_timeout")
        raise SystemExit(3)
    for key in ("localization_png", "reliability_png"):
        artifact = report.get(key)
        report[key] = base64.b64encode(artifact).decode("ascii") if artifact else None
    sys.stdout.write(json.dumps(report, separators=(",", ":"), allow_nan=False))

if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Do not put filenames or image metadata in stderr/logs.
        sys.stderr.write("analysis_failed")
        raise SystemExit(2)
