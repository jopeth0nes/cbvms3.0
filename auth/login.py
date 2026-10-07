"""CBVMS login screen."""

from __future__ import annotations

import time
import queue
import threading

from auth.passwords import validate_new_password
from database.db_manager import CBVMSDatabase
from database.student_credentials import SessionExpired

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
    OPERATION_TIMEOUT = 12.

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
        self._pending = None
        self._operation = None
        self._operation_generation = 0
        self._welcome_generation = 0

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
        self._form_panel = panel
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
        if not self._intro.finished or self._signing_in or self._pending or self._operation:
            return
        username = self.username_entry.get().strip()
        password = self.password_entry.get()

        if not username or not password:
            self.error_label.configure(text="Enter your username and password to continue.")
            return

        self._signing_in = True
        self.login_btn.configure(state="disabled", text="Signing in…")
        # Workers own a short-timeout DB and never touch Tk objects.
        manager = AuthManager(CBVMSDatabase(self._auth._db.db_path, timeout=2))
        self._start_operation(lambda cancel: manager.authenticate(username, password), self._authenticated)

    def _start_operation(self, work, on_success):
        self._operation_generation += 1
        generation = self._operation_generation
        cancel = threading.Event()
        results = queue.Queue()
        self._operation = cancel
        db = CBVMSDatabase(self._auth._db.db_path, timeout=2)
        def run():
            try:
                value, error = work(cancel), None
                if cancel.is_set() and value and value.get('session_token'):
                    db.revoke_student_session(value['session_token'])
            except Exception as exc:
                value = None
                error = str(exc) if isinstance(exc, ValueError) else 'Could not save or verify credentials. Please try again.'
            results.put((value, error))
        threading.Thread(target=run, name='login-credentials', daemon=True).start()
        started = time.monotonic()
        def poll():
            if generation != self._operation_generation or getattr(self, '_destroying', False):
                return
            try:
                value, error = results.get_nowait()
            except queue.Empty:
                if time.monotonic() - started >= self.OPERATION_TIMEOUT:
                    cancel.set()
                    self._operation_generation += 1
                    self._operation = None
                    self._operation_error('This is taking too long. Try again, or sign in again if the password was saved.')
                    return
                self.after(40, poll)
                return
            self._operation = None
            if error:
                self._operation_error(error)
            else:
                on_success(value)
        self.after(40, poll)

    def _operation_error(self, message):
        self._signing_in = False
        if self.result and not self._pending:
            self._back_to_login()
        if self._pending:
            self.setup_error.configure(text=message)
            self.save_password_btn.configure(state='normal', text='Save Password & Continue')
        else:
            self.error_label.configure(text=message)
            self.login_btn.configure(state='normal', text='Sign in')

    def _authenticated(self, result):
        self._signing_in = False
        self.login_btn.configure(state='normal', text='Sign in')
        self.password_entry.delete(0, 'end')
        if not result:
            self.error_label.configure(text='Invalid username or password.')
            return
        if result.get('must_change_password'):
            self._pending = result
            self.result = self.result_username = None
            self._show_password_setup()
            return
        self.result = result
        self.result_username = result['username']
        name = result['display_name'] if result['role'] == 'student' else (
            'Superadmin' if result['role'] == 'superadmin' else 'Admin')
        self._show_welcome(name)

    def _show_password_setup(self):
        self._cancel_welcome()
        self.unbind('<Return>')
        self._form_panel.grid_remove()
        panel = self._setup_panel = ctk.CTkFrame(self, fg_color='#F3F5F7', corner_radius=0)
        panel.grid(row=0, column=1, sticky='nsew')
        inner = ctk.CTkFrame(panel, fg_color='transparent')
        inner.pack(fill='both', expand=True, padx=36, pady=30)
        ctk.CTkLabel(inner, text='YOUR CAMPUS WORKSPACE', anchor='w',
                     font=body_font(11, 'bold'), text_color='#087F75').pack(fill='x')
        ctk.CTkLabel(inner, text='Set your new password', anchor='w',
                     font=body_font(25, 'bold'), text_color='#172F39').pack(fill='x', pady=(14, 8))
        ctk.CTkLabel(inner, text='Before entering your student portal, replace your initial or temporary password with a password only you know.',
                     wraplength=365, justify='left', anchor='w', font=body_font(13),
                     text_color='#526570').pack(fill='x', pady=(0, 14))
        self.setup_entries = []
        for label in ('New password', 'Confirm new password'):
            ctk.CTkLabel(inner, text=label, anchor='w', font=body_font(13, 'bold'),
                         text_color='#172F39').pack(fill='x')
            row = ctk.CTkFrame(inner, fg_color='transparent')
            row.pack(fill='x', pady=(5, 12))
            entry = ctk.CTkEntry(row, show='•', height=44, corner_radius=10,
                                fg_color='white', text_color='#172F39', border_color='#CED8DE',
                                font=body_font(14))
            entry.pack(side='left', fill='x', expand=True)
            button = ctk.CTkButton(row, text='Show', width=58, height=44, fg_color='#E2EBEE',
                                   hover_color='#D1E0E4', text_color='#172F39')
            def toggle(field=entry, control=button):
                visible = bool(field.cget('show'))
                field.configure(show='' if visible else '•')
                control.configure(text='Hide' if visible else 'Show')
            button.configure(command=toggle)
            button.pack(side='right', padx=(8, 0))
            self.setup_entries.append(entry)
        ctk.CTkLabel(inner, text='Use at least 8 characters. Longer passphrases are welcome. Choose a different password; default credentials and account IDs are not allowed.',
                     wraplength=365, justify='left', anchor='w', font=body_font(11),
                     text_color='#526570').pack(fill='x')
        self.setup_error = ctk.CTkLabel(inner, text='', wraplength=365, height=42,
                                       anchor='w', justify='left', font=body_font(12), text_color='#B42318')
        self.setup_error.pack(fill='x', pady=5)
        self.save_password_btn = ctk.CTkButton(inner, text='Save Password & Continue', height=44,
            corner_radius=10, fg_color='#087F75', hover_color='#06675F', font=body_font(14, 'bold'),
            command=self._save_setup_password)
        self.save_password_btn.pack(fill='x')
        ctk.CTkButton(inner, text='Back to Login', height=36, fg_color='#E2EBEE',
                      text_color='#172F39', hover_color='#D1E0E4', command=self._back_to_login).pack(fill='x', pady=(10, 0))
        self.bind('<Return>', lambda e: self._save_setup_password())
        self.setup_entries[0].focus_set()

    def _save_setup_password(self):
        if not self._pending or self._operation:
            return
        password, confirmation = (entry.get() for entry in self.setup_entries)
        try:
            validate_new_password(password, confirmation, username=self._pending['username'],
                                  student_id=self._pending['student_id'])
        except ValueError as exc:
            self.setup_error.configure(text=str(exc))
            return
        self.save_password_btn.configure(state='disabled', text='Saving password…')
        self.setup_error.configure(text='')
        token = self._pending['session_token']
        db = CBVMSDatabase(self._auth._db.db_path, timeout=2)
        def saved(result):
            for entry in self.setup_entries:
                entry.delete(0, 'end')
            self._pending = None
            self._setup_panel.destroy()
            self._authenticated(result)
        self._start_operation(lambda cancel: db.change_student_password(token, password, confirmation, cancel=cancel), saved)

    def _cancel_welcome(self):
        self._welcome_generation += 1
        if self._welcome_job is not None:
            self.after_cancel(self._welcome_job)
            self._welcome_job = None

    def _discard_login(self):
        self._cancel_welcome()
        self._operation_generation += 1
        if self._operation:
            self._operation.set()
            self._operation = None
        tokens = {r['session_token'] for r in (self._pending, self.result) if r and r.get('session_token')}
        self._pending = self.result = self.result_username = None
        db = CBVMSDatabase(self._auth._db.db_path, timeout=2)
        def revoke():
            for token in tokens:
                db.revoke_student_session(token)
        if tokens:
            threading.Thread(target=revoke, daemon=True).start()

    def _back_to_login(self):
        self._discard_login()
        if hasattr(self, '_setup_panel') and self._setup_panel.winfo_exists():
            self._setup_panel.destroy()
        if hasattr(self, '_welcome_panel') and self._welcome_panel.winfo_exists():
            self._welcome_panel.destroy()
        for child in self.winfo_children():
            if isinstance(child, ctk.CTkFrame):
                child.grid()
        self._signing_in = False
        self.password_entry.delete(0, 'end')
        self.error_label.configure(text='')
        self.login_btn.configure(state='normal', text='Sign in')
        self.bind('<Return>', lambda e: self._attempt_login())
        self.username_entry.focus_set()

    def _show_welcome(self, name: str) -> None:
        """One in-window greeting between authentication and role routing."""
        if not self.result or self._pending or self.result.get("must_change_password"):
            return
        self._cancel_welcome()
        generation = self._welcome_generation
        self._signing_in = True
        self.login_btn.configure(state="disabled")
        self.unbind("<Return>")
        self.password_entry.delete(0, "end")
        # Retire the form rather than letting deferred draws flash beneath
        # the greeting during the role handoff.
        for child in self.winfo_children():
            if isinstance(child, ctk.CTkFrame):
                child.grid_remove()
        panel = self._welcome_panel = ctk.CTkFrame(self, fg_color="#101D29", corner_radius=0)
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
            if generation != self._welcome_generation or not self.result or self._pending:
                return
            self._welcome_job = None
            elapsed = time.monotonic() - started
            if elapsed >= .65:
                if self.result['role'] == 'student':
                    token = self.result['session_token']
                    db = CBVMSDatabase(self._auth._db.db_path, timeout=2)
                    def finish(value):
                        if generation == self._welcome_generation and self.result and not self._pending:
                            self.result = value
                            self.destroy()
                    self._start_operation(lambda cancel: db.get_student_session(token, require_full=True), finish)
                else:
                    self.destroy()
                return
            fade = min(1, elapsed / .2)
            color = tuple(round(a + (b-a)*fade) for a, b in
                          zip((16, 29, 41), (245, 242, 233)))
            title.configure(text_color="#%02x%02x%02x" % color)
            progress.set(min(1, elapsed / .65))
            self._welcome_job = self.after(16, animate)

        # Finish independently of animation draws: a failed frame must not
        # leave a successfully authenticated user trapped on the greeting.
        self.after(800, self.destroy)
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
        self._discard_login()
        if not self._intro.finished:
            self._intro.cancel()
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

            try:
                portal = StudentPortal(
                    student_id=result["student_id"],
                    display_name=result["display_name"],
                    database=auth_manager._db,
                    session_token=result["session_token"],
                )
            except SessionExpired:
                continue  # A reset during the window handoff requires fresh authentication.
            portal.mainloop()
            logged_out = portal.logged_out
            del portal
            gc.collect()
            if logged_out:
                continue  # back to the login screen
            return None  # portal closed directly → exit the app

        return result["username"]  # admin → caller opens the dashboard
