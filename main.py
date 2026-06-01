"""CBVMS application entry point."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from auth.auth_manager import AuthManager
from auth.login import run_login
from database.db_manager import CBVMSDatabase
from ui.dashboard import open_dashboard


def main() -> None:
    database = CBVMSDatabase()
    database.initialize()

    auth = AuthManager(database)

    username = run_login(auth)
    if not username:
        return  # login window closed — exit

    logged_out = open_dashboard(username=username)

    if logged_out:
        # Both CBVMSLoginWindow and CBVMSDashboard extend ctk.CTk (the Tkinter
        # root). Tkinter/CustomTkinter leaves stale global state after the root
        # is destroyed, so creating a second root in the same process crashes.
        # Restarting the process gives a clean slate and brings the login screen
        # back instantly without the user noticing any difference.
        os.execv(sys.executable, [sys.executable] + sys.argv)


if __name__ == "__main__":
    main()
