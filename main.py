"""CBVMS application entry point."""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from auth.auth_manager import AuthManager
from auth.login import run_login
from core.person_detector import PersonDetector
from core.recognizer import FaceRecognizer
from database.db_manager import CBVMSDatabase
from ui.dashboard import open_dashboard


def _warm_models(recognizer: FaceRecognizer, person_detector) -> None:
    """Load + prime the heavy CV models in the background while the user logs in, so the
    Live Monitor scans the instant the dashboard opens (model load is the main cost)."""
    import numpy as np
    dummy = np.zeros((480, 640, 3), dtype=np.uint8)
    try:
        if recognizer._ensure_models():
            recognizer.detect_faces(dummy)          # prime SCRFD detection graph
    except Exception as exc:
        print(f"[CBVMS] recognizer warm failed: {exc}")
    try:
        if person_detector is not None:
            person_detector._ensure_model()
            person_detector.detect_persons(dummy)   # prime YOLO graph
    except Exception as exc:
        print(f"[CBVMS] person-detector warm failed: {exc}")


def main() -> None:
    database = CBVMSDatabase()
    database.initialize()

    # Build the heavy CV objects now and warm their models on a background thread while
    # the login screen is up — by the time the user signs in, scanning is ready to go.
    recognizer = FaceRecognizer(database)
    try:
        person_detector = PersonDetector()
    except Exception as exc:
        print(f"[CBVMS] PersonDetector init failed: {exc}")
        person_detector = None
    threading.Thread(
        target=_warm_models, args=(recognizer, person_detector), daemon=True
    ).start()

    auth = AuthManager(database)

    username = run_login(auth)
    if not username:
        return  # login window closed — exit

    logged_out = open_dashboard(
        username=username,
        database=database,
        recognizer=recognizer,
        person_detector=person_detector,
    )

    if logged_out:
        # Both CBVMSLoginWindow and CBVMSDashboard extend ctk.CTk (the Tkinter
        # root). Tkinter/CustomTkinter leaves stale global state after the root
        # is destroyed, so creating a second root in the same process crashes.
        # Restarting the process gives a clean slate and brings the login screen
        # back instantly without the user noticing any difference.
        os.execv(sys.executable, [sys.executable] + sys.argv)


if __name__ == "__main__":
    main()
