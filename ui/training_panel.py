"""In-app training panel for CBVMS — build datasets and train YOLOv8 classifiers."""

from __future__ import annotations

import math
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from tkinter import filedialog
from typing import Callable

import cv2
import customtkinter as ctk
import numpy as np
from PIL import Image, ImageOps

# HEIC/HEIF is the default iPhone/macOS photo format. pillow-heif registers a HEIF
# opener into Pillow so Image.open() can decode it; guard the import so the app still
# runs (just without HEIC) if the optional dependency is missing.
try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
    _HEIC_OK = True
except Exception:
    _HEIC_OK = False

_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
if _HEIC_OK:
    _IMAGE_EXTS += (".heic", ".heif")

from core.trainer import MIN_SAMPLES_PER_CLASS, MODULES, ViolationTrainer
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
    ROW_STRIPE_ODD,
    MirrorController,
    body_font,
    body_small_font,
    heading_font,
    make_mirror_button,
    panel_title_font,
    show_toast,
)

# Confusion-matrix tile backgrounds (dark green/red approximating low-opacity SAFE/DANGER)
_TILE_GREEN = "#13261F"
_TILE_RED = "#2A1518"

_MODULE_TABS = [
    ("uniform", "Uniform Check"),
    ("earring", "Earring Check"),
]

_HELP_TEXT = (
    "Photos you upload or capture build the reference set for each check.\n\n"
    f"Minimum {MIN_SAMPLES_PER_CLASS} photos per class is recommended. "
    "More diverse photos (different angles, lighting, people) = better accuracy.\n\n"
    "UNIFORM: live detection uses a pretrained-image-embedding 'fingerprint' built "
    "automatically from your CORRECT-uniform photos (no gradient training needed — just add "
    "good correct photos and it rebuilds), cross-checked by a uniform-colour match.\n"
    "IMPORTANT: reference photos must look like the LIVE view — a CHEST/TORSO close-up of the "
    "shirt (like a webcam selfie), NOT full-body shots where the uniform is tiny and the "
    "background fills the frame. The most reliable way is 'Capture from Camera' here, framed on "
    "your chest. A few dozen good chest-framed photos beat hundreds of full-body ones. A few "
    "'wrong uniform' photos help calibrate the threshold.\n\n"
    "EARRING: trains a YOLOv8 image classifier — provide a BALANCED set (roughly as many "
    "'with' as 'without' photos). 'Evaluate Model' tests on held-out photos the model never "
    "trained on, so its accuracy reflects real-world performance. Everything runs on your "
    "machine."
)


def _pretty(label: str) -> str:
    return label.replace("_", " ").title()


def _gather_images(folder: str) -> list[str]:
    """Recursively collect image files (case-insensitive) under a folder."""
    root = Path(folder)
    if not root.is_dir():
        return []
    return sorted(
        str(p) for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in _IMAGE_EXTS
    )


def _load_bgr(path) -> np.ndarray | None:
    """Decode an image file to a BGR ndarray.

    HEIC/HEIF go through Pillow (OpenCV has no HEIF codec); EXIF orientation is applied
    so iPhone photos aren't sideways. Every other format keeps the fast cv2.imread path.
    Returns None if the file can't be decoded.
    """
    if str(path).lower().endswith((".heic", ".heif")):
        try:
            with Image.open(path) as im:
                im = ImageOps.exif_transpose(im).convert("RGB")
                return cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)
        except Exception:
            return None
    return cv2.imread(str(path))


