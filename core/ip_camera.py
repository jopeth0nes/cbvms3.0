"""Smart IP-camera connection: auto-detect the stream URL from IP + credentials.

Most IP cameras expose RTSP (port 554) or MJPEG-over-HTTP, but the exact path is
brand-specific (Hikvision `/Streaming/Channels/101`, Dahua `/cam/realmonitor…`,
Reolink `/h264Preview_01_main`, …). Users rarely know it. Given just host + user +
password this module:

  1. checks which ports are actually open (so we never probe a dead protocol),
  2. builds credential-embedded candidate URLs for the open ports, and
  3. opens them concurrently with hard timeouts, returning the first that yields a
     frame.

It also exposes `open_stream()` — a timeout-hardened VideoCapture used everywhere we
open a network stream, so an unreachable camera fails in seconds instead of hanging.
"""

from __future__ import annotations

import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote, urlparse

import cv2

# RTSP stream paths, ordered by how common they are across brands (main stream first).
_RTSP_PATHS = [
    "Streaming/Channels/101",           # Hikvision / Annke / LaView (main)
    "cam/realmonitor?channel=1&subtype=0",  # Dahua / Amcrest / Lorex (main)
    "h264Preview_01_main",              # Reolink (main)
    "stream1",                          # TP-Link Tapo / generic
    "live",                             # generic
    "h264/ch1/main/av_stream",          # older Hikvision
    "live/ch00_0",                      # generic ONVIF
    "11",                               # generic ONVIF (main)
    "onvif1",                           # Sricam / Wansview
    "videoMain",                        # Foscam
    "axis-media/media.amp",             # Axis
    "media/video1",                     # generic
    "Streaming/Channels/102",           # Hikvision (sub)
    "cam/realmonitor?channel=1&subtype=1",  # Dahua (sub)
    "h264Preview_01_sub",               # Reolink (sub)
    "stream2",                          # generic (sub)
]

# MJPEG / HTTP stream paths.
_HTTP_PATHS = [
    "video",                  # Android "IP Webcam" and many others
    "videostream.cgi",        # Foscam MJPEG
    "axis-cgi/mjpg/video.cgi",  # Axis
    "mjpg/video.mjpg",        # Axis / generic
    "video.cgi",
    "?action=stream",         # mjpg-streamer
]

_RTSP_PORTS = [554, 8554]
_HTTP_PORTS = [80, 8080, 81, 88]


def _split_host_port(raw: str) -> tuple[str, int | None, str | None]:
    """Parse a user-entered address → (host, port, scheme). Tolerant of bare IPs/URLs."""
    raw = (raw or "").strip()
    scheme = None
    if "://" in raw:
        parsed = urlparse(raw)
        scheme = parsed.scheme or None
        netloc = parsed.netloc or parsed.path
    else:
        netloc = raw
    if "@" in netloc:                       # strip any creds the user pasted in
        netloc = netloc.split("@", 1)[1]
    netloc = netloc.split("/", 1)[0]        # drop any trailing path
    host, port = netloc, None
    if ":" in netloc and not netloc.startswith("["):
        h, _, p = netloc.rpartition(":")
        if p.isdigit():
            host, port = h, int(p)
    return host, port, scheme


def _creds(username: str, password: str) -> str:
    if not username:
        return ""
    return f"{quote(username, safe='')}:{quote(password or '', safe='')}@"


def _normalize_override(path: str | None) -> str | None:
    """Return a meaningful stream override, or None to trigger auto-detection.

    Users often paste the camera's *web page* address (e.g. "http://192.168.1.108")
    into the URL field — that's not a video stream. We only honor:
      • any rtsp:// URL,
      • an http(s):// URL that has a real path after the host (e.g. .../video), and
      • a bare path fragment ("/cam/realmonitor…" or "stream1").
    A bare web root like "http://host" or "http://host/" → None (auto-detect instead).
    """
    if not path:
        return None
    p = path.strip()
    if not p:
        return None
    low = p.lower()
    if low.startswith("rtsp://"):
        return p
    if low.startswith(("http://", "https://")):
        rest = p.split("://", 1)[1]
        host_path = rest.split("/", 1)
        if len(host_path) < 2 or not host_path[1].strip("/"):
            return None  # bare web root → not a stream, auto-detect
        return p
    return p  # bare path fragment ("/stream1", "cam/realmonitor?…")


def build_candidate_urls(
    address: str,
    username: str = "",
    password: str = "",
    *,
    path: str | None = None,
) -> list[str]:
    """Build ordered candidate stream URLs for a host + credentials.

    If `path` is a meaningful override (an rtsp:// URL, an http URL with a real stream
    path, or a bare "/stream1" path) it pins the result. A bare web URL like
    "http://192.168.1.108" (the camera's web page, not a stream) is ignored so we fall
    back to full auto-detection.
    """
    host, port, scheme = _split_host_port(address)
    if not host:
        return []
    creds = _creds(username, password)
    path = _normalize_override(path)

    # Explicit full URL provided → trust the user (inject creds if absent).
    if path and "://" in path:
        if "@" not in path and creds:
            head, sep, tail = path.partition("://")
            return [f"{head}://{creds}{tail}"]
        return [path.strip()]

    explicit_paths = [path.lstrip("/")] if path else None

    urls: list[str] = []
    rtsp_port = port if (scheme == "rtsp" and port) else 554
    for p in (explicit_paths or _RTSP_PATHS):
        urls.append(f"rtsp://{creds}{host}:{rtsp_port}/{p}")

    http_port = port if (scheme in ("http", "https") and port) else 80
    for p in (explicit_paths or _HTTP_PATHS):
        urls.append(f"http://{creds}{host}:{http_port}/{p}")
    return urls


