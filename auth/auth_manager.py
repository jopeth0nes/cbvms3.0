"""Credential verification for CBVMS."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from database.db_manager import CBVMSDatabase

class AuthManager:
    def __init__(self, database: CBVMSDatabase, *, recognizer=None) -> None:
        self._db = database
        self.recognizer = recognizer

    def verify_login(self, username: str, password: str) -> bool:
        if not username or not password:
            return False
        return self._db.verify_user(username, password)

    def authenticate(self, username: str, password: str) -> dict | None:
        """Return an auth dict on success, else None.

        Students receive a persisted, possibly restricted session. Callers must
        check must_change_password and validate session_token before portal access.
        A successful credential check does not complete first-login setup.
        Staff results retain role, username, student_id=None and display_name.
        """
        if not username or not password:
            return None
        uname = username.strip()

        # 2. Self-registered student accounts
        student_acc = self._db.verify_student_account(uname, password)
        if student_acc is not None:
            return self._db.create_student_session(student_acc)

        # A persisted student username cannot fall through to another role on a bad password.
        if self._db.username_exists(uname):
            return None

        # 3. Admin users (DB)
        if self._db.verify_user(uname, password):
            return {
                "role": self._db.get_user_role(uname),
                "username": uname,
                "student_id": None,
                "display_name": uname,
            }
        return None
