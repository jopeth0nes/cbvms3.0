"""Account Manager panel — admin view of all student accounts with password reset."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

import customtkinter as ctk

from ui.components import (
    COLOR_ACCENT,
    COLOR_ACCENT_HOVER,
    COLOR_BG,
    COLOR_BORDER,
    COLOR_DANGER,
    COLOR_SAFE,
    COLOR_SURFACE,
    COLOR_TEXT,
    COLOR_TEXT_MUTED,
    COLOR_WARNING,
    CORNER_RADIUS,
    PADDING,
    body_font,
    body_small_font,
    heading_font,
    panel_title_font,
    show_toast,
)

if TYPE_CHECKING:
    from database.db_manager import CBVMSDatabase


class AccountManagerPanel(ctk.CTkFrame):
    """Lists all student accounts and lets the admin reset any password."""

    def __init__(self, master, *, database: "CBVMSDatabase", **kwargs) -> None:
        super().__init__(master, fg_color=COLOR_BG, **kwargs)
        self.database = database
        self._accounts: list[dict] = []
        self._selected_account: dict | None = None

        self.grid_columnconfigure(0, weight=3)
        self.grid_columnconfigure(1, weight=2)
        self.grid_rowconfigure(0, weight=1)

        self._build_list_panel()
        self._build_detail_panel()
        self._reload()
        self.grid_remove()

    # ------------------------------------------------------------------
    # List panel (left)
    # ------------------------------------------------------------------

    def _build_list_panel(self) -> None:
        left = ctk.CTkFrame(self, fg_color=COLOR_SURFACE, corner_radius=CORNER_RADIUS,
                            border_width=1, border_color=COLOR_BORDER)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, PADDING // 2))
        left.grid_rowconfigure(2, weight=1)
        left.grid_columnconfigure(0, weight=1)

        # Header
        hdr = ctk.CTkFrame(left, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=PADDING, pady=(PADDING, 8))
        hdr.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(hdr, text="Student Accounts", font=heading_font(16),
                     text_color=COLOR_TEXT).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(hdr, text="↺  Reload", width=90, height=30,
                      corner_radius=CORNER_RADIUS, fg_color=COLOR_BORDER,
                      hover_color=COLOR_ACCENT_HOVER,
                      command=self._reload).grid(row=0, column=1, sticky="e")

        # Search
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._apply_filter())
        ctk.CTkEntry(left, placeholder_text="Search by name, student ID or username…",
                     textvariable=self._search_var).grid(
            row=1, column=0, sticky="ew", padx=PADDING, pady=(0, 8))

        # Table
        wrap = ctk.CTkFrame(left, fg_color=COLOR_BG, corner_radius=CORNER_RADIUS)
        wrap.grid(row=2, column=0, sticky="nsew", padx=PADDING, pady=(0, 8))
        wrap.grid_rowconfigure(0, weight=1)
        wrap.grid_columnconfigure(0, weight=1)

        self._configure_tree_style()
        cols = ("name", "student_id", "username", "course", "created_at")
        self._tree = ttk.Treeview(wrap, columns=cols, show="headings",
                                  style="AccMgr.Treeview", selectmode="browse")
        heads = {"name": "Name", "student_id": "Student ID",
                 "username": "Username", "course": "Course", "created_at": "Registered"}
        widths = {"name": 140, "student_id": 100, "username": 110,
                  "course": 90, "created_at": 110}
        for c in cols:
            self._tree.heading(c, text=heads[c])
            self._tree.column(c, width=widths[c], anchor="w")

        vsb = ttk.Scrollbar(wrap, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        self._tree.bind("<<TreeviewSelect>>", self._on_row_select)

        # Footer count
        self._count_lbl = ctk.CTkLabel(left, text="0 accounts",
                                       font=body_small_font(), text_color=COLOR_TEXT_MUTED)
        self._count_lbl.grid(row=3, column=0, sticky="w", padx=PADDING, pady=(0, PADDING))

    @staticmethod
    def _configure_tree_style() -> None:
        s = ttk.Style()
        s.theme_use("clam")
        s.configure("AccMgr.Treeview", background=COLOR_BG, foreground=COLOR_TEXT,
                    fieldbackground=COLOR_BG, bordercolor=COLOR_BORDER, rowheight=28)
        s.configure("AccMgr.Treeview.Heading", background=COLOR_SURFACE,
                    foreground=COLOR_TEXT, relief="flat")
        s.map("AccMgr.Treeview",
              background=[("selected", COLOR_ACCENT)],
              foreground=[("selected", COLOR_TEXT)])

    # ------------------------------------------------------------------
    # Detail / reset panel (right)
    # ------------------------------------------------------------------

    def _build_detail_panel(self) -> None:
        right = ctk.CTkFrame(self, fg_color=COLOR_SURFACE, corner_radius=CORNER_RADIUS,
                             border_width=1, border_color=COLOR_BORDER)
        right.grid(row=0, column=1, sticky="nsew", padx=(PADDING // 2, 0))
        right.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(right, text="Account Details", font=heading_font(16),
                     text_color=COLOR_TEXT).pack(anchor="w", padx=PADDING, pady=(PADDING, 4))

        # Info card
        self._info_card = ctk.CTkFrame(right, fg_color=COLOR_BG, corner_radius=CORNER_RADIUS)
        self._info_card.pack(fill="x", padx=PADDING, pady=(0, PADDING))
        self._info_card.grid_columnconfigure(1, weight=1)

        self._lbl_name     = self._info_row(self._info_card, 0, "Full Name")
        self._lbl_sid      = self._info_row(self._info_card, 1, "Student ID")
        self._lbl_username = self._info_row(self._info_card, 2, "Username")
        self._lbl_course   = self._info_row(self._info_card, 3, "Course")
        self._lbl_year     = self._info_row(self._info_card, 4, "Year & Section")
        self._lbl_created  = self._info_row(self._info_card, 5, "Registered")

        # Password hash display
        hash_card = ctk.CTkFrame(right, fg_color=COLOR_BG, corner_radius=CORNER_RADIUS)
        hash_card.pack(fill="x", padx=PADDING, pady=(0, PADDING))
        ctk.CTkLabel(hash_card, text="Password Hash", font=body_small_font(),
                     text_color=COLOR_TEXT_MUTED).pack(anchor="w", padx=10, pady=(8, 2))
        self._hash_box = ctk.CTkTextbox(hash_card, height=52, corner_radius=6,
                                        fg_color=COLOR_SURFACE, border_width=1,
                                        border_color=COLOR_BORDER, text_color=COLOR_TEXT_MUTED,
                                        font=ctk.CTkFont(family="Courier", size=10),
                                        state="disabled")
        self._hash_box.pack(fill="x", padx=10, pady=(0, 8))

        # Divider
        ctk.CTkFrame(right, fg_color=COLOR_BORDER, height=1).pack(fill="x", padx=PADDING)

        # Reset password section
        ctk.CTkLabel(right, text="Reset Password", font=heading_font(14),
                     text_color=COLOR_TEXT).pack(anchor="w", padx=PADDING, pady=(PADDING, 4))
        ctk.CTkLabel(right,
                     text="Set a new password for the selected student.\nThey can use it to log in immediately.",
                     font=body_small_font(), text_color=COLOR_TEXT_MUTED,
                     justify="left").pack(anchor="w", padx=PADDING, pady=(0, 10))

        pw_frame = ctk.CTkFrame(right, fg_color="transparent")
        pw_frame.pack(fill="x", padx=PADDING, pady=(0, 4))
        pw_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(pw_frame, text="New Password", font=body_small_font(),
                     text_color=COLOR_TEXT_MUTED).grid(row=0, column=0, sticky="w", pady=(0, 4))
        self._pw_entry = ctk.CTkEntry(pw_frame, placeholder_text="Enter new password…",
                                      show="•", height=38, corner_radius=CORNER_RADIUS,
                                      fg_color=COLOR_BG, border_color=COLOR_BORDER)
        self._pw_entry.grid(row=1, column=0, sticky="ew", pady=(0, 4))

        ctk.CTkLabel(pw_frame, text="Confirm Password", font=body_small_font(),
                     text_color=COLOR_TEXT_MUTED).grid(row=2, column=0, sticky="w", pady=(4, 4))
        self._pw_confirm = ctk.CTkEntry(pw_frame, placeholder_text="Confirm new password…",
                                        show="•", height=38, corner_radius=CORNER_RADIUS,
                                        fg_color=COLOR_BG, border_color=COLOR_BORDER)
        self._pw_confirm.grid(row=3, column=0, sticky="ew")

        # Show/hide toggle
        self._show_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(pw_frame, text="Show password", variable=self._show_var,
                        font=body_small_font(), text_color=COLOR_TEXT_MUTED,
                        command=self._toggle_show).grid(row=4, column=0, sticky="w", pady=(6, 0))

        self._status_lbl = ctk.CTkLabel(right, text="Select a student to reset their password.",
                                        font=body_small_font(), text_color=COLOR_TEXT_MUTED,
                                        wraplength=280, justify="left")
        self._status_lbl.pack(anchor="w", padx=PADDING, pady=(8, 0))

        self._reset_btn = ctk.CTkButton(
            right, text="Reset Password", height=40, corner_radius=CORNER_RADIUS,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
            font=heading_font(13), command=self._do_reset, state="disabled",
        )
        self._reset_btn.pack(fill="x", padx=PADDING, pady=(10, 4))

        self._delete_btn = ctk.CTkButton(
            right, text="Delete Account", height=38, corner_radius=CORNER_RADIUS,
            fg_color=COLOR_DANGER, hover_color="#DC2626",
            font=body_font(12), command=self._do_delete, state="disabled",
        )
        self._delete_btn.pack(fill="x", padx=PADDING, pady=(0, PADDING))

    @staticmethod
    def _info_row(parent, row: int, label: str) -> ctk.CTkLabel:
        ctk.CTkLabel(parent, text=label, font=body_small_font(),
                     text_color=COLOR_TEXT_MUTED, anchor="w").grid(
            row=row, column=0, sticky="w", padx=(10, 6), pady=5)
        val = ctk.CTkLabel(parent, text="—", font=body_small_font(),
                           text_color=COLOR_TEXT, anchor="w")
        val.grid(row=row, column=1, sticky="ew", padx=(0, 10), pady=5)
        return val

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def _reload(self) -> None:
        self._accounts = self.database.get_all_student_accounts()
        self._apply_filter()

    def _apply_filter(self) -> None:
        query = self._search_var.get().strip().lower()
        for item in self._tree.get_children():
            self._tree.delete(item)
        for acc in self._accounts:
            name = (acc.get("name") or "").lower()
            sid  = (acc.get("student_id") or "").lower()
            uname = (acc.get("username") or "").lower()
            if query and query not in name and query not in sid and query not in uname:
                continue
            created = (acc.get("created_at") or "")[:10]
            self._tree.insert("", "end", iid=str(acc["id"]),
                              values=(
                                  acc.get("name") or "—",
                                  acc.get("student_id") or "—",
                                  acc.get("username") or "—",
                                  acc.get("course") or "—",
                                  created or "—",
                              ))
        visible = len(self._tree.get_children())
        self._count_lbl.configure(text=f"{visible} account{'s' if visible != 1 else ''}")

    def _on_row_select(self, _event=None) -> None:
        sel = self._tree.selection()
        if not sel:
            self._selected_account = None
            self._reset_btn.configure(state="disabled")
            self._delete_btn.configure(state="disabled")
            self._clear_detail()
            return
        acc_id = int(sel[0])
        self._selected_account = next(
            (a for a in self._accounts if a["id"] == acc_id), None)
        self._reset_btn.configure(state="normal")
        self._delete_btn.configure(state="normal")
        self._fill_detail()

    def _fill_detail(self) -> None:
        acc = self._selected_account
        if not acc:
            return
        self._lbl_name.configure(text=acc.get("name") or "—")
        self._lbl_sid.configure(text=acc.get("student_id") or "—")
        self._lbl_username.configure(text=acc.get("username") or "—")
        self._lbl_course.configure(text=acc.get("course") or "—")
        self._lbl_year.configure(text=acc.get("year_and_section") or "—")
        self._lbl_created.configure(text=(acc.get("created_at") or "—")[:19])

        phash = acc.get("password_hash") or ""
        self._hash_box.configure(state="normal")
        self._hash_box.delete("1.0", "end")
        self._hash_box.insert("1.0", phash)
        self._hash_box.configure(state="disabled")

        self._pw_entry.delete(0, "end")
        self._pw_confirm.delete(0, "end")
        self._status_lbl.configure(
            text=f"Resetting password for: {acc.get('name') or acc.get('student_id')}",
            text_color=COLOR_TEXT_MUTED)

    def _clear_detail(self) -> None:
        for lbl in (self._lbl_name, self._lbl_sid, self._lbl_username,
                    self._lbl_course, self._lbl_year, self._lbl_created):
            lbl.configure(text="—")
        self._hash_box.configure(state="normal")
        self._hash_box.delete("1.0", "end")
        self._hash_box.configure(state="disabled")
        self._status_lbl.configure(text="Select a student to reset their password.",
                                   text_color=COLOR_TEXT_MUTED)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _toggle_show(self) -> None:
        show = "" if self._show_var.get() else "•"
        self._pw_entry.configure(show=show)
        self._pw_confirm.configure(show=show)

    def _do_reset(self) -> None:
        acc = self._selected_account
        if not acc:
            return
        pw  = self._pw_entry.get()
        cpw = self._pw_confirm.get()

        if not pw:
            self._set_status("Please enter a new password.", error=True)
            return
        if len(pw) < 6:
            self._set_status("Password must be at least 6 characters.", error=True)
            return
        if pw != cpw:
            self._set_status("Passwords do not match.", error=True)
            return

        ok = self.database.reset_student_password(acc["student_id"], pw)
        if ok:
            # Refresh the hash display immediately
            self._accounts = self.database.get_all_student_accounts()
            self._apply_filter()
            updated = next(
                (a for a in self._accounts if a["id"] == acc["id"]), None)
            if updated:
                self._selected_account = updated
                self._fill_detail()
            self._pw_entry.delete(0, "end")
            self._pw_confirm.delete(0, "end")
            self._set_status(
                f"Password reset successfully for {acc.get('name') or acc.get('student_id')}.",
                success=True)
            show_toast(self, "Password reset successfully.", type="success")
        else:
            self._set_status("Reset failed — student account not found.", error=True)

    def _do_delete(self) -> None:
        acc = self._selected_account
        if not acc:
            return
        from tkinter import messagebox
        name = acc.get("name") or acc.get("student_id") or "this account"
        if not messagebox.askyesno(
            "Confirm Delete",
            f"Delete the login account for {name}?\n\nThe student will no longer be able to log in.",
            parent=self.winfo_toplevel(),
        ):
            return
        try:
            with self.database.connect() as conn:
                conn.execute(
                    "DELETE FROM student_accounts WHERE student_id = ?",
                    (acc["student_id"],))
                conn.commit()
        except Exception as exc:
            self._set_status(f"Delete failed: {exc}", error=True)
            return
        self._selected_account = None
        self._reset_btn.configure(state="disabled")
        self._delete_btn.configure(state="disabled")
        self._clear_detail()
        self._reload()
        self._set_status(f"Account for {name} deleted.", success=True)
        show_toast(self, f"Account deleted for {name}.", type="success")

    def _set_status(self, msg: str, *, success: bool = False, error: bool = False) -> None:
        color = COLOR_SAFE if success else COLOR_DANGER if error else COLOR_TEXT_MUTED
        self._status_lbl.configure(text=msg, text_color=color)

    # ------------------------------------------------------------------
    # Lifecycle hooks (called by dashboard)
    # ------------------------------------------------------------------

    def on_show(self) -> None:
        self._reload()

    def on_hide(self) -> None:
        pass
