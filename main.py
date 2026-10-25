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
from core.monitor_models import MonitorModels
from database.db_manager import CBVMSDatabase
from ui.dashboard import open_dashboard


def _warm_models(models: MonitorModels) -> None:
    models.start()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    database = CBVMSDatabase()
    database.initialize()
    from core.cloud_sync import start_cloud_sync
    start_cloud_sync(database)

    # Prepare reusable models in bounded background workers while login is open.
    # Fast logins can still reach the dashboard before preparation finishes.
    recognizer = FaceRecognizer(database)
    try:
        person_detector = PersonDetector()
    except Exception as exc:
        print(f"[CBVMS] PersonDetector init failed: {exc}")
        person_detector = None
    monitor_models = MonitorModels(recognizer, person_detector)
    def start_model_warmup():
        threading.Thread(
            target=_warm_models, args=(monitor_models,), daemon=True,
            name="login-model-preparation",
        ).start()

    auth = AuthManager(database, recognizer=recognizer)

    staff_result = run_login(auth, on_ready=start_model_warmup, return_staff_result=True)
    if not staff_result:
        monitor_models.cancel_pending()
        monitor_models.wait(5.)
        return  # login window closed — exit

    # The login root has been destroyed. Dispose its cyclic font/widget objects
    # here so a model/database worker cannot run Tk finalizers for that old root.
    gc.collect()

    # Pick up any students who registered via the login screen's self-registration
    # window (the recognizer was created before login, so new enrollments are stale).
    threading.Thread(target=recognizer.load_known_faces, daemon=True,
                     name="post-login-face-refresh").start()

    logged_out = open_dashboard(
        username=staff_result["username"],
        staff_session_token=staff_result["staff_session_token"],
        database=database,
        recognizer=recognizer,
        person_detector=person_detector,
        monitor_models=monitor_models,
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
