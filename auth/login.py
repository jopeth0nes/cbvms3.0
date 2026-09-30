"""CBVMS login screen."""

from __future__ import annotations

import sys
import time
from typing import Callable

import customtkinter as ctk

from auth.auth_manager import AuthManager
from auth.register import StudentRegistrationWindow
from ui.components import apply_cbvms_theme
from ui.login_intro import LoginIntro
from ui.window_lifecycle import WorkspaceWindow


def body_font(size: int = 14, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family="Segoe UI", size=size, weight=weight)



class CBVMSLoginWindow(WorkspaceWindow):
    WIDTH = 920
    HEIGHT = 620

    def __init__(self, auth_manager: AuthManager, on_ready=None) -> None:
        super().__init__()
        self.withdraw()  # Map only after the intro covers the prepared form.
        self._auth = auth_manager
        self._registration_model = ({"recognizer": auth_manager.recognizer}
                                    if auth_manager.recognizer is not None else {})
        self._on_ready = on_ready
        self.result_username: str | None = None
        self.result: dict | None = None
        self._password_visible = False
        self._reg_win = None
        self._welcome_job = None
        self._signing_in = False

        apply_cbvms_theme()
        self._configure_window()
        self._intro = LoginIntro(self, self._build_ui)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_window(self) -> None:
        self.title("CBVMS — Login")
        self.geometry(f"{self.WIDTH}x{self.HEIGHT}")
        self.minsize(self.WIDTH, self.HEIGHT)
        self.maxsize(self.WIDTH, self.HEIGHT)
        self.configure(fg_color="#F3F5F7")
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
        navy, teal = "#112C36", "#087F75"
        ink, muted, border = "#172F39", "#526570", "#CED8DE"
        self.grid_columnconfigure(0, weight=4, uniform="login")
        self.grid_columnconfigure(1, weight=5, uniform="login")
        self.grid_rowconfigure(0, weight=1)

        brand = ctk.CTkFrame(self, fg_color=navy, corner_radius=0)
        brand.grid(row=0, column=0, sticky="nsew")
        brand.grid_columnconfigure(0, weight=1)
        brand.grid_rowconfigure(2, weight=1)
        wordmark = ctk.CTkFrame(brand, fg_color="transparent")
        wordmark.grid(row=0, column=0, sticky="w", padx=34, pady=(36, 0))
        ctk.CTkLabel(wordmark, text="C", width=40, height=40,
                     corner_radius=12, fg_color="#B8E9DC", text_color=navy,
                     font=body_font(24, "bold")).pack(side="left", padx=(0, 12))
        ctk.CTkLabel(wordmark, text="CBVMS", font=body_font(22, "bold"),
                     text_color="#FFFFFF").pack(side="left")

        story = ctk.CTkFrame(brand, fg_color="transparent")
        story.grid(row=1, column=0, sticky="nw", padx=34, pady=(62, 0))
        ctk.CTkLabel(story, text="CAMPUS ENTRANCE MONITORING", anchor="w",
                     font=body_font(11, "bold"), text_color="#B8E9DC").pack(anchor="w")
        ctk.CTkLabel(story, text="A smarter view.\nA safer campus.", justify="left",
                     font=body_font(34, "bold"), text_color="#FFFFFF").pack(anchor="w", pady=(16, 16))
        ctk.CTkLabel(story, text="Computer Based Vision\nMonitoring System", justify="left",
                     font=body_font(15), text_color="#BCD0D6").pack(anchor="w")

        # Architectural motif: an entrance framed by three layers of vision.
        art = ctk.CTkFrame(brand, width=285, height=116, fg_color="#1C414A",
                           corner_radius=22, border_width=1, border_color="#345861")
        art.grid(row=2, column=0, sticky="sw", padx=34, pady=(22, 24))
        art.grid_propagate(False)
        for x, h, color in ((24, 52, "#38726F"), (64, 76, "#70B6A8"),
                            (104, 96, "#B8E9DC")):
            ctk.CTkFrame(art, width=26, height=h, corner_radius=7,
                          fg_color=color).place(x=x, rely=1, y=-12, anchor="sw")
        ctk.CTkLabel(art, text="One campus.\nConnected.", justify="left",
                     text_color="#E4F6F0", font=body_font(16, "bold")).place(x=154, y=35)
        ctk.CTkLabel(brand, text="STUDENTS  /  ADMINISTRATORS", text_color="#BCD0D6",
                     font=body_font(10, "bold")).grid(row=3, column=0, sticky="w",
                                                     padx=34, pady=(0, 30))

        panel = ctk.CTkFrame(self, fg_color="#F3F5F7", corner_radius=0)
        panel.grid(row=0, column=1, sticky="nsew")
        inner = ctk.CTkFrame(panel, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=44, pady=(40, 28))
        ctk.CTkLabel(inner, text="YOUR CAMPUS WORKSPACE", font=body_font(11, "bold"),
                     text_color=teal, anchor="w").pack(fill="x")
        ctk.CTkLabel(inner, text="Welcome back", font=body_font(32, "bold"),
                     text_color=ink, anchor="w").pack(fill="x", pady=(16, 4))
        ctk.CTkLabel(inner, text="Sign in with your campus account to continue.",
                     font=body_font(13), text_color=muted, anchor="w").pack(fill="x", pady=(0, 28))

        entry_style = dict(height=48, corner_radius=10, border_width=1,
                           border_color=border, fg_color="#FFFFFF", text_color=ink,
                           placeholder_text_color=muted, font=body_font(14))
        ctk.CTkLabel(inner, text="Username", font=body_font(13, "bold"),
                     text_color=ink, anchor="w").pack(fill="x")
        self.username_entry = ctk.CTkEntry(inner, placeholder_text="Enter your username", **entry_style)
        self.username_entry.pack(fill="x", pady=(6, 18))
        ctk.CTkLabel(inner, text="Password", font=body_font(13, "bold"),
                     text_color=ink, anchor="w").pack(fill="x")
        pwd_row = ctk.CTkFrame(inner, fg_color="transparent")
        pwd_row.pack(fill="x", pady=(6, 0))
        self.password_entry = ctk.CTkEntry(pwd_row, placeholder_text="Enter your password",
                                          show="\u2022", **entry_style)
        self.password_entry.pack(side="left", fill="x", expand=True)
        self.toggle_btn = ctk.CTkButton(pwd_row, text="Show", width=62, height=48,
                                        corner_radius=10, fg_color="#E2EBEE",
                                        hover_color="#D1E0E4", text_color=ink,
                                        font=body_font(12, "bold"), command=self._toggle_password)
        self.toggle_btn.pack(side="right", padx=(8, 0))
        for entry in (self.username_entry, self.password_entry):
            entry.bind("<FocusIn>", lambda event, field=entry: field.configure(border_color=teal))
            entry.bind("<FocusOut>", lambda event, field=entry: field.configure(border_color=border))
        self.error_label = ctk.CTkLabel(inner, text="", height=36, anchor="w",
                                        wraplength=360, font=body_font(12), text_color="#B42318")
        self.error_label.pack(fill="x", pady=(4, 4))
        self.login_btn = ctk.CTkButton(inner, text="Sign in", height=48, corner_radius=10,
                                       fg_color=teal, hover_color="#06675F", text_color="#FFFFFF",
                                       font=body_font(15, "bold"), command=self._attempt_login)
        self.login_btn.pack(fill="x")
        ctk.CTkLabel(inner, text="For students and administrators", font=body_font(12),
                     text_color=muted).pack(pady=(14, 0))
        footer = ctk.CTkFrame(inner, fg_color="transparent")
        footer.pack(side="bottom", fill="x", pady=(16, 0))
        ctk.CTkFrame(footer, height=1, fg_color=border).pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(footer, text="Need access? Contact your campus administrator.",
                     font=body_font(11), text_color=muted, wraplength=360).pack()
        self.bind("<Return>", lambda _e: self._attempt_login())
        # The intro gives keyboard focus to the form once its reveal finishes.

    def _toggle_password(self) -> None:
        self._password_visible = not self._password_visible
        self.password_entry.configure(show="" if self._password_visible else "•")
        self.toggle_btn.configure(text="Hide" if self._password_visible else "Show")

    def _attempt_login(self) -> None:
        if not self._intro.finished or self._signing_in:
            return
        username = self.username_entry.get().strip()
        password = self.password_entry.get()

        if not username or not password:
            self.error_label.configure(text="Enter your username and password to continue.")
            return

        result = self._auth.authenticate(username, password)
        if result is not None:
            if not self._intro.finished:
                self._intro.cancel()
            self.error_label.configure(text="")
            self.result = result
            self.result_username = result["username"]
            name = "Admin"
            if result["role"] == "student":
                student = self._auth._db.get_student_by_student_id(result["student_id"]) or {}
                name = student.get("name") or result.get("display_name") or result["username"]
                self.result["display_name"] = name
            self._show_welcome(name)
            return

        self.error_label.configure(text="Invalid username or password.")

    def _show_welcome(self, name: str) -> None:
        """One in-window greeting between authentication and role routing."""
        self._signing_in = True
        self.login_btn.configure(state="disabled")
        self.unbind("<Return>")
        self.password_entry.delete(0, "end")
        # Retire the form rather than letting deferred draws flash beneath
        # the greeting during the role handoff.
        for child in self.winfo_children():
            if isinstance(child, ctk.CTkFrame):
                child.grid_remove()
        panel = ctk.CTkFrame(self, fg_color="#101D29", corner_radius=0)
        panel.place(x=0, y=0, relwidth=1, relheight=1)
        panel.lift()
        content = ctk.CTkFrame(panel, fg_color="transparent")
        content.place(relx=.5, rely=.5, anchor="center", relwidth=.85)
        ctk.CTkLabel(content, text="CBVMS  /  CAMPUS WORKSPACE",
                     font=body_font(12, "bold"), text_color="#D0AE68").pack(pady=(0, 22))
        title = ctk.CTkLabel(content, text=f"Welcome {name}", wraplength=700,
                             font=body_font(32, "bold"), text_color="#101D29")
        title.pack()
        ctk.CTkLabel(content, text="Your workspace is ready.", font=body_font(14),
                     text_color="#AABCC5").pack(pady=(12, 24))
        progress = ctk.CTkProgressBar(content, width=200, height=3,
                                     fg_color="#304655", progress_color="#D0AE68")
        progress.pack()
        progress.set(0)
        started = time.monotonic()

        def animate():
            self._welcome_job = None
            elapsed = time.monotonic() - started
            if elapsed >= .65:
                self.destroy()  # Existing run_login routing opens the correct workspace.
                return
            fade = min(1, elapsed / .2)
            color = tuple(round(a + (b-a)*fade) for a, b in
                          zip((16, 29, 41), (245, 242, 233)))
            title.configure(text_color="#%02x%02x%02x" % color)
            progress.set(min(1, elapsed / .65))
            self._welcome_job = self.after(16, animate)

        animate()

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
        if self._welcome_job is not None:
            self.after_cancel(self._welcome_job)
            self._welcome_job = None
        if not self._intro.finished:
            self._intro.cancel()
        self.result = None
        self.result_username = None
        self.destroy()


def run_login(auth_manager: AuthManager, on_ready=None) -> str | None:
    """Show the login window and route by role.

    - admin → return the username so the caller launches the admin dashboard.
    - student → launch the StudentPortal here; loop back to login on logout,
      or return None (exit) when the portal window is closed directly.
    - closed login window → return None.
    """
    import gc

    while True:
        app = CBVMSLoginWindow(auth_manager, on_ready=on_ready)
        on_ready = None
        app.mainloop()
        result = app.result
        del app
        gc.collect()  # Finalize the destroyed Tk root on its owning thread.
        if not result:
            return None

        if result["role"] == "student":
            from ui.student_portal import StudentPortal

            portal = StudentPortal(
                student_id=result["student_id"],
                display_name=result["display_name"],
                database=auth_manager._db,
            )
            portal.mainloop()
            logged_out = portal.logged_out
            del portal
            gc.collect()
            if logged_out:
                continue  # back to the login screen
            return None  # portal closed directly → exit the app

        return result["username"]  # admin → caller opens the dashboard
