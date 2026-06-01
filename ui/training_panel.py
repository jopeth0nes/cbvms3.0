"""In-app training panel for CBVMS — build datasets and train YOLOv8 classifiers."""

from __future__ import annotations

import threading
from datetime import datetime
from tkinter import filedialog
from typing import Callable

import cv2
import customtkinter as ctk
import numpy as np
from PIL import Image

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
    body_font,
    body_small_font,
    heading_font,
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
    "Photos you upload or capture are used to train a YOLOv8 image classifier. "
    "The model learns the visual difference between the two classes.\n\n"
    f"Minimum {MIN_SAMPLES_PER_CLASS} photos per class is recommended. "
    "More diverse photos (different angles, lighting, people) = better accuracy.\n\n"
    "Training runs entirely on your machine using the same YOLOv8 framework as "
    "the live detector. The trained model is saved next to yolov8n.pt and can be "
    "used by the detection pipeline."
)


def _pretty(label: str) -> str:
    return label.replace("_", " ").title()


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

        capture_btn = ctk.CTkButton(
            btns, text="📷  Capture from Camera", height=34, corner_radius=CORNER_RADIUS,
            fg_color=COLOR_BORDER, hover_color=COLOR_ACCENT_HOVER, font=body_small_font(),
            command=lambda: self._capture(module, label),
        )
        capture_btn.grid(row=1, column=0, sticky="ew", pady=2)

        clear_btn = ctk.CTkButton(
            btns, text="🗑  Clear All", height=34, corner_radius=CORNER_RADIUS,
            fg_color=COLOR_DANGER, hover_color="#DC2626", font=body_small_font(),
            command=lambda: self._clear(module, label),
        )
        clear_btn.grid(row=2, column=0, sticky="ew", pady=2)

        ctk.CTkLabel(
            frame, text=f"Min. {MIN_SAMPLES_PER_CLASS} photos required to train",
            font=body_small_font(), text_color=COLOR_TEXT_MUTED,
        ).grid(row=3, column=0, sticky="w", padx=PADDING, pady=(6, PADDING))

        self._ui[module][label] = {
            "count_badge": count_badge,
            "thumbs": thumbs,
            "buttons": [upload_btn, capture_btn, clear_btn],
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
        controls["counts_label"].configure(
            text="   ".join(f"{_pretty(l)}: {c}" for l, c in counts.items())
        )

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
        paths = filedialog.askopenfilenames(
            title="Select photos",
            filetypes=[("Images", "*.jpg *.jpeg *.png *.bmp")],
        )
        if not paths:
            return
        added = 0
        for p in paths:
            img = cv2.imread(p)
            if img is None:
                continue
            try:
                self.trainer.add_sample(module, label, img)
                added += 1
            except Exception:
                continue
        self._refresh_thumbnails(module, label)
        self._refresh_counts(module)
        show_toast(self, f"Added {added} photo(s) to {_pretty(label)}.", type="success")

    def _capture(self, module: str, label: str) -> None:
        frame = self.get_frame()
        if frame is None:
            show_toast(self, "Camera not available. Open Live Monitor first.", type="error")
            return
        try:
            self.trainer.add_sample(module, label, frame)
        except Exception as exc:
            show_toast(self, f"Capture failed: {exc}", type="error")
            return
        self._refresh_thumbnails(module, label)
        self._refresh_counts(module)
        show_toast(self, f"Captured 1 photo for {_pretty(label)}.", type="success")

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
            text="Evaluated on training data — for best results, test on unseen photos.",
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
