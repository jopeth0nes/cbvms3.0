"""Camera Configuration section for Settings (USB + IP/RJ45 sources)."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import customtkinter as ctk

from api.camera_manager import camera_manager, probe_ip_camera, scan_usb_cameras, test_ip_camera
from api.camera_store import add_ip_camera, delete_ip_camera, get_camera_preference, get_saved_ip_cameras
from core.ip_camera import build_candidate_urls, redact
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
    CORNER_RADIUS,
    PADDING,
    body_small_font,
    section_title_font,
    show_toast,
)


class CameraConfigurationSection(ctk.CTkFrame):
  def __init__(
      self,
      master,
      *,
      on_camera_connected: Callable[[dict[str, Any]], None],
      apply_stream_settings: Callable[[int, tuple[int, int], int], None],
      **kwargs,
  ) -> None:
    super().__init__(master, fg_color=COLOR_SURFACE, corner_radius=CORNER_RADIUS, border_width=1, border_color=COLOR_BORDER, **kwargs)
    self._on_camera_connected = on_camera_connected
    self._apply_stream_settings = apply_stream_settings
    self._scanning = False
    self._tab = "usb"
    self._cameras: list[dict[str, Any]] = []
    self._active: dict[str, Any] | None = get_camera_preference()
    self._connecting_id: str | None = None

    self._camera_index_var = ctk.StringVar(value="0")
    self._resolution_var = ctk.StringVar(value="1280x720")
    self._fps_cap_var = ctk.IntVar(value=30)

    self._build()
    self.after(200, self._run_scan)

  def _build(self) -> None:
    header = ctk.CTkFrame(self, fg_color="transparent")
    header.pack(fill="x", padx=PADDING, pady=(PADDING, 8))
    header.grid_columnconfigure(0, weight=1)

    text_col = ctk.CTkFrame(header, fg_color="transparent")
    text_col.grid(row=0, column=0, sticky="w")
    ctk.CTkLabel(text_col, text="Camera Configuration", font=section_title_font(), text_color=COLOR_TEXT).pack(anchor="w")
    ctk.CTkLabel(
        text_col,
        text="Manage and connect camera sources for the live monitor",
        font=body_small_font(),
        text_color=COLOR_TEXT_MUTED,
    ).pack(anchor="w", pady=(2, 0))

    self._scan_btn = ctk.CTkButton(
        header,
        text="Scan for Cameras",
        height=34,
        width=150,
        corner_radius=CORNER_RADIUS,
        fg_color=COLOR_ACCENT,
        hover_color=COLOR_ACCENT_HOVER,
        command=self._run_scan,
    )
    self._scan_btn.grid(row=0, column=1, sticky="e")

    self._active_card = ctk.CTkFrame(self, fg_color=COLOR_BG, corner_radius=CORNER_RADIUS, border_width=1, border_color=COLOR_BORDER)
    self._active_card.pack(fill="x", padx=PADDING, pady=(0, 12))
    self._active_title = ctk.CTkLabel(self._active_card, text="", font=body_small_font(), text_color=COLOR_TEXT)
    self._active_title.pack(anchor="w", padx=12, pady=(10, 0))
    self._active_sub = ctk.CTkLabel(self._active_card, text="", font=body_small_font(), text_color=COLOR_TEXT_MUTED)
    self._active_sub.pack(anchor="w", padx=12, pady=(0, 10))
    self._active_badge = ctk.CTkLabel(self._active_card, text="", font=body_small_font())
    self._active_badge.pack(anchor="e", padx=12, pady=(0, 10))
    self._refresh_active_card()

    tabs = ctk.CTkFrame(self, fg_color="transparent")
    tabs.pack(fill="x", padx=PADDING, pady=(0, 8))
    self._usb_tab_btn = ctk.CTkButton(
        tabs, text="USB Cameras", height=32, fg_color=COLOR_ACCENT, command=lambda: self._switch_tab("usb")
    )
    self._usb_tab_btn.pack(side="left", padx=(0, 6))
    self._ip_tab_btn = ctk.CTkButton(
        tabs, text="IP / RJ45 Cameras", height=32, fg_color=COLOR_BORDER, command=lambda: self._switch_tab("ip")
    )
    self._ip_tab_btn.pack(side="left")

    # Always-visible entry point — opens the "Add IP Camera" modal (no longer a buried
    # inline form). Packed before the list so it sits directly under the tabs and is
    # obvious on either tab. (Packed in order — no `before=` against the scrollable
    # list, which CustomTkinter wraps and can't be used as a pack reference.)
    self._add_ip_btn = ctk.CTkButton(
        self,
        text="+ Add IP Camera",
        height=34,
        fg_color="transparent",
        border_width=1,
        border_color=COLOR_BORDER,
        text_color=COLOR_ACCENT,
        hover_color=COLOR_BORDER,
        command=self._open_add_ip_modal,
    )
    self._add_ip_btn.pack(fill="x", padx=PADDING, pady=(0, 8))

    self._add_modal: ctk.CTkToplevel | None = None

    self._list_host = ctk.CTkScrollableFrame(self, fg_color="transparent", height=220)
    self._list_host.pack(fill="both", expand=True, padx=PADDING, pady=(0, 8))

    stream = ctk.CTkFrame(self, fg_color="transparent")
    stream.pack(fill="x", padx=PADDING, pady=(8, PADDING))
    ctk.CTkLabel(stream, text="Stream Settings", font=section_title_font(), text_color=COLOR_TEXT).pack(anchor="w", pady=(0, 8))

    form = ctk.CTkFrame(stream, fg_color="transparent")
    form.pack(fill="x")
    form.grid_columnconfigure(1, weight=1)

    ctk.CTkLabel(form, text="Resolution", font=body_small_font(), text_color=COLOR_TEXT_MUTED).grid(row=0, column=0, sticky="w", padx=(0, 12), pady=6)
    ctk.CTkComboBox(
        form,
        values=["640x480", "1280x720", "1920x1080"],
        variable=self._resolution_var,
        height=34,
    ).grid(row=0, column=1, sticky="ew", pady=6)

    fps_row = ctk.CTkFrame(form, fg_color="transparent")
    fps_row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=6)
    fps_row.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(fps_row, text="FPS Cap", font=body_small_font(), text_color=COLOR_TEXT_MUTED).grid(row=0, column=0, sticky="w", padx=(0, 12))
    self._fps_value = ctk.CTkLabel(fps_row, text="30", font=body_small_font(), text_color=COLOR_TEXT)
    self._fps_value.grid(row=0, column=2, sticky="e")

    def _on_fps(v: float) -> None:
      value = int(round(float(v)))
      self._fps_cap_var.set(value)
      self._fps_value.configure(text=str(value))

    slider = ctk.CTkSlider(fps_row, from_=10, to=60, number_of_steps=50, command=_on_fps)
    slider.set(30)
    slider.grid(row=0, column=1, sticky="ew")

    ctk.CTkButton(
        stream,
        text="Apply Stream Settings",
        height=34,
        fg_color=COLOR_BORDER,
        hover_color=COLOR_ACCENT_HOVER,
        command=self._apply_stream,
    ).pack(anchor="e", pady=(8, 0))

  def _switch_tab(self, tab: str) -> None:
    self._tab = tab
    if tab == "usb":
      self._usb_tab_btn.configure(fg_color=COLOR_ACCENT)
      self._ip_tab_btn.configure(fg_color=COLOR_BORDER)
    else:
      self._ip_tab_btn.configure(fg_color=COLOR_ACCENT)
      self._usb_tab_btn.configure(fg_color=COLOR_BORDER)
    self._render_list()

  def _refresh_active_card(self) -> None:
    active = self._active or get_camera_preference()
    if active and active.get("label"):
      self._active_title.configure(text=str(active["label"]))
      if active.get("type") in ("rj45", "ip"):
        self._active_sub.configure(text=redact(str(active.get("url", "")))[:80])
      else:
        self._active_sub.configure(text=f"USB index {active.get('index', 0)}")
      self._active_badge.configure(text="Connected", text_color=COLOR_SAFE)
    else:
      self._active_title.configure(text="No camera selected")
      self._active_sub.configure(text="Scan for cameras below and connect a source")
      self._active_badge.configure(text="Not connected", text_color=COLOR_TEXT_MUTED)

  def _run_scan(self) -> None:
    if self._scanning:
      return
    self._scanning = True
    self._scan_btn.configure(text="Scanning…", state="disabled")

    def _work() -> None:
      usb = scan_usb_cameras()
      items = list(usb)
      for cam in get_saved_ip_cameras():
        url = str(cam["url"])
        items.append(
            {
                "id": f"ip_{cam['id']}",
                "type": "rj45",
                "label": cam.get("label", "IP Camera"),
                "url": url,
                "status": "available" if test_ip_camera(url) else "unreachable",
            }
        )

      def _done() -> None:
        self._cameras = items
        self._scanning = False
        self._scan_btn.configure(text="Scan for Cameras", state="normal")
        self._render_list()

      if self.winfo_exists():
        self.after(0, _done)

    threading.Thread(target=_work, daemon=True).start()

  def _render_list(self) -> None:
    for child in self._list_host.winfo_children():
      child.destroy()

    filtered = [c for c in self._cameras if c.get("type") == ("usb" if self._tab == "usb" else "rj45")]
    if not filtered:
      msg = "No USB cameras detected. Try scanning again." if self._tab == "usb" else "No IP cameras added yet."
      ctk.CTkLabel(self._list_host, text=msg, font=body_small_font(), text_color=COLOR_TEXT_MUTED).pack(pady=24)
      return

    active_id = (self._active or {}).get("id")
    for cam in filtered:
      row = ctk.CTkFrame(self._list_host, fg_color=COLOR_BG, corner_radius=CORNER_RADIUS, border_width=1, border_color=COLOR_BORDER)
      row.pack(fill="x", pady=4)
      inner = ctk.CTkFrame(row, fg_color="transparent")
      inner.pack(fill="x", padx=10, pady=8)
      inner.grid_columnconfigure(0, weight=1)

      label = str(cam.get("label", "Camera"))
      ctk.CTkLabel(inner, text=label, font=body_small_font(), text_color=COLOR_TEXT).grid(row=0, column=0, sticky="w")
      if cam.get("url"):
        ctk.CTkLabel(inner, text=redact(str(cam["url"]))[:60], font=body_small_font(), text_color=COLOR_TEXT_MUTED).grid(
            row=1, column=0, sticky="w"
        )
      status = str(cam.get("status", ""))
      color = COLOR_SAFE if status == "available" else COLOR_DANGER if status == "unreachable" else COLOR_TEXT_MUTED
      ctk.CTkLabel(inner, text=status, font=body_small_font(), text_color=color).grid(row=0, column=1, padx=8)

      cam_id = str(cam.get("id", ""))
      if cam_id == active_id:
        ctk.CTkLabel(inner, text="● Active", font=body_small_font(), text_color=COLOR_SAFE).grid(row=0, column=2)
      else:
        state = "normal" if status == "available" else "disabled"
        ctk.CTkButton(
            inner,
            text="Connect",
            width=80,
            height=28,
            state=state,
            fg_color=COLOR_ACCENT,
            hover_color=COLOR_ACCENT_HOVER,
            command=lambda c=cam: self._connect(c),
        ).grid(row=0, column=2)

        if cam.get("type") == "rj45":
          ctk.CTkButton(
              inner,
              text="Delete",
              width=70,
              height=28,
              fg_color=COLOR_BORDER,
              hover_color=COLOR_DANGER,
              command=lambda c=cam: self._delete_ip(c),
          ).grid(row=0, column=3, padx=(6, 0))

  def _connect(self, cam: dict[str, Any]) -> None:
    try:
      result = camera_manager.select(
          {
              "id": cam.get("id"),
              "type": cam.get("type"),
              "index": cam.get("index"),
              "label": cam.get("label"),
              "url": cam.get("url"),
          }
      )
    except ValueError as exc:
      show_toast(self, str(exc), type="error")
      return
    if not result.get("success"):
      show_toast(self, result.get("message", "Failed to connect"), type="error")
      return
    self._active = result.get("active")
    self._refresh_active_card()
    if self._active:
      self._on_camera_connected(self._active)
      show_toast(self, f"Connected to {self._active.get('label', 'camera')}", type="success")

  def _delete_ip(self, cam: dict[str, Any]) -> None:
    raw = str(cam.get("id", "")).removeprefix("ip_")
    if delete_ip_camera(raw):
      camera_manager.clear_active_if(str(cam.get("id", "")))
      if (self._active or {}).get("id") == cam.get("id"):
        self._active = None
      self._run_scan()
      self._refresh_active_card()

  # ------------------------------------------------------------------
  # Add IP Camera modal
  # ------------------------------------------------------------------

  def _open_add_ip_modal(self) -> None:
    """Open a focused dialog to add an IP camera (auto-detect or add manually)."""
    if self._add_modal is not None and self._add_modal.winfo_exists():
      self._add_modal.focus()
      return

    modal = ctk.CTkToplevel(self)
    self._add_modal = modal
    modal.title("Add IP Camera")
    modal.configure(fg_color=COLOR_BG)
    modal.geometry("440x520")
    modal.resizable(False, False)
    modal.transient(self.winfo_toplevel())

    def _close() -> None:
      try:
        modal.grab_release()
      except Exception:
        pass
      self._add_modal = None
      modal.destroy()

    modal.protocol("WM_DELETE_WINDOW", _close)

    body = ctk.CTkFrame(modal, fg_color="transparent")
    body.pack(fill="both", expand=True, padx=18, pady=16)

    ctk.CTkLabel(body, text="Add IP Camera", font=section_title_font(), text_color=COLOR_TEXT).pack(anchor="w")
    ctk.CTkLabel(
        body, text="Enter the camera's IP and login. We'll auto-detect the stream,\n"
                   "or add it manually and connect later.",
        font=body_small_font(), text_color=COLOR_TEXT_MUTED, justify="left",
    ).pack(anchor="w", pady=(2, 10))

    label_entry = ctk.CTkEntry(body, placeholder_text="Entrance Camera 1")
    host_entry = ctk.CTkEntry(body, placeholder_text="192.168.1.108")
    user_entry = ctk.CTkEntry(body, placeholder_text="admin")
    pass_entry = ctk.CTkEntry(body, placeholder_text="password", show="•")
    url_entry = ctk.CTkEntry(body, placeholder_text="(optional) rtsp://…/full-url  or  /custom/path")

    def _field(text: str, widget: ctk.CTkEntry) -> None:
      ctk.CTkLabel(body, text=text, font=body_small_font(), text_color=COLOR_TEXT_MUTED).pack(anchor="w", pady=(8, 0))
      widget.pack(fill="x", pady=(2, 0))

    _field("Label", label_entry)
    _field("IP address / host", host_entry)
    _field("Username", user_entry)
    _field("Password", pass_entry)
    # Most users leave this blank. The camera's web-page address (http://host) is NOT a
    # stream and is ignored — only an rtsp:// URL or a stream path is used here.
    _field("Stream URL (optional) — blank to auto-detect; advanced: rtsp://… only", url_entry)

    status = ctk.CTkLabel(body, text="", font=body_small_font(), text_color=COLOR_TEXT_MUTED, wraplength=400, justify="left")
    status.pack(anchor="w", pady=(12, 4))

    btns = ctk.CTkFrame(body, fg_color="transparent")
    btns.pack(fill="x", pady=(6, 0))
    test_btn = ctk.CTkButton(btns, text="Test & Add", width=120, fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER)
    test_btn.pack(side="left")
    manual_btn = ctk.CTkButton(btns, text="Add Manually", width=120, fg_color=COLOR_BORDER, hover_color=COLOR_ACCENT_HOVER)
    manual_btn.pack(side="left", padx=8)
    ctk.CTkButton(btns, text="Cancel", width=80, fg_color="transparent", border_width=1,
                  border_color=COLOR_BORDER, hover_color=COLOR_BORDER, command=_close).pack(side="right")

    def _set_status(msg: str, color: str = COLOR_TEXT_MUTED) -> None:
      try:
        if modal.winfo_exists():
          modal.after(0, lambda: status.configure(text=msg, text_color=color))
      except Exception:
        pass

    def _persist_and_connect(label: str, url: str, *, connect: bool) -> None:
      """Save the camera and (optionally) connect. Runs on the UI thread."""
      try:
        entry = add_ip_camera(label, url)
      except ValueError as exc:
        show_toast(self, str(exc), type="error")
        return
      self._switch_tab("ip")
      if connect:
        result = camera_manager.select(
            {"id": f"ip_{entry['id']}", "type": "rj45", "label": label, "url": url}
        )
        if result.get("success"):
          self._active = result.get("active")
          self._on_camera_connected(self._active)
          show_toast(self, f"Connected — {label}  ({redact(url)})", type="success", duration=5000)
        else:
          show_toast(self, "Saved, but couldn't connect yet. Use Connect in the list to retry.",
                     type="warning", duration=5000)
      else:
        show_toast(self, f"Added “{label}”. Use Connect in the list when ready.",
                   type="success", duration=4000)
      self._run_scan()
      self._refresh_active_card()
      _close()

    def _on_test() -> None:
      host = host_entry.get().strip()
      if not host:
        _set_status("Enter the camera IP address.", COLOR_DANGER)
        return
      user, pwd = user_entry.get().strip(), pass_entry.get()
      override = url_entry.get().strip() or None
      label = label_entry.get().strip() or "IP Camera"
      test_btn.configure(state="disabled", text="Detecting…")
      manual_btn.configure(state="disabled")
      _set_status("Scanning ports & probing streams…")

      def _work() -> None:
        url = probe_ip_camera(host, user, pwd, path=override, on_progress=_set_status)

        def _done() -> None:
          if not modal.winfo_exists():
            return
          test_btn.configure(state="normal", text="Test & Add")
          manual_btn.configure(state="normal")
          if not url:
            _set_status("No stream found. Check IP/credentials, paste a full rtsp:// URL "
                        "above, or use “Add Manually”.", COLOR_DANGER)
            return
          _persist_and_connect(label, url, connect=True)

        try:
          if modal.winfo_exists():
            modal.after(0, _done)
        except Exception:
          pass

      threading.Thread(target=_work, daemon=True).start()

    def _on_manual() -> None:
      host = host_entry.get().strip()
      if not host:
        _set_status("Enter the camera IP address.", COLOR_DANGER)
        return
      user, pwd = user_entry.get().strip(), pass_entry.get()
      override = url_entry.get().strip() or None
      label = label_entry.get().strip() or "IP Camera"
      candidates = build_candidate_urls(host, user, pwd, path=override)
      if not candidates:
        _set_status("Couldn't build a stream URL from that address.", COLOR_DANGER)
        return
      _persist_and_connect(label, candidates[0], connect=False)

    test_btn.configure(command=_on_test)
    manual_btn.configure(command=_on_manual)

    modal.after(120, lambda: (modal.lift(), modal.grab_set(), host_entry.focus()))

  def _apply_stream(self) -> None:
    res_raw = (self._resolution_var.get() or "1280x720").strip().lower()
    mapping = {"640x480": (640, 480), "1280x720": (1280, 720), "1920x1080": (1920, 1080)}
    res = mapping.get(res_raw, (1280, 720))
    fps = max(10, min(60, int(self._fps_cap_var.get() or 30)))
    idx = int((self._active or {}).get("index", 0) or 0)
    try:
      self._apply_stream_settings(idx, res, fps)
    except Exception as exc:
      show_toast(self, f"Failed to apply stream settings: {exc}", type="error")
