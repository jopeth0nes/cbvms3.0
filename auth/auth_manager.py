"""Credential verification for CBVMS."""

from __future__ import annotations

import hashlib

from database.db_manager import CBVMSDatabase

# Hardcoded student portal account (not stored in the DB users table).
STUDENT_CREDENTIALS = {
    "student": {
        "password_hash": hashlib.sha256("student123".encode()).hexdigest(),
        "student_id": "2023-00883",
        "display_name": "Student Portal",
        "role": "student",
    }
}


class AuthManager:
    def __init__(self, database: CBVMSDatabase) -> None:
        self._db = database

    def verify_login(self, username: str, password: str) -> bool:
        if not username or not password:
            return False
        return self._db.verify_user(username, password)

    def authenticate(self, username: str, password: str) -> dict | None:
        """Return an auth dict on success, else None.

        Priority: hardcoded student → registered student_accounts → admin users table.
        Dict keys: role ("admin"|"student"), username, student_id (None for admin),
        display_name.
        """
        if not username or not password:
            return None
        uname = username.strip()

        # 1. Hardcoded student account (legacy backward-compat)
        cred = STUDENT_CREDENTIALS.get(uname)
        if cred is not None:
            pw_hash = hashlib.sha256(password.encode()).hexdigest()
            if pw_hash == cred["password_hash"]:
                return {
                    "role": "student",
                    "username": uname,
                    "student_id": cred["student_id"],
                    "display_name": cred["display_name"],
                }
            return None

        # 2. Self-registered student accounts
        student_acc = self._db.verify_student_account(uname, password)
        if student_acc is not None:
            return {
                "role": "student",
                "username": uname,
                "student_id": student_acc["student_id"],
                "display_name": student_acc["display_name"],
            }

        # 3. Admin users (DB)
        if self._db.verify_user(uname, password):
            return {
                "role": "admin",
                "username": uname,
                "student_id": None,
                "display_name": uname,
            }
        return None
