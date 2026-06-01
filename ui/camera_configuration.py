"""Camera Configuration section for Settings (USB + IP/RJ45 sources)."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import customtkinter as ctk

from api.camera_manager import camera_manager, scan_usb_cameras, test_ip_camera
from api.camera_store import add_ip_camera, delete_ip_camera, get_camera_preference, get_saved_ip_cameras
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

    self._list_host = ctk.CTkScrollableFrame(self, fg_color="transparent", height=220)
    self._list_host.pack(fill="both", expand=True, padx=PADDING, pady=(0, 8))

    self._add_ip_btn = ctk.CTkButton(
        self,
        text="+ Add IP Camera",
        height=34,
        fg_color="transparent",
        border_width=1,
        border_color=COLOR_BORDER,
        text_color=COLOR_ACCENT,
        hover_color=COLOR_BORDER,
        command=self._toggle_add_ip_form,
    )

    self._ip_form = ctk.CTkFrame(self, fg_color=COLOR_BG, corner_radius=CORNER_RADIUS)
    self._ip_label_entry = ctk.CTkEntry(self._ip_form, placeholder_text="Entrance Camera 1")
    self._ip_url_entry = ctk.CTkEntry(self._ip_form, placeholder_text="rtsp://192.168.1.x:554/stream")
    self._ip_test_label = ctk.CTkLabel(self._ip_form, text="", font=body_small_font())
    self._ip_test_ok = False
    self._ip_form_visible = False
    ctk.CTkLabel(self._ip_form, text="Label", font=body_small_font(), text_color=COLOR_TEXT_MUTED).pack(
        anchor="w", padx=10, pady=(8, 0)
    )
    self._ip_label_entry.pack(fill="x", padx=10, pady=4)
    ctk.CTkLabel(self._ip_form, text="Stream URL", font=body_small_font(), text_color=COLOR_TEXT_MUTED).pack(anchor="w", padx=10)
    self._ip_url_entry.pack(fill="x", padx=10, pady=4)
    ip_btns = ctk.CTkFrame(self._ip_form, fg_color="transparent")
    ip_btns.pack(fill="x", padx=10, pady=8)
    ctk.CTkButton(ip_btns, text="Test", width=70, command=self._test_ip).pack(side="left")
    self._ip_test_label.pack(side="left", padx=8)
    ctk.CTkButton(ip_btns, text="Save", width=70, fg_color=COLOR_ACCENT, command=self._save_ip).pack(side="left", padx=6)
    ctk.CTkButton(ip_btns, text="Cancel", width=70, fg_color=COLOR_BORDER, command=self._hide_ip_form).pack(side="left")

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
      self._add_ip_btn.pack_forget()
      if self._ip_form_visible:
        self._ip_form.pack_forget()
    else:
      self._ip_tab_btn.configure(fg_color=COLOR_ACCENT)
      self._usb_tab_btn.configure(fg_color=COLOR_BORDER)
      self._add_ip_btn.pack(fill="x", padx=PADDING, pady=(0, 8))
    self._render_list()

  def _refresh_active_card(self) -> None:
    active = self._active or get_camera_preference()
    if active and active.get("label"):
      self._active_title.configure(text=str(active["label"]))
      if active.get("type") in ("rj45", "ip"):
        self._active_sub.configure(text=str(active.get("url", ""))[:80])
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
        ctk.CTkLabel(inner, text=str(cam["url"])[:60], font=body_small_font(), text_color=COLOR_TEXT_MUTED).grid(
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

  def _hide_ip_form(self) -> None:
    self._ip_form.pack_forget()
    self._ip_form_visible = False
    self._ip_test_ok = False
    self._ip_test_label.configure(text="")

  def _toggle_add_ip_form(self) -> None:
    if self._ip_form_visible:
      self._hide_ip_form()
      return
    self._ip_form_visible = True
    self._ip_form.pack(fill="x", padx=PADDING, pady=(0, 8))

  def _test_ip(self) -> None:
    url = self._ip_url_entry.get().strip()
    if not url:
      return
    ok = test_ip_camera(url)
    self._ip_test_label.configure(
        text="Reachable" if ok else "Unreachable",
        text_color=COLOR_SAFE if ok else COLOR_DANGER,
    )
    self._ip_test_ok = ok

  def _save_ip(self) -> None:
    if not getattr(self, "_ip_test_ok", False):
      show_toast(self, "Test the connection before saving.", type="warning")
      return
    label = self._ip_label_entry.get().strip()
    url = self._ip_url_entry.get().strip()
    if not label or not url:
      show_toast(self, "Label and URL are required.", type="warning")
      return
    try:
      add_ip_camera(label, url)
    except ValueError as exc:
      show_toast(self, str(exc), type="error")
      return
    self._hide_ip_form()
    self._switch_tab("ip")
    self._run_scan()
    show_toast(self, "IP camera saved.", type="success")

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
