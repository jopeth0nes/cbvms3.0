"""Credential verification for CBVMS."""

from __future__ import annotations

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

        Only persisted account mappings authorize student access.
        Dict keys: role ("admin"|"superadmin"|"student"), username, student_id (None for staff),
        display_name.
        """
        if not username or not password:
            return None
        uname = username.strip()

        # 2. Self-registered student accounts
        student_acc = self._db.verify_student_account(uname, password)
        if student_acc is not None:
            return {
                "role": "student",
                "username": uname,
                "student_id": student_acc["student_id"],
                "display_name": student_acc["display_name"],
            }

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
