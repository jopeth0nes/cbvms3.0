"""CBVMS login screen."""

from __future__ import annotations

import sys
from typing import Callable

import customtkinter as ctk

from auth.auth_manager import AuthManager
from auth.register import StudentRegistrationWindow
from ui.components import (
    COLOR_ACCENT,
    COLOR_ACCENT_HOVER,
    COLOR_BG,
    COLOR_BORDER,
    COLOR_DANGER,
    COLOR_SURFACE,
    COLOR_TEXT_MUTED,
    CORNER_RADIUS,
    PADDING,
    PADDING_LG,
    apply_cbvms_theme,
    body_font,
    heading_font,
)


class CBVMSLoginWindow(ctk.CTk):
    WIDTH = 420
    HEIGHT = 560

    def __init__(self, auth_manager: AuthManager) -> None:
        super().__init__()
        self._auth = auth_manager
        self.result_username: str | None = None
        self.result: dict | None = None
        self._password_visible = False
        self._reg_win = None

        apply_cbvms_theme()
        self._configure_window()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_window(self) -> None:
        self.title("CBVMS — Login")
        self.geometry(f"{self.WIDTH}x{self.HEIGHT}")
        self.minsize(self.WIDTH, self.HEIGHT)
        self.maxsize(self.WIDTH, self.HEIGHT)
        self.configure(fg_color=COLOR_BG)
        self.resizable(False, False)
        self._center_on_screen()

    def _center_on_screen(self) -> None:
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = (sw - self.WIDTH) // 2
        y = (sh - self.HEIGHT) // 2
        self.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")

    def _build_ui(self) -> None:
        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=PADDING_LG, pady=PADDING_LG)

        ctk.CTkLabel(
            container,
            text="Computer Based Vision\nMonitoring System",
            font=heading_font(20),
            text_color=COLOR_ACCENT,
            justify="center",
        ).pack(pady=(8, 4))

        ctk.CTkLabel(
            container,
            text="CBVMS — Campus Entrance Monitoring",
            font=body_font(13),
            text_color=COLOR_TEXT_MUTED,
        ).pack(pady=(0, PADDING_LG))

        form = ctk.CTkFrame(
            container,
            fg_color=COLOR_SURFACE,
            corner_radius=CORNER_RADIUS,
            border_width=1,
            border_color=COLOR_BORDER,
        )
        form.pack(fill="x", pady=(0, PADDING))

        inner = ctk.CTkFrame(form, fg_color="transparent")
        inner.pack(fill="x", padx=PADDING, pady=PADDING)

        ctk.CTkLabel(
            inner,
            text="Username",
            font=body_font(12),
            text_color=COLOR_TEXT_MUTED,
            anchor="w",
        ).pack(fill="x")
        self.username_entry = ctk.CTkEntry(
            inner,
            placeholder_text="Enter username",
            height=40,
            corner_radius=CORNER_RADIUS,
            border_color=COLOR_BORDER,
            fg_color=COLOR_BG,
        )
        self.username_entry.pack(fill="x", pady=(4, PADDING))

        ctk.CTkLabel(
            inner,
            text="Password",
            font=body_font(12),
            text_color=COLOR_TEXT_MUTED,
            anchor="w",
        ).pack(fill="x")

        pwd_row = ctk.CTkFrame(inner, fg_color="transparent")
        pwd_row.pack(fill="x", pady=(4, 0))

        self.password_entry = ctk.CTkEntry(
            pwd_row,
            placeholder_text="Enter password",
            height=40,
            corner_radius=CORNER_RADIUS,
            border_color=COLOR_BORDER,
            fg_color=COLOR_BG,
            show="•",
        )
        self.password_entry.pack(side="left", fill="x", expand=True)

        self.toggle_btn = ctk.CTkButton(
            pwd_row,
            text="Show",
            width=64,
            height=40,
            corner_radius=CORNER_RADIUS,
            fg_color=COLOR_BORDER,
            hover_color=COLOR_ACCENT_HOVER,
            command=self._toggle_password,
        )
        self.toggle_btn.pack(side="right", padx=(8, 0))

        self.login_btn = ctk.CTkButton(
            container,
            text="Login",
            height=44,
            corner_radius=CORNER_RADIUS,
            fg_color=COLOR_ACCENT,
            hover_color=COLOR_ACCENT_HOVER,
            font=body_font(14),
            command=self._attempt_login,
        )
        self.login_btn.pack(fill="x", pady=(PADDING, 8))

        self.error_label = ctk.CTkLabel(
            container,
            text="",
            font=body_font(12),
            text_color=COLOR_DANGER,
        )
        self.error_label.pack(fill="x")

        # Divider
        div = ctk.CTkFrame(container, fg_color=COLOR_BORDER, height=1)
        div.pack(fill="x", pady=(PADDING, 0))

        ctk.CTkLabel(
            container,
            text="New student?",
            font=body_font(12),
            text_color=COLOR_TEXT_MUTED,
        ).pack(pady=(10, 4))

        ctk.CTkButton(
            container,
            text="Register as Student",
            height=40,
            corner_radius=CORNER_RADIUS,
            fg_color="transparent",
            hover_color=COLOR_ACCENT_HOVER,
            border_width=1,
            border_color=COLOR_ACCENT,
            text_color=COLOR_ACCENT,
            font=body_font(13),
            command=self._open_registration,
        ).pack(fill="x")

        self.bind("<Return>", lambda _e: self._attempt_login())
        self.username_entry.focus_set()

    def _toggle_password(self) -> None:
        self._password_visible = not self._password_visible
        self.password_entry.configure(show="" if self._password_visible else "•")
        self.toggle_btn.configure(text="Hide" if self._password_visible else "Show")

    def _attempt_login(self) -> None:
        username = self.username_entry.get().strip()
        password = self.password_entry.get()

        result = self._auth.authenticate(username, password)
        if result is not None:
            self.error_label.configure(text="")
            self.result = result
            self.result_username = result["username"]
            self.destroy()
            return

        self.error_label.configure(text="Invalid username or password.")

    def _open_registration(self) -> None:
        # Only one registration window at a time
        if self._reg_win is not None:
            try:
                if self._reg_win.winfo_exists():
                    self._reg_win.lift()
                    return
            except Exception:
                pass
        self._reg_win = StudentRegistrationWindow(self, database=self._auth._db)

    def _on_close(self) -> None:
        self.result = None
        self.result_username = None
        self.destroy()


def run_login(auth_manager: AuthManager) -> str | None:
    """Show the login window and route by role.

    - admin → return the username so the caller launches the admin dashboard.
    - student → launch the StudentPortal here; loop back to login on logout,
      or return None (exit) when the portal window is closed directly.
    - closed login window → return None.
    """
    while True:
        app = CBVMSLoginWindow(auth_manager)
        app.mainloop()
        result = app.result
        if not result:
            return None

        if result["role"] == "student":
            from ui.student_portal import StudentPortal

            portal = StudentPortal(
                student_id=result["student_id"],
                display_name=result["display_name"],
            )
            portal.mainloop()
            if getattr(portal, "logged_out", False):
                continue  # back to the login screen
            return None  # portal closed directly → exit the app

        return result["username"]  # admin → caller opens the dashboard