def _port_open(host: str, port: int, timeout: float = 0.8) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _ports_open(host: str) -> tuple[bool, bool]:
    """Check all RTSP + HTTP ports in parallel. Returns (rtsp_open, http_open)."""
    checks = [(p, "rtsp") for p in _RTSP_PORTS] + [(p, "http") for p in _HTTP_PORTS]
    rtsp_ok = http_ok = False
    with ThreadPoolExecutor(max_workers=len(checks)) as ex:
        futs = {ex.submit(_port_open, host, p): kind for p, kind in checks}
        for fut in as_completed(futs):
            try:
                if fut.result():
                    if futs[fut] == "rtsp":
                        rtsp_ok = True
                    else:
                        http_ok = True
            except Exception:
                pass
    return rtsp_ok, http_ok


def open_stream(
    url: str,
    *,
    open_timeout_ms: int = 6000,
    read_timeout_ms: int = 6000,
    transport: str = "tcp",
) -> cv2.VideoCapture | None:
    """Open a network stream via FFMPEG with TCP transport + hard timeouts.

    Without these, OpenCV can block for 30s+ on an unreachable RTSP host. The FFMPEG
    options must be set in the environment before the capture is created.
    """
    # nobuffer + low_delay tell FFMPEG not to pre-buffer packets, so frames reach the
    # reader as soon as they decode (lower live latency). The reader thread then keeps
    # the stream drained so no backlog builds up.
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
        f"rtsp_transport;{transport}|stimeout;{read_timeout_ms * 1000}"
        f"|fflags;nobuffer|flags;low_delay"
    )
    params: list[int] = []
    if hasattr(cv2, "CAP_PROP_OPEN_TIMEOUT_MSEC"):
        params += [int(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC), int(open_timeout_ms)]
    if hasattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC"):
        params += [int(cv2.CAP_PROP_READ_TIMEOUT_MSEC), int(read_timeout_ms)]
    try:
        if params:
            cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG, params)
        else:
            cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    except Exception:
        return None
    if cap is None or not cap.isOpened():
        if cap is not None:
            cap.release()
        return None
    return cap


def _grab_one(cap: cv2.VideoCapture, budget_s: float = 3.0) -> bool:
    deadline = time.time() + budget_s
    while time.time() < deadline:
        ok, frame = cap.read()
        if ok and frame is not None and getattr(frame, "size", 0) > 0:
            return True
        time.sleep(0.05)
    return False


def _try_one(url: str, open_ms: int) -> bool:
    cap = open_stream(url, open_timeout_ms=open_ms, read_timeout_ms=open_ms)
    if cap is None:
        return False
    try:
        # Real cameras (esp. an RTSP main stream) need a moment to deliver a keyframe.
        return _grab_one(cap, budget_s=3.0)
    finally:
        cap.release()


def probe_camera(
    address: str,
    username: str = "",
    password: str = "",
    *,
    path: str | None = None,
    per_url_open_ms: int = 4000,
    on_progress=None,
) -> str | None:
    """Auto-detect a working stream URL for host+credentials. Returns it or None.

    Open ports are checked first (in parallel) so dead protocols are skipped, then the
    matching candidate URLs are all opened concurrently and the FIRST one to deliver a
    frame wins — so a reachable camera is found in ~1-2s instead of waiting through
    earlier candidates' connect timeouts.
    """
    host, _port, _scheme = _split_host_port(address)
    if not host:
        return None

    # A bare web URL ("http://host") is not a stream → treat as no override (auto-detect).
    path = _normalize_override(path)
    candidates = build_candidate_urls(address, username, password, path=path)
    if not candidates:
        return None

    # With no pinned override, port-prune (skip dead protocols) before probing.
    if path is None:
        rtsp_ok, http_ok = _ports_open(host)   # parallel port scan (~0.8s)
        if not (rtsp_ok or http_ok):
            if on_progress:
                on_progress("No camera ports (RTSP 554 / HTTP 80) open at that address.")
            return None
        candidates = [
            u for u in candidates
            if (u.startswith("rtsp://") and rtsp_ok) or (u.startswith("http") and http_ok)
        ]

    if on_progress:
        on_progress(f"Probing {len(candidates)} stream URL(s)…")

    # Probe ALL candidates at once and return the FIRST that delivers a frame — don't
    # wait through earlier candidates' timeouts. shutdown(wait=False) lets us return the
    # instant a stream connects (~1-2s on a LAN); the losing probes finish in the
    # background, bounded by their own timeout.
    ex = ThreadPoolExecutor(max_workers=min(16, max(1, len(candidates))))
    fut_to_url = {ex.submit(_try_one, url, per_url_open_ms): url for url in candidates}
    result: str | None = None
    try:
        for fut in as_completed(fut_to_url):
            try:
                if fut.result():
                    result = fut_to_url[fut]
                    break
            except Exception:
                continue
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    return result


def redact(url: str) -> str:
    """Hide the password in a stream URL for display/logging."""
    if "://" not in url or "@" not in url:
        return url
    head, sep, tail = url.partition("://")
    creds, at, rest = tail.partition("@")
    if ":" in creds:
        user = creds.split(":", 1)[0]
        creds = f"{user}:••••"
    return f"{head}://{creds}{at}{rest}"
