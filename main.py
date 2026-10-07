"""CBVMS application entry point."""

from __future__ import annotations

import logging
import gc
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
    from core.model_readiness import face_readiness
    face_readiness(recognizer).start()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
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
    def start_model_warmup():
        threading.Thread(
            target=_warm_models, args=(recognizer, person_detector), daemon=True
        ).start()

    auth = AuthManager(database, recognizer=recognizer)

    username = run_login(auth, on_ready=start_model_warmup)
    if not username:
        if hasattr(recognizer,'readiness'):
            recognizer.readiness.wait(5.)
        return  # login window closed — exit

    # The login root has been destroyed. Dispose its cyclic font/widget objects
    # here so a model/database worker cannot run Tk finalizers for that old root.
    gc.collect()

    # Pick up any students who registered via the login screen's self-registration
    # window (the recognizer was created before login, so new enrollments are stale).
    threading.Thread(target=recognizer.load_known_faces, daemon=True,
                     name="post-login-face-refresh").start()

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