class TrainingPanel(ctk.CTkFrame):
    """Two-tab training UI (Uniform / Earring) with dataset builder + trainer."""

    def __init__(
        self,
        master,
        *,
        trainer: ViolationTrainer,
        get_frame: Callable[[], np.ndarray | None],
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color=COLOR_BG, **kwargs)
        self.trainer = trainer
        self.get_frame = get_frame

        # Per-module widget references and runtime state
        self._ui: dict[str, dict] = {}
        self._thumb_images: dict[tuple[str, str], list] = {}
        self._training: dict[str, bool] = {}
        self._evaluating: dict[str, bool] = {}

        # In-panel "Capture from Camera" screen (replaces the old modal)
        self._tabview: ctk.CTkTabview | None = None
        self._capture_screen: ctk.CTkFrame | None = None
        self._capture_state: dict | None = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build(self) -> None:
        tabview = ctk.CTkTabview(
            self,
            fg_color=COLOR_BG,
            segmented_button_fg_color=COLOR_SURFACE,
            segmented_button_selected_color=COLOR_ACCENT,
            segmented_button_selected_hover_color=COLOR_ACCENT_HOVER,
            segmented_button_unselected_color=COLOR_SURFACE,
            segmented_button_unselected_hover_color=COLOR_BORDER,
            text_color=COLOR_TEXT,
        )
        tabview.grid(row=0, column=0, sticky="nsew")
        self._tabview = tabview

        for module, title in _MODULE_TABS:
            tab = tabview.add(title)
            self._ui[module] = {}
            self._training[module] = False
            self._build_module_tab(tab, module)
            self._refresh_counts(module)

    def _build_module_tab(self, tab, module: str) -> None:
        tab.grid_columnconfigure(0, weight=2)
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(0, weight=1)

        # LEFT — dataset builder (two class columns)
        left = ctk.CTkFrame(tab, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, PADDING), pady=PADDING)
        left.grid_rowconfigure(0, weight=1)
        labels = MODULES[module]["labels"]
        for i in range(len(labels)):
            left.grid_columnconfigure(i, weight=1, uniform="cls")
        for col, label in enumerate(labels):
            self._build_class_column(left, module, label, col)

        # RIGHT — training controls (scrollable: results card can be tall)
        right = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", pady=PADDING)
        self._build_training_controls(right, module)

    def _build_class_column(self, parent, module: str, label: str, col: int) -> None:
        frame = ctk.CTkFrame(
            parent, fg_color=COLOR_SURFACE, corner_radius=CORNER_RADIUS,
            border_width=1, border_color=COLOR_BORDER,
        )
        frame.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else PADDING // 2, 0))
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)

        # Header: class name + count badge
        header = ctk.CTkFrame(frame, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=PADDING, pady=(PADDING, 8))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header, text=_pretty(label), font=body_font(14), text_color=COLOR_TEXT,
        ).grid(row=0, column=0, sticky="w")
        count_badge = ctk.CTkLabel(
            header, text="0 photos", font=body_small_font(), text_color=COLOR_TEXT,
            fg_color=COLOR_ACCENT, corner_radius=999, padx=8, pady=2,
        )
        count_badge.grid(row=0, column=1, sticky="e")

        # Thumbnail grid (3 columns)
        thumbs = ctk.CTkScrollableFrame(
            frame, fg_color=COLOR_BG, corner_radius=CORNER_RADIUS, height=170,
        )
        thumbs.grid(row=1, column=0, sticky="nsew", padx=PADDING, pady=(0, 8))
        for c in range(3):
            thumbs.grid_columnconfigure(c, weight=1)

        # Action buttons
        btns = ctk.CTkFrame(frame, fg_color="transparent")
        btns.grid(row=2, column=0, sticky="ew", padx=PADDING)
        btns.grid_columnconfigure(0, weight=1)

        upload_btn = ctk.CTkButton(
            btns, text="📁  Upload Photos", height=34, corner_radius=CORNER_RADIUS,
            fg_color=COLOR_BORDER, hover_color=COLOR_ACCENT_HOVER, font=body_small_font(),
            command=lambda: self._upload(module, label),
        )
        upload_btn.grid(row=0, column=0, sticky="ew", pady=2)

        folder_btn = ctk.CTkButton(
            btns, text="📂  Import Folder", height=34, corner_radius=CORNER_RADIUS,
            fg_color=COLOR_BORDER, hover_color=COLOR_ACCENT_HOVER, font=body_small_font(),
            command=lambda: self._import_folder(module, label),
        )
        folder_btn.grid(row=1, column=0, sticky="ew", pady=2)

        capture_btn = ctk.CTkButton(
            btns, text="📷  Capture from Camera", height=34, corner_radius=CORNER_RADIUS,
            fg_color=COLOR_BORDER, hover_color=COLOR_ACCENT_HOVER, font=body_small_font(),
            command=lambda: self._open_capture_screen(module, label),
        )
        capture_btn.grid(row=2, column=0, sticky="ew", pady=2)

        # Read-only navigation — opens the on-disk save folder for this class.
        open_btn = ctk.CTkButton(
            btns, text="🗂  Open Save Folder", height=34, corner_radius=CORNER_RADIUS,
            fg_color=COLOR_BORDER, hover_color=COLOR_ACCENT_HOVER, font=body_small_font(),
            command=lambda: self._open_save_folder(module, label),
        )
        open_btn.grid(row=3, column=0, sticky="ew", pady=2)

        clear_btn = ctk.CTkButton(
            btns, text="🗑  Clear All", height=34, corner_radius=CORNER_RADIUS,
            fg_color=COLOR_DANGER, hover_color="#DC2626", font=body_small_font(),
            command=lambda: self._clear(module, label),
        )
        clear_btn.grid(row=4, column=0, sticky="ew", pady=2)

        ctk.CTkLabel(
            frame, text=f"Min. {MIN_SAMPLES_PER_CLASS} photos required to train",
            font=body_small_font(), text_color=COLOR_TEXT_MUTED,
        ).grid(row=5, column=0, sticky="w", padx=PADDING, pady=(6, PADDING))

        self._ui[module][label] = {
            "count_badge": count_badge,
            "thumbs": thumbs,
            "buttons": [upload_btn, folder_btn, capture_btn, clear_btn],
        }
        self._refresh_thumbnails(module, label)

    def _build_training_controls(self, right, module: str) -> None:
        right.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            right, text="Train Model", font=panel_title_font(), text_color=COLOR_TEXT,
        ).grid(row=0, column=0, sticky="w", pady=(0, PADDING))

        # Status card
        status_card = ctk.CTkFrame(
            right, fg_color=COLOR_SURFACE, corner_radius=CORNER_RADIUS,
            border_width=1, border_color=COLOR_BORDER,
        )
        status_card.grid(row=1, column=0, sticky="ew", pady=(0, PADDING))
        status_card.grid_columnconfigure(0, weight=1)

        status_badge = ctk.CTkLabel(
            status_card, text="Not Trained", font=body_small_font(), text_color=COLOR_TEXT,
            fg_color=COLOR_DANGER, corner_radius=999, padx=10, pady=4,
        )
        status_badge.grid(row=0, column=0, sticky="w", padx=PADDING, pady=(PADDING, 8))

        counts_label = ctk.CTkLabel(
            status_card, text="", font=body_small_font(),
            text_color=COLOR_TEXT_MUTED, justify="left",
        )
        counts_label.grid(row=1, column=0, sticky="w", padx=PADDING, pady=(0, 4))

        trained_label = ctk.CTkLabel(
            status_card, text="", font=body_small_font(), text_color=COLOR_TEXT_MUTED,
        )
        trained_label.grid(row=2, column=0, sticky="w", padx=PADDING, pady=(0, PADDING))

        progress_label = ctk.CTkLabel(
            right, text="", font=body_small_font(), text_color=COLOR_ACCENT,
        )
        progress_label.grid(row=2, column=0, sticky="w", pady=(0, 4))

        progress_bar = ctk.CTkProgressBar(right, mode="indeterminate")
        progress_bar.grid(row=3, column=0, sticky="ew", pady=(0, PADDING))
        progress_bar.set(0)

        train_btn = ctk.CTkButton(
            right, text="Train Now", height=44, corner_radius=CORNER_RADIUS,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, font=body_font(14),
            command=lambda: self._start_training(module),
        )
        train_btn.grid(row=4, column=0, sticky="ew", pady=(0, 8))

        # Evaluate Model (outline button) — disabled until trained
        evaluate_btn = ctk.CTkButton(
            right, text="Evaluate Model", height=40, corner_radius=12,
            fg_color="transparent", border_width=1, border_color=COLOR_ACCENT,
            text_color=COLOR_ACCENT, hover_color=COLOR_BORDER, font=body_font(14),
            command=lambda: self._start_evaluation(module), state="disabled",
        )
        evaluate_btn.grid(row=5, column=0, sticky="ew", pady=(0, PADDING))

        # Collapsible help
        help_btn = ctk.CTkButton(
            right, text="How does this work?  ▾", anchor="w", height=30,
            corner_radius=CORNER_RADIUS, fg_color="transparent", hover_color=COLOR_BORDER,
            text_color=COLOR_TEXT_MUTED, font=body_small_font(),
            command=lambda: self._toggle_help(module),
        )
        help_btn.grid(row=6, column=0, sticky="ew")

        help_box = ctk.CTkTextbox(
            right, height=130, fg_color=COLOR_BG, border_color=COLOR_BORDER,
            border_width=1, corner_radius=CORNER_RADIUS, text_color=COLOR_TEXT_MUTED,
            font=body_small_font(), wrap="word",
        )
        help_box.insert("1.0", _HELP_TEXT)
        help_box.configure(state="disabled")
        help_box.grid(row=7, column=0, sticky="ew", pady=(4, 0))
        help_box.grid_remove()

        # Evaluation results card (hidden until an evaluation completes)
        results_card = ctk.CTkFrame(
            right, fg_color=COLOR_SURFACE, corner_radius=16,
            border_width=1, border_color=COLOR_BORDER,
        )
        results_card.grid(row=8, column=0, sticky="ew", pady=(PADDING, 0))
        results_card.grid_columnconfigure(0, weight=1)
        results_card.grid_remove()

        self._ui[module]["controls"] = {
            "status_badge": status_badge,
            "counts_label": counts_label,
            "trained_label": trained_label,
            "progress_label": progress_label,
            "progress_bar": progress_bar,
            "train_btn": train_btn,
            "evaluate_btn": evaluate_btn,
            "results_card": results_card,
            "help_btn": help_btn,
            "help_box": help_box,
            "help_visible": False,
        }

    # ------------------------------------------------------------------
    # Refresh helpers
    # ------------------------------------------------------------------

    def _refresh_thumbnails(self, module: str, label: str) -> None:
        info = self._ui[module][label]
        frame = info["thumbs"]
        for child in frame.winfo_children():
            child.destroy()

        images: list = []
        for i, path in enumerate(self.trainer.list_samples(module, label, limit=12)):
            try:
                img = Image.open(path).convert("RGB")
                ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(64, 64))
                images.append(ctk_img)
                ctk.CTkLabel(frame, text="", image=ctk_img).grid(
                    row=i // 3, column=i % 3, padx=4, pady=4,
                )
            except Exception:
                continue
        self._thumb_images[(module, label)] = images  # keep refs alive

    def _refresh_counts(self, module: str) -> None:
        counts = self.trainer.get_sample_counts(module)
        for label, count in counts.items():
            self._ui[module][label]["count_badge"].configure(text=f"{count} photos")

        controls = self._ui[module]["controls"]
        base = "   ".join(f"{_pretty(l)}: {c}" for l, c in counts.items())
        # Warn on class imbalance — a lopsided dataset (e.g. 563 vs 11) trains a model
        # that always predicts the majority class. Recommend balancing.
        vals = list(counts.values())
        hi, lo = (max(vals), min(vals)) if vals else (0, 0)
        imbalanced = lo >= 1 and hi >= lo * 3
        if imbalanced:
            minority = min(counts, key=counts.get)
            controls["counts_label"].configure(
                text=f"{base}\n⚠ Unbalanced — add more “{_pretty(minority)}” photos "
                     f"(aim for similar counts) for accurate detection.",
                text_color=COLOR_WARNING,
            )
        else:
            controls["counts_label"].configure(text=base, text_color=COLOR_TEXT_MUTED)

        trained = self.trainer.is_trained(module)
        if trained:
            controls["status_badge"].configure(text="Ready ✓", fg_color=COLOR_SAFE)
            mtime = self.trainer.model_mtime(module)
            if mtime:
                ts = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
                controls["trained_label"].configure(text=f"Last trained: {ts}")
        else:
            controls["status_badge"].configure(text="Not Trained", fg_color=COLOR_DANGER)
            controls["trained_label"].configure(text="")
        controls["evaluate_btn"].configure(state="normal" if trained else "disabled")

    # ------------------------------------------------------------------
    # Dataset actions
    # ------------------------------------------------------------------

    def _upload(self, module: str, label: str) -> None:
        # Per-extension filters + an All-files fallback — the macOS native panel is
        # unreliable with a single space-separated multi-extension pattern.
        filetypes = [
            ("JPEG image", "*.jpg"), ("JPEG image", "*.jpeg"),
            ("PNG image", "*.png"), ("BMP image", "*.bmp"),
            ("WebP image", "*.webp"),
        ]
        if _HEIC_OK:
            filetypes += [("HEIC image", "*.heic"), ("HEIF image", "*.heif")]
        filetypes.append(("All files", "*"))
        raw = filedialog.askopenfilenames(
            title="Select photos (Cmd/Shift-click for multiple)",
            filetypes=filetypes,
        )
        # askopenfilenames may return a tuple or a Tcl-list string — splitlist handles both.
        paths = [p for p in self.tk.splitlist(raw) if str(p).lower().endswith(_IMAGE_EXTS)]
        if not paths:
            return
        self._add_paths_bg(module, label, paths)

    def _import_folder(self, module: str, label: str) -> None:
        folder = filedialog.askdirectory(title="Select a folder of photos")
        if not folder:
            return
        paths = _gather_images(folder)
        if not paths:
            show_toast(self, "No images found in that folder.", type="warning")
            return
        self._add_paths_bg(module, label, paths)

    def _open_save_folder(self, module: str, label: str) -> None:
        """Open this class's on-disk save folder in the OS file manager."""
        folder = self.trainer.label_dir(module, label)
        try:
            folder.mkdir(parents=True, exist_ok=True)  # may not exist until first photo
            path = str(folder)
            if sys.platform == "darwin":
                subprocess.Popen(["open", path])
            elif os.name == "nt":
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:
            show_toast(self, f"Could not open folder: {exc}", type="error")

    def _add_paths_bg(self, module: str, label: str, paths: list[str]) -> None:
        """Read + add many image files on a worker thread so the UI stays responsive."""
        total = len(paths)
        self._set_buttons_state(module, False)
        show_toast(self, f"Importing {total} photo(s) to {_pretty(label)}…", type="info")

        def _safe_after(func, *args) -> None:
            try:
                if self.winfo_exists():
                    self.after(0, func, *args)
            except Exception:
                pass

        def _work() -> None:
            added = 0
            for p in paths:
                try:
                    img = _load_bgr(p)
                    if img is None:
                        continue
                    self.trainer.add_sample(module, label, img)
                    added += 1
                except Exception:
                    continue
            _safe_after(self._on_import_done, module, label, added, total)

        threading.Thread(target=_work, daemon=True).start()

    def _on_import_done(self, module: str, label: str, added: int, total: int) -> None:
        self._set_buttons_state(module, True)
        self._refresh_thumbnails(module, label)
        self._refresh_counts(module)
        skipped = total - added
        msg = f"Added {added} photo(s) to {_pretty(label)}."
        if skipped:
            msg += f" Skipped {skipped} unreadable."
        show_toast(self, msg, type="success", duration=4000)

    # ------------------------------------------------------------------
    # In-panel "Capture from Camera" (replaces the pop-up modal)
    # ------------------------------------------------------------------

    _PREVIEW_W = 640
    _PREVIEW_H = 360
    _RECENT_MAX = 12

    def _open_capture_screen(self, module: str, label: str) -> None:
        """Take over the panel center with a live camera + capture controls (no modal)."""
        if self._capture_screen is not None:
            return  # already capturing
        if self._tabview is not None:
            self._tabview.grid_remove()

        screen = ctk.CTkFrame(self, fg_color=COLOR_BG)
        screen.grid(row=0, column=0, sticky="nsew")
        screen.grid_columnconfigure(0, weight=1)
        screen.grid_rowconfigure(1, weight=1)
        self._capture_screen = screen

        state: dict = {
            "module": module, "label": label, "mode": "live",
            "preview_job": None, "img": None, "mirror": MirrorController(),
            "buffer": [], "strip_cells": [],
            "timer_secs": 3, "countdown": None, "flash_until": 0.0, "auto": None,
        }
        self._capture_state = state

        header = ctk.CTkFrame(screen, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=PADDING, pady=(PADDING, 8))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header, text=f"📷  Capture — {_pretty(label)}",
            font=panel_title_font(), text_color=COLOR_TEXT,
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            header, text="← Back", width=110, height=34, corner_radius=CORNER_RADIUS,
            fg_color=COLOR_BORDER, hover_color=COLOR_ACCENT_HOVER, font=body_small_font(),
            command=self._on_back,
        ).grid(row=0, column=1, sticky="e")

        body = ctk.CTkFrame(screen, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)
        state["body"] = body

        col = ctk.CTkFrame(body, fg_color="transparent")
        col.grid(row=0, column=0)  # centered in the body cell
        col.grid_columnconfigure(0, weight=1)
        state["col"] = col

        # Live preview with an overlaid mirror toggle (display-only flip + animation).
        preview_wrap = ctk.CTkFrame(col, fg_color="transparent")
        preview_wrap.grid(row=0, column=0)
        preview = ctk.CTkLabel(
            preview_wrap, text="Starting camera…", width=self._PREVIEW_W, height=self._PREVIEW_H,
            fg_color=COLOR_BG, text_color=COLOR_TEXT_MUTED, corner_radius=10,
        )
        preview.grid(row=0, column=0)
        state["preview"] = preview
        mirror_btn = make_mirror_button(preview_wrap, state["mirror"])
        mirror_btn.place(in_=preview, relx=1.0, x=-10, y=10, anchor="ne")
        mirror_btn.lift()

        count_lbl = ctk.CTkLabel(col, text="Captured: 0 (unsaved)", font=body_small_font(),
                                 text_color=COLOR_SAFE)
        count_lbl.grid(row=1, column=0, sticky="w", pady=(8, 0))
        state["count_lbl"] = count_lbl

        # Controls (capture timer + single shot + auto-capture).
        controls = ctk.CTkFrame(col, fg_color="transparent")
        controls.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        controls.grid_columnconfigure(0, weight=1)
        state["controls"] = controls
        self._build_capture_live_controls(state)

        # Running strip of captured (still-unsaved) thumbnails.
        strip_card = ctk.CTkFrame(col, fg_color=COLOR_SURFACE, corner_radius=CORNER_RADIUS,
                                  border_width=1, border_color=COLOR_BORDER)
        strip_card.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        strip_card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(strip_card, text="Captured photos  ·  not saved yet",
                     font=body_small_font(), text_color=COLOR_TEXT_MUTED).grid(
            row=0, column=0, sticky="w", padx=12, pady=(10, 4))
        strip = ctk.CTkScrollableFrame(strip_card, fg_color="transparent",
                                       orientation="horizontal", height=84)
        strip.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 10))
        state["strip"] = strip
        state["empty_lbl"] = ctk.CTkLabel(strip, text="No photos captured yet.",
                                          font=body_small_font(), text_color=COLOR_TEXT_MUTED)
        state["empty_lbl"].grid(row=0, column=0, padx=8, pady=8)

        review_btn = ctk.CTkButton(
            col, text="Review & Save (0)", height=40, corner_radius=CORNER_RADIUS,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, font=body_font(14),
            state="disabled", command=lambda: self._show_review(state),
        )
        review_btn.grid(row=4, column=0, sticky="ew", pady=(10, PADDING))
        state["review_btn"] = review_btn

        self._capture_tick(state)

    def _build_capture_live_controls(self, state: dict) -> None:
        controls = state["controls"]
        for w in controls.winfo_children():
            w.destroy()

        # Capture timer (countdown length) — modern slider with min/max ends + a value pill.
        timer_card = ctk.CTkFrame(controls, fg_color=COLOR_SURFACE, corner_radius=CORNER_RADIUS,
                                  border_width=1, border_color=COLOR_BORDER)
        timer_card.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        timer_card.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(timer_card, text="Capture timer", font=body_font(13),
                     text_color=COLOR_TEXT).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 0))
        value_pill = ctk.CTkLabel(timer_card, text=f"{state['timer_secs']}s", font=body_small_font(),
                                  text_color=COLOR_TEXT, fg_color=COLOR_ACCENT, corner_radius=999,
                                  padx=10, pady=2)
        value_pill.grid(row=0, column=2, sticky="e", padx=12, pady=(10, 0))
        ctk.CTkLabel(timer_card, text="Countdown before each shot  (min 1s · max 30s)",
                     font=body_small_font(), text_color=COLOR_TEXT_MUTED).grid(
            row=1, column=0, columnspan=3, sticky="w", padx=12, pady=(0, 6))
        rowf = ctk.CTkFrame(timer_card, fg_color="transparent")
        rowf.grid(row=2, column=0, columnspan=3, sticky="ew", padx=12, pady=(0, 12))
        rowf.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(rowf, text="1s", font=body_small_font(), text_color=COLOR_TEXT_MUTED).grid(
            row=0, column=0, padx=(0, 8))

        def _on_timer(v: float) -> None:
            secs = max(1, min(30, int(round(float(v)))))
            state["timer_secs"] = secs
            value_pill.configure(text=f"{secs}s")

        slider = ctk.CTkSlider(rowf, from_=1, to=30, number_of_steps=29, command=_on_timer)
        slider.set(state["timer_secs"])
        slider.grid(row=0, column=1, sticky="ew")
        ctk.CTkLabel(rowf, text="30s", font=body_small_font(), text_color=COLOR_TEXT_MUTED).grid(
            row=0, column=2, padx=(8, 0))
        state["timer_slider"] = slider

        # Single shot (with countdown).
        ctk.CTkButton(
            controls, text="📸  Capture Photo", height=44, corner_radius=CORNER_RADIUS,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, font=body_font(14),
            command=lambda: self._request_single(state),
        ).grid(row=1, column=0, sticky="ew", pady=(0, 8))

        # Auto-capture: N photos, each preceded by the countdown.
        auto = ctk.CTkFrame(controls, fg_color=COLOR_SURFACE, corner_radius=CORNER_RADIUS,
                            border_width=1, border_color=COLOR_BORDER)
        auto.grid(row=2, column=0, sticky="ew")
        auto.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(auto, text="Auto-capture", font=body_font(13), text_color=COLOR_TEXT).grid(
            row=0, column=0, sticky="w", padx=12, pady=(10, 2))
        rowf2 = ctk.CTkFrame(auto, fg_color="transparent")
        rowf2.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 6))
        ctk.CTkLabel(rowf2, text="Capture", font=body_small_font(), text_color=COLOR_TEXT_MUTED).pack(side="left")
        count_entry = ctk.CTkEntry(rowf2, width=56)
        count_entry.insert(0, "20")
        count_entry.pack(side="left", padx=6)
        ctk.CTkLabel(rowf2, text="photos", font=body_small_font(), text_color=COLOR_TEXT_MUTED).pack(side="left")
        auto_status = ctk.CTkLabel(auto, text="", font=body_small_font(), text_color=COLOR_ACCENT)
        auto_status.grid(row=2, column=0, sticky="w", padx=12, pady=(0, 2))
        start_btn = ctk.CTkButton(auto, text="Start Auto-Capture", height=34, corner_radius=CORNER_RADIUS,
                                  fg_color=COLOR_BORDER, hover_color=COLOR_ACCENT_HOVER,
                                  font=body_small_font(), command=lambda: self._toggle_auto(state))
        start_btn.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 12))
        state.update({"count_entry": count_entry, "auto_status": auto_status, "start_btn": start_btn})

    # --- preview loop + countdown overlay -----------------------------

    def _render_preview(self, state: dict, frame_bgr, countdown=None) -> None:
        disp = cv2.resize(frame_bgr, (self._PREVIEW_W, self._PREVIEW_H))
        mctrl = state["mirror"]
        if mctrl.display_mirror():
            disp = cv2.flip(disp, 1)
        disp = mctrl.apply_anim(disp)
        if countdown is not None:
            self._draw_countdown(disp, *countdown)
        if time.monotonic() < state.get("flash_until", 0.0):
            white = np.full_like(disp, 255)
            disp = cv2.addWeighted(disp, 0.35, white, 0.65, 0)
        rgb = cv2.cvtColor(disp, cv2.COLOR_BGR2RGB)
        img = ctk.CTkImage(light_image=Image.fromarray(rgb), dark_image=Image.fromarray(rgb),
                           size=(self._PREVIEW_W, self._PREVIEW_H))
        state["img"] = img  # keep a strong ref
        state["preview"].configure(image=img, text="")

    @staticmethod
    def _draw_countdown(disp, remaining: float, total: float) -> None:
        h, w = disp.shape[:2]
        cx, cy = w // 2, h // 2
        r = 64
        overlay = disp.copy()
        cv2.circle(overlay, (cx, cy), r + 16, (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.45, disp, 0.55, 0, dst=disp)
        frac = max(0.0, min(1.0, 1.0 - (remaining / total if total else 0.0)))
        accent = (246, 130, 59)   # COLOR_ACCENT (#3B82F6) in BGR
        cv2.ellipse(disp, (cx, cy), (r, r), 0, 0, 360, (90, 90, 90), 6)
        cv2.ellipse(disp, (cx, cy), (r, r), -90, 0, int(360 * frac), accent, 6)
        txt = str(max(1, math.ceil(remaining)))
        scale, thick = 2.4, 5
        (tw, th), _ = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)
        cv2.putText(disp, txt, (cx - tw // 2, cy + th // 2), cv2.FONT_HERSHEY_SIMPLEX,
                    scale, (255, 255, 255), thick, cv2.LINE_AA)

    def _capture_tick(self, state: dict) -> None:
        screen = self._capture_screen
        if screen is None or not screen.winfo_exists():
            return
        # Render only in live mode while visible (the review grid hides the camera, and the
        # camera can be released for power saving when the panel is off-screen).
        if state["mode"] == "live" and self.winfo_ismapped():
            frame = self.get_frame()
            if frame is not None:
                cd = state.get("countdown")
                if cd is not None:
                    remaining = cd["end"] - time.monotonic()
                    if remaining <= 0:
                        state["countdown"] = None
                        state["flash_until"] = time.monotonic() + 0.12
                        self._do_capture(state, frame)
                        self._render_preview(state, frame)
                        on_done = cd.get("on_done")
                        if on_done is not None:
                            on_done()
                    else:
                        self._render_preview(state, frame, countdown=(remaining, cd["total"]))
                else:
                    self._render_preview(state, frame)
            else:
                state["preview"].configure(image=None,
                                           text="Camera unavailable — open Live Monitor first")
        state["preview_job"] = screen.after(66, lambda: self._capture_tick(state))

    # --- capture into the in-memory buffer (no disk writes yet) -------

    def _start_countdown(self, state: dict, on_done=None) -> None:
        secs = int(state.get("timer_secs", 3))
        state["countdown"] = {"end": time.monotonic() + secs, "total": float(secs), "on_done": on_done}

    def _do_capture(self, state: dict, frame_bgr) -> None:
        state["buffer"].append(frame_bgr.copy())   # raw, un-mirrored — committed only on Save
        self._add_buffer_thumb(state, frame_bgr)
        self._update_buffer_count(state)

    def _request_single(self, state: dict) -> None:
        if state.get("countdown") is not None or state.get("auto") is not None:
            return  # already counting down / bursting
        if self.get_frame() is None:
            return
        self._start_countdown(state, on_done=None)

    # --- auto-capture: chained countdowns -----------------------------

    def _toggle_auto(self, state: dict) -> None:
        if state.get("auto") is not None:
            self._stop_auto(state, status="Stopped.")
            return
        if state.get("countdown") is not None:
            return
        try:
            target = max(1, int(float(state["count_entry"].get())))
        except (ValueError, KeyError):
            state["auto_status"].configure(text="Enter a valid photo count.", text_color=COLOR_DANGER)
            return
        if self.get_frame() is None:
            state["auto_status"].configure(text="Camera unavailable.", text_color=COLOR_DANGER)
            return
        state["auto"] = {"target": target, "n": 0}
        state["start_btn"].configure(text="Stop", fg_color=COLOR_DANGER)
        state["auto_status"].configure(text=f"Auto-capture: 0 / {target}", text_color=COLOR_ACCENT)
        self._start_countdown(state, on_done=lambda: self._auto_after(state))

    def _auto_after(self, state: dict) -> None:
        auto = state.get("auto")
        if auto is None:
            return
        auto["n"] += 1
        if state.get("auto_status") is not None and state["auto_status"].winfo_exists():
            state["auto_status"].configure(text=f"Auto-capture: {auto['n']} / {auto['target']}",
                                           text_color=COLOR_ACCENT)
        if auto["n"] >= auto["target"]:
            n = auto["target"]
            state["auto"] = None
            btn = state.get("start_btn")
            if btn is not None and btn.winfo_exists():
                btn.configure(text="Start Auto-Capture", fg_color=COLOR_BORDER)
            self._show_success_overlay(state, n)
        else:
            self._start_countdown(state, on_done=lambda: self._auto_after(state))

    def _stop_auto(self, state: dict, status: str = "") -> None:
        state["auto"] = None
        state["countdown"] = None
        btn = state.get("start_btn")
        if btn is not None:
            try:
                if btn.winfo_exists():
                    btn.configure(text="Start Auto-Capture", fg_color=COLOR_BORDER)
            except Exception:
                pass
        if status and state.get("auto_status") is not None:
            try:
                if state["auto_status"].winfo_exists():
                    state["auto_status"].configure(text=status, text_color=COLOR_TEXT_MUTED)
            except Exception:
                pass

    # --- buffer thumbnails + count ------------------------------------

    def _thumb_ctkimage(self, frame_bgr, size: int):
        t = cv2.resize(frame_bgr, (size, size))
        rgb = cv2.cvtColor(t, cv2.COLOR_BGR2RGB)
        return ctk.CTkImage(light_image=Image.fromarray(rgb), dark_image=Image.fromarray(rgb),
                            size=(size, size))

    def _add_buffer_thumb(self, state: dict, frame_bgr) -> None:
        strip = state["strip"]
        if state.get("empty_lbl") is not None:
            try:
                state["empty_lbl"].destroy()
            except Exception:
                pass
            state["empty_lbl"] = None
        cimg = self._thumb_ctkimage(frame_bgr, 64)
        cell = ctk.CTkLabel(strip, text="", image=cimg)
        cells = state.setdefault("strip_cells", [])
        cells.append({"cell": cell, "img": cimg})
        while len(cells) > self._RECENT_MAX:
            old = cells.pop(0)
            try:
                old["cell"].destroy()
            except Exception:
                pass
        for i, c in enumerate(cells):
            c["cell"].grid(row=0, column=i, padx=4, pady=4)

    def _update_buffer_count(self, state: dict) -> None:
        n = len(state["buffer"])
        if state.get("count_lbl") is not None and state["count_lbl"].winfo_exists():
            state["count_lbl"].configure(text=f"Captured: {n} (unsaved)")
        btn = state.get("review_btn")
        if btn is not None and btn.winfo_exists():
            btn.configure(text=f"Review & Save ({n})", state="normal" if n else "disabled")

    # --- success overlay ----------------------------------------------

    def _show_success_overlay(self, state: dict, n: int) -> None:
        screen = self._capture_screen
        if screen is None:
            return
        card = ctk.CTkFrame(screen, fg_color=COLOR_SURFACE, corner_radius=18,
                            border_width=1, border_color=COLOR_BORDER)
        card.place(relx=0.5, rely=0.5, anchor="center")
        state["overlay"] = card
        check = ctk.CTkLabel(card, text="✓", font=heading_font(18), text_color=COLOR_SAFE)
        check.pack(padx=48, pady=(28, 4))
        ctk.CTkLabel(card, text=f"{n} photo{'s' if n != 1 else ''} taken successfully",
                     font=heading_font(16), text_color=COLOR_TEXT).pack(padx=48, pady=(0, 2))
        ctk.CTkLabel(card, text="Review them next, then save or discard.",
                     font=body_small_font(), text_color=COLOR_TEXT_MUTED).pack(padx=48, pady=(0, 14))
        ctk.CTkButton(card, text="OK", width=180, height=42, corner_radius=CORNER_RADIUS,
                      fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER, font=body_font(14),
                      command=lambda: self._dismiss_success(state)).pack(padx=48, pady=(0, 26))

        def _grow(sz: int) -> None:
            if sz <= 60 and check.winfo_exists():
                check.configure(font=heading_font(sz))
                card.after(16, lambda: _grow(sz + 7))
        _grow(18)

    def _dismiss_success(self, state: dict) -> None:
        ov = state.pop("overlay", None)
        if ov is not None:
            try:
                ov.destroy()
            except Exception:
                pass
        self._show_review(state)

    # --- review grid: discard / save ----------------------------------

    def _show_review(self, state: dict) -> None:
        if not state["buffer"]:
            return
        state["mode"] = "review"
        self._stop_auto(state)
        if state.get("col") is not None:
            state["col"].grid_remove()

        review = ctk.CTkFrame(state["body"], fg_color="transparent")
        review.grid(row=0, column=0, sticky="nsew")
        review.grid_columnconfigure(0, weight=1)
        review.grid_rowconfigure(1, weight=1)
        state["review_frame"] = review

        head = ctk.CTkFrame(review, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=PADDING, pady=(0, 8))
        head.grid_columnconfigure(0, weight=1)
        state["review_title"] = ctk.CTkLabel(
            head, text=f"Review {len(state['buffer'])} photos", font=heading_font(16),
            text_color=COLOR_TEXT)
        state["review_title"].grid(row=0, column=0, sticky="w")
        ctk.CTkButton(head, text="← Back to camera", width=160, height=34,
                      corner_radius=CORNER_RADIUS, fg_color=COLOR_BORDER,
                      hover_color=COLOR_ACCENT_HOVER, font=body_small_font(),
                      command=lambda: self._back_to_camera(state)).grid(row=0, column=1, sticky="e")

        grid = ctk.CTkScrollableFrame(review, fg_color=COLOR_SURFACE, corner_radius=CORNER_RADIUS,
                                      border_width=1, border_color=COLOR_BORDER)
        grid.grid(row=1, column=0, sticky="nsew", padx=PADDING)
        cols = 6
        for c in range(cols):
            grid.grid_columnconfigure(c, weight=1)
        state["review_grid"] = grid
        state["review_cols"] = cols
        self._rebuild_review_grid(state)

        footer = ctk.CTkFrame(review, fg_color="transparent")
        footer.grid(row=2, column=0, sticky="ew", padx=PADDING, pady=PADDING)
        footer.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkButton(footer, text="Discard changes", height=44, corner_radius=CORNER_RADIUS,
                      fg_color=COLOR_DANGER, hover_color="#DC2626", font=body_font(14),
                      command=lambda: self._discard_changes(state)).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ctk.CTkButton(footer, text="Save changes to folder", height=44, corner_radius=CORNER_RADIUS,
                      fg_color=COLOR_SAFE, hover_color="#0E9F6E", font=body_font(14),
                      command=lambda: self._save_changes(state)).grid(row=0, column=1, sticky="ew", padx=(6, 0))

    def _rebuild_review_grid(self, state: dict) -> None:
        grid = state.get("review_grid")
        if grid is None:
            return
        for w in grid.winfo_children():
            w.destroy()
        state["review_thumbs"] = []
        cols = state.get("review_cols", 6)
        if not state["buffer"]:
            ctk.CTkLabel(grid, text="No photos.", font=body_small_font(),
                         text_color=COLOR_TEXT_MUTED).grid(row=0, column=0, padx=8, pady=8)
            return
        for i, frame in enumerate(list(state["buffer"])):
            cimg = self._thumb_ctkimage(frame, 96)
            cell = ctk.CTkFrame(grid, fg_color=COLOR_BG, corner_radius=8)
            cell.grid(row=i // cols, column=i % cols, padx=6, pady=6)
            ctk.CTkLabel(cell, text="", image=cimg).grid(row=0, column=0, padx=4, pady=4)
            ctk.CTkButton(cell, text="✕", width=22, height=22, corner_radius=11,
                          fg_color=COLOR_DANGER, hover_color="#DC2626", font=body_small_font(),
                          command=lambda fr=frame: self._remove_from_buffer(state, fr)
                          ).place(relx=1.0, x=-2, y=2, anchor="ne")
            state["review_thumbs"].append(cimg)

    def _remove_from_buffer(self, state: dict, frame) -> None:
        buf = state["buffer"]
        for i, f in enumerate(buf):
            if f is frame:
                buf.pop(i)
                break
        self._update_buffer_count(state)
        if state.get("review_title") is not None and state["review_title"].winfo_exists():
            state["review_title"].configure(text=f"Review {len(buf)} photos")
        if not buf:
            self._back_to_camera(state)
        else:
            self._rebuild_review_grid(state)

    def _back_to_camera(self, state: dict) -> None:
        rf = state.pop("review_frame", None)
        if rf is not None:
            try:
                rf.destroy()
            except Exception:
                pass
        state["review_grid"] = None
        state["mode"] = "live"
        if state.get("col") is not None:
            state["col"].grid()
        self._rebuild_strip(state)
        self._update_buffer_count(state)

    def _rebuild_strip(self, state: dict) -> None:
        strip = state.get("strip")
        if strip is None:
            return
        for c in state.get("strip_cells", []):
            try:
                c["cell"].destroy()
            except Exception:
                pass
        state["strip_cells"] = []
        recent = state["buffer"][-self._RECENT_MAX:]
        if not recent:
            state["empty_lbl"] = ctk.CTkLabel(strip, text="No photos captured yet.",
                                              font=body_small_font(), text_color=COLOR_TEXT_MUTED)
            state["empty_lbl"].grid(row=0, column=0, padx=8, pady=8)
            return
        state["empty_lbl"] = None
        for frame in recent:
            self._add_buffer_thumb(state, frame)

    def _save_changes(self, state: dict) -> None:
        module, label = state["module"], state["label"]
        n = 0
        for frame in state["buffer"]:
            try:
                self.trainer.add_sample(module, label, frame)
                n += 1
            except Exception as exc:
                print(f"[Training] save error: {exc}")
        state["buffer"] = []
        show_toast(self, f"Saved {n} photo{'s' if n != 1 else ''} to {_pretty(label)}.",
                   type="success", duration=4000)
        self._close_capture_screen()

    def _discard_changes(self, state: dict) -> None:
        state["buffer"] = []
        self._close_capture_screen()

    # --- teardown / navigation ----------------------------------------

    def _cancel_capture_jobs(self, state: dict) -> None:
        state["countdown"] = None
        state["auto"] = None

    def _on_back(self) -> None:
        state = self._capture_state
        if state is not None and state.get("buffer") and state.get("mode") != "review":
            self._cancel_capture_jobs(state)
            self._show_review(state)
        else:
            self._close_capture_screen()

    def _close_capture_screen(self) -> None:
        state = self._capture_state
        module = label = None
        if state is not None:
            self._cancel_capture_jobs(state)
            if state.get("preview_job") is not None:
                try:
                    self._capture_screen.after_cancel(state["preview_job"])
                except Exception:
                    pass
                state["preview_job"] = None
            module, label = state["module"], state["label"]
        if self._capture_screen is not None:
            try:
                self._capture_screen.destroy()
            except Exception:
                pass
            self._capture_screen = None
        self._capture_state = None
        if self._tabview is not None:
            self._tabview.grid()
        if module is not None:
            self._refresh_thumbnails(module, label)
            self._refresh_counts(module)

    def on_show(self) -> None:
        return

    def on_hide(self) -> None:
        # Leaving Training mid-capture: stop the camera loop and restore the tabs.
        if self._capture_screen is not None:
            self._close_capture_screen()

    def _clear(self, module: str, label: str) -> None:
        dialog = ctk.CTkInputDialog(
            text=f'Type "CLEAR" to delete ALL "{_pretty(label)}" photos.',
            title="Confirm Clear",
        )
        if dialog.get_input() != "CLEAR":
            return
        self.trainer.clear_samples(module, label)
        self._refresh_thumbnails(module, label)
        self._refresh_counts(module)
        show_toast(self, f"Cleared all {_pretty(label)} photos.", type="success")

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------

    def _set_buttons_state(self, module: str, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for label in MODULES[module]["labels"]:
            for btn in self._ui[module][label]["buttons"]:
                btn.configure(state=state)
        controls = self._ui[module]["controls"]
        controls["train_btn"].configure(state=state)
        # Evaluate only enables when a trained model exists.
        eval_state = "normal" if (enabled and self.trainer.is_trained(module)) else "disabled"
        controls["evaluate_btn"].configure(state=eval_state)

    def _start_training(self, module: str) -> None:
        if self._training.get(module):
            return
        self._training[module] = True
        self._set_buttons_state(module, False)

        controls = self._ui[module]["controls"]
        controls["progress_bar"].configure(mode="indeterminate")
        controls["progress_bar"].start()
        controls["progress_label"].configure(text="Preparing…")

        def _safe_after(func, *args) -> None:
            # Window may be destroyed mid-training; ignore the resulting TclError.
            try:
                if self.winfo_exists():
                    self.after(0, func, *args)
            except Exception:
                pass

        def _run() -> None:
            ok, message = self.trainer.train(
                module,
                on_progress=lambda m: _safe_after(self._set_progress, module, m),
            )
            _safe_after(self._on_train_done, module, ok, message)

        threading.Thread(target=_run, daemon=True).start()

    def _set_progress(self, module: str, message: str) -> None:
        self._ui[module]["controls"]["progress_label"].configure(text=message)

    def _on_train_done(self, module: str, ok: bool, message: str) -> None:
        self._training[module] = False
        controls = self._ui[module]["controls"]
        controls["progress_bar"].stop()
        controls["progress_bar"].set(0)
        controls["progress_label"].configure(
            text=message, text_color=COLOR_SAFE if ok else COLOR_DANGER,
        )
        self._set_buttons_state(module, True)
        self._refresh_counts(module)
        show_toast(self, message, type="success" if ok else "error", duration=5000)

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def _start_evaluation(self, module: str) -> None:
        if self._training.get(module) or self._evaluating.get(module):
            return
        if not self.trainer.is_trained(module):
            return
        self._evaluating[module] = True
        self._set_buttons_state(module, False)

        controls = self._ui[module]["controls"]
        controls["progress_bar"].configure(mode="indeterminate")
        controls["progress_bar"].start()
        controls["progress_label"].configure(text="Evaluating…", text_color=COLOR_ACCENT)

        def _safe_after(func, *args) -> None:
            try:
                if self.winfo_exists():
                    self.after(0, func, *args)
            except Exception:
                pass

        def _run() -> None:
            try:
                results = self.trainer.evaluate(
                    module, on_progress=lambda m: _safe_after(self._set_progress, module, m)
                )
            except Exception as exc:
                print(f"[Training] evaluate error: {exc}")
                results = None
            _safe_after(self._on_eval_done, module, results)

        threading.Thread(target=_run, daemon=True).start()

    def _on_eval_done(self, module: str, results: dict | None) -> None:
        self._evaluating[module] = False
        controls = self._ui[module]["controls"]
        controls["progress_bar"].stop()
        controls["progress_bar"].set(0)
        self._set_buttons_state(module, True)

        if results is None:
            controls["progress_label"].configure(
                text="No samples to evaluate.", text_color=COLOR_DANGER,
            )
            show_toast(self, "No samples to evaluate.", type="error")
            return

        controls["progress_label"].configure(
            text="Evaluation complete.", text_color=COLOR_SAFE,
        )
        self._render_eval_results(module, results)
        show_toast(
            self,
            f"Accuracy: {results['accuracy']:.1%} ({results['correct']}/{results['total']})",
            type="success", duration=5000,
        )

    def _render_eval_results(self, module: str, results: dict) -> None:
        card = self._ui[module]["controls"]["results_card"]
        for child in card.winfo_children():
            child.destroy()

        labels = results["labels"]
        acc = results["accuracy"]
        acc_color = COLOR_SAFE if acc >= 0.85 else COLOR_WARNING if acc >= 0.65 else COLOR_DANGER

        row = 0
        ctk.CTkLabel(
            card, text="Evaluation Results", font=heading_font(16), text_color=COLOR_TEXT,
        ).grid(row=row, column=0, sticky="w", padx=PADDING, pady=(PADDING, 8))
        row += 1

        # Overall accuracy
        acc_box = ctk.CTkFrame(card, fg_color="transparent")
        acc_box.grid(row=row, column=0, sticky="w", padx=PADDING, pady=(0, 10))
        ctk.CTkLabel(
            acc_box, text=f"{acc:.1%}", font=heading_font(28), text_color=acc_color,
        ).pack(side="left")
        ctk.CTkLabel(
            acc_box, text=f"  {results['correct']}/{results['total']} correct",
            font=body_small_font(), text_color=COLOR_TEXT_MUTED,
        ).pack(side="left", pady=(10, 0))

        # Per-class metrics table
        table = ctk.CTkFrame(card, fg_color="transparent")
        table.grid(row=row + 1, column=0, sticky="ew", padx=PADDING, pady=(0, 10))
        for c, w in enumerate((3, 2, 2, 2)):
            table.grid_columnconfigure(c, weight=w)

        headers = ("CLASS", "PRECISION", "RECALL", "F1")
        for c, h in enumerate(headers):
            ctk.CTkLabel(
                table, text=h, font=body_small_font(), text_color=COLOR_TEXT_MUTED,
                anchor="w" if c == 0 else "center",
            ).grid(row=0, column=c, sticky="ew", padx=(6 if c else 10, 6), pady=(0, 4))

        for r, lbl in enumerate(labels, start=1):
            m = results["per_class"][lbl]
            f1 = m["f1"]
            bar_color = COLOR_SAFE if f1 >= 0.80 else COLOR_WARNING if f1 >= 0.60 else COLOR_DANGER
            row_bg = COLOR_SURFACE if r % 2 else ROW_STRIPE_ODD

            rowf = ctk.CTkFrame(table, fg_color=row_bg, corner_radius=6)
            rowf.grid(row=r, column=0, columnspan=4, sticky="ew", pady=1)
            rowf.grid_columnconfigure(1, weight=3)
            for c, w in enumerate((2, 2, 2), start=2):
                rowf.grid_columnconfigure(c, weight=w)

            ctk.CTkFrame(rowf, fg_color=bar_color, width=3, corner_radius=0).grid(
                row=0, column=0, sticky="ns", padx=(0, 6),
            )
            ctk.CTkLabel(
                rowf, text=_pretty(lbl), font=body_small_font(), text_color=COLOR_TEXT, anchor="w",
            ).grid(row=0, column=1, sticky="w", pady=6)
            for c, key in ((2, "precision"), (3, "recall"), (4, "f1")):
                ctk.CTkLabel(
                    rowf, text=f"{m[key]:.1%}", font=body_small_font(),
                    text_color=COLOR_TEXT, anchor="center",
                ).grid(row=0, column=c, sticky="ew", pady=6, padx=4)

        # Confusion matrix (2x2)
        conf = results["confusion"]
        cm_wrap = ctk.CTkFrame(card, fg_color="transparent")
        cm_wrap.grid(row=row + 2, column=0, sticky="ew", padx=PADDING, pady=(0, 8))
        for c in range(3):
            cm_wrap.grid_columnconfigure(c, weight=1 if c else 0)

        ctk.CTkLabel(
            cm_wrap, text="Predicted:", font=body_small_font(), text_color=COLOR_TEXT_MUTED,
        ).grid(row=0, column=1, columnspan=2, sticky="w", pady=(0, 2))
        for j, lbl in enumerate(labels):
            ctk.CTkLabel(
                cm_wrap, text=_pretty(lbl), font=body_small_font(), text_color=COLOR_TEXT_MUTED,
            ).grid(row=1, column=1 + j, sticky="ew")

        for i, lbl in enumerate(labels):
            ctk.CTkLabel(
                cm_wrap, text=f"Actual: {_pretty(lbl)}", font=body_small_font(),
                text_color=COLOR_TEXT_MUTED, anchor="e",
            ).grid(row=2 + i, column=0, sticky="e", padx=(0, 6), pady=2)
            for j in range(len(labels)):
                tile_bg = _TILE_GREEN if i == j else _TILE_RED
                tile = ctk.CTkFrame(cm_wrap, fg_color=tile_bg, corner_radius=8, height=64)
                tile.grid(row=2 + i, column=1 + j, sticky="ew", padx=3, pady=3)
                tile.grid_propagate(False)
                tile.grid_columnconfigure(0, weight=1)
                ctk.CTkLabel(
                    tile, text=str(conf[i][j]), font=heading_font(20),
                    text_color=COLOR_SAFE if i == j else COLOR_DANGER,
                ).grid(row=0, column=0, pady=(8, 0))
                ctk.CTkLabel(
                    tile, text="correct" if i == j else "wrong",
                    font=body_small_font(), text_color=COLOR_TEXT_MUTED,
                ).grid(row=1, column=0, pady=(0, 6))

        # Footnote
        ctk.CTkLabel(
            card,
            text="Evaluated on held-out photos the model never trained on — these numbers "
                 "reflect real-world accuracy.",
            font=ctk.CTkFont(size=12, slant="italic"),
            text_color=COLOR_TEXT_MUTED, wraplength=300, justify="left",
        ).grid(row=row + 3, column=0, sticky="w", padx=PADDING, pady=(0, PADDING))

        card.grid()

    def _toggle_help(self, module: str) -> None:
        controls = self._ui[module]["controls"]
        if controls["help_visible"]:
            controls["help_box"].grid_remove()
            controls["help_btn"].configure(text="How does this work?  ▾")
            controls["help_visible"] = False
        else:
            controls["help_box"].grid()
            controls["help_btn"].configure(text="How does this work?  ▴")
            controls["help_visible"] = True
