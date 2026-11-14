"""Paginated, correctable review of duplicate matches during uniform imports."""
from __future__ import annotations

import weakref

import customtkinter as ctk
from PIL import Image, ImageOps

from core.uniform_dataset import CATEGORIES
from ui.background_task import BackgroundTask
from ui.components import COLOR_ACCENT, COLOR_TEXT_MUTED

_WINDOWS = weakref.WeakValueDictionary()
_PAGE_SIZE = 4
_THUMB_SIZE = (210, 210)

_ACTIONS = {
    "exact": (("skip", "Skip"), ("add_source", "Add source anyway")),
    "similar": (("keep_both", "Keep both"), ("skip", "Skip incoming")),
    "conflict": (("use_existing", "Use existing category"),
                 ("change_category", "Change category for this image"), ("skip", "Skip incoming")),
}


def _thumbnail(path):
    if not path:
        return None
    try:
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail(_THUMB_SIZE, Image.Resampling.LANCZOS)
            return image.copy()
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


def _source_name(value):
    # Keep folder/archive context so identical filenames remain distinguishable.
    value = str(value or "source").replace("\n", " ").replace("\r", " ")
    return value if len(value) <= 240 else "…" + value[-239:]


class DuplicateReviewWindow(ctk.CTkToplevel):
    """Review staged duplicate groups while the import worker waits for choices."""

    def __new__(cls, master, job):
        existing = _WINDOWS.get(getattr(job, "job_id", None))
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing._closed = False
                    return existing
            except Exception:
                pass
        return super().__new__(cls)

    def __init__(self, master, job):
        if getattr(self, "_duplicate_review_initialized", False):
            try:
                self.deiconify()
                self.lift()
                self.refresh()
            except Exception:
                pass
            return
        super().__init__(master)
        self._duplicate_review_initialized = True
        self.job = job
        self.job_id = job.job_id
        self.title("Review possible duplicate photos")
        self.geometry("1040x790")
        self.minsize(780, 600)
        self.transient(master.winfo_toplevel())
        self.protocol("WM_DELETE_WINDOW", self.close)
        _WINDOWS[self.job_id] = self

        self._rows = []
        self._page = 0
        self._match_indices = {}
        self._images = {}
        self._labels = {}
        self._page_widgets = []
        self._thumbnail_task = BackgroundTask(self)
        self._thumbnail_generation = 0
        self._thumbnail_busy = False
        self._closed = False

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)
        ctk.CTkLabel(self, text="Review possible duplicate photos", font=ctk.CTkFont(size=21, weight="bold"),
                     anchor="w").grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 2))
        ctk.CTkLabel(self, text=("Exact matches can add provenance. Similarity is only an estimate: useful views stay separate. "
            "Inspect the original and crop before choosing; visual matches can be wrong."),
            anchor="w", justify="left", wraplength=980, text_color=COLOR_TEXT_MUTED).grid(
                row=1, column=0, sticky="ew", padx=18, pady=(0, 10))

        self.toolbar = ctk.CTkFrame(self, fg_color="transparent")
        self.toolbar.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 6))
        self.toolbar.grid_columnconfigure(1, weight=1)
        self.previous_button = ctk.CTkButton(self.toolbar, text="Previous", width=90, command=self.previous_page)
        self.previous_button.grid(row=0, column=0, padx=4)
        self.page_label = ctk.CTkLabel(self.toolbar, text="", anchor="w")
        self.page_label.grid(row=0, column=1, sticky="w", padx=8)
        self.next_button = ctk.CTkButton(self.toolbar, text="Next", width=90, command=self.next_page)
        self.next_button.grid(row=0, column=2, padx=4)

        self.groups = ctk.CTkScrollableFrame(self, label_text="Duplicate groups")
        self.groups.grid(row=3, column=0, sticky="nsew", padx=14, pady=5)
        self.groups.grid_columnconfigure(0, weight=1)
        self.summary = ctk.CTkLabel(self, text="", anchor="w", justify="left", wraplength=980,
                                    text_color=COLOR_TEXT_MUTED)
        self.summary.grid(row=4, column=0, sticky="ew", padx=18, pady=(7, 4))
        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=5, column=0, sticky="ew", padx=14, pady=(4, 14))
        buttons.grid_columnconfigure(0, weight=1)
        self.cancel_button = ctk.CTkButton(buttons, text="Cancel import", width=130,
                                           fg_color="transparent", border_width=1, command=self.cancel_import)
        self.cancel_button.grid(row=0, column=1, padx=5)
        self.continue_button = ctk.CTkButton(buttons, text="Continue import", width=150,
                                             fg_color=COLOR_ACCENT, command=self.submit)
        self.continue_button.grid(row=0, column=2, padx=5)
        self.status = ctk.CTkLabel(self, text="", anchor="w", text_color=COLOR_TEXT_MUTED)
        self.status.grid(row=6, column=0, sticky="ew", padx=18, pady=(0, 10))
        self.refresh()

    def _current(self):
        try:
            state = self.job.snapshot()
            return state.get("job_id") == self.job_id and state.get("phase") == "waiting_duplicates"
        except Exception:
            return False

    def refresh(self):
        if self._closed:
            return
        state = self.job.snapshot()
        if state.get("job_id") != self.job_id:
            self.status.configure(text="This import job is no longer available.")
            self.continue_button.configure(state="disabled")
            self.cancel_button.configure(state="disabled")
            return
        self._rows = list(self.job.duplicate_rows())
        choices = self.job.duplicate_choices()
        self._page = min(self._page, max(0, (len(self._rows) - 1) // _PAGE_SIZE))
        self._render_page(choices)
        self._render_summary(choices)
        waiting = state.get("phase") == "waiting_duplicates" and state.get("active")
        self.continue_button.configure(state="normal" if waiting else "disabled")
        self.cancel_button.configure(state="normal" if waiting else "disabled")
        if not waiting:
            self.status.configure(text="Duplicate decisions are not currently being requested.")
        else:
            self.status.configure(text="Choose how to handle each group, then continue the import.")

    def _render_page(self, choices):
        for widget in self._page_widgets:
            try: widget.destroy()
            except Exception: pass
        self._page_widgets.clear()
        start = self._page * _PAGE_SIZE
        rows = self._rows[start:start + _PAGE_SIZE]
        total_pages = max(1, (len(self._rows) + _PAGE_SIZE - 1) // _PAGE_SIZE)
        self.page_label.configure(text=f"Groups {start + 1 if rows else 0}–{start + len(rows)} of {len(self._rows)}  •  Page {self._page + 1}/{total_pages}")
        self.previous_button.configure(state="normal" if self._page else "disabled")
        self.next_button.configure(state="normal" if self._page + 1 < total_pages else "disabled")
        self._labels.clear()
        for index, row in enumerate(rows):
            absolute = start + index
            frame = ctk.CTkFrame(self.groups)
            frame.grid(row=index, column=0, sticky="ew", padx=5, pady=5)
            frame.grid_columnconfigure((0, 1), weight=1)
            self._page_widgets.append(frame)
            match_type = row.get("match_type", "similar")
            occurrences = max(1, int(row.get("occurrences", 1)))
            ctk.CTkLabel(frame, text=(f"{match_type.title()} match • {occurrences} incoming occurrence"
                f"{'s' if occurrences != 1 else ''} • {CATEGORIES.get(row.get('incoming_category'), row.get('incoming_category', 'Unknown category'))}"),
                anchor="w", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, columnspan=2,
                                                                    sticky="ew", padx=10, pady=(8, 3))
            incoming = ctk.CTkFrame(frame, fg_color="transparent")
            incoming.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)
            incoming.grid_columnconfigure(0, weight=1)
            sources = row.get("incoming_sources") or [row.get("incoming_source", "source")]
            shown_sources = [_source_name(value) for value in sources[:3]]
            extra_sources = max(0, len(sources) - len(shown_sources))
            source_text = ", ".join(shown_sources) + (f" and {extra_sources} more" if extra_sources else "")
            ctk.CTkLabel(incoming, text=f"Incoming • {source_text}", anchor="w", wraplength=420).grid(
                row=0, column=0, sticky="ew")
            incoming_label = ctk.CTkLabel(incoming, text="Loading photo…", width=220, height=150)
            incoming_label.grid(row=1, column=0, sticky="w", pady=3)
            self._labels[(absolute, "incoming")] = incoming_label

            matches = list(row.get("matches") or [])
            match_box = ctk.CTkFrame(frame, fg_color="transparent")
            match_box.grid(row=1, column=1, sticky="nsew", padx=8, pady=4)
            match_box.grid_columnconfigure(0, weight=1)
            match_idx = min(self._match_indices.get(row.get("row_id"), 0), max(0, len(matches) - 1))
            self._match_indices[row.get("row_id")] = match_idx
            selected = matches[match_idx] if matches else {}
            title = "Within this batch" if selected.get("within_batch") else "Existing photo"
            ctk.CTkLabel(match_box, text=f"{title} • {CATEGORIES.get(selected.get('category'), selected.get('category', 'Unknown category'))}", anchor="w").grid(
                row=0, column=0, sticky="ew")
            match_label = ctk.CTkLabel(match_box, text="Loading photo…", width=220, height=150)
            match_label.grid(row=1, column=0, sticky="w", pady=3)
            self._labels[(absolute, "match")] = match_label
            match_source = selected.get("source", "match")
            if len(matches) > 1:
                options = [f"{i + 1}: {CATEGORIES.get(m.get('category'), m.get('category', '?'))} • {_source_name(m.get('source'))}" for i, m in enumerate(matches)]
                selector = ctk.CTkOptionMenu(match_box, values=options,
                    command=lambda value, ri=absolute, opts=options: self._select_match(ri, opts.index(value)))
                selector.set(options[match_idx])
                selector.grid(row=2, column=0, sticky="w", pady=2)
                self._page_widgets.append(selector)
            else:
                ctk.CTkLabel(match_box, text=_source_name(match_source), anchor="w", wraplength=420, text_color=COLOR_TEXT_MUTED).grid(
                    row=2, column=0, sticky="ew")
            ctk.CTkLabel(match_box, text=_source_name(match_source), anchor="w",
                         wraplength=420, text_color=COLOR_TEXT_MUTED).grid(row=3, column=0, sticky="ew")
            ctk.CTkLabel(match_box, text="Inspect original and crop in the review workspace.", anchor="w",
                         text_color=COLOR_TEXT_MUTED).grid(row=4, column=0, sticky="ew")

            actions = _ACTIONS.get(match_type, _ACTIONS["similar"])
            action_map = dict(actions)
            stored = choices.get(row.get("row_id"), row.get("default_action", actions[0][0]))
            if stored not in action_map:
                stored = row.get("default_action", actions[0][0])
            choice = ctk.StringVar(value=action_map[stored])
            menu = ctk.CTkOptionMenu(frame, values=[label for _, label in actions], variable=choice,
                command=lambda label, rid=row.get("row_id"), typ=match_type, apply=None:
                    self._choose(rid, typ, label))
            menu.grid(row=2, column=0, padx=10, pady=(3, 8), sticky="w")
            self._page_widgets.append(menu)
            apply_var = ctk.BooleanVar(value=False)
            apply_box = ctk.CTkCheckBox(frame, text="Apply to this match type", variable=apply_var,
                command=lambda rid=row.get("row_id"), typ=match_type, value=choice, flag=apply_var:
                    self._choose(rid, typ, value.get(), apply_to_type=flag.get()))
            apply_box.grid(row=2, column=1, padx=10, pady=(3, 8), sticky="w")
            self._page_widgets.extend((menu, apply_box))
            if match_type == "conflict":
                ctk.CTkLabel(frame, text=f"Existing: {CATEGORIES.get(selected.get('category'), selected.get('category', 'Unknown'))}  →  Incoming: {CATEGORIES.get(row.get('incoming_category'), row.get('incoming_category', 'Unknown'))}",
                             anchor="w", text_color=COLOR_TEXT_MUTED).grid(row=3, column=0, columnspan=2,
                                                                            sticky="ew", padx=10, pady=(0, 7))
        self._load_thumbnails(start, rows)

    def _choose(self, row_id, match_type, label, apply_to_type=False):
        if not self._current():
            self.refresh()
            return
        actions = dict(_ACTIONS.get(match_type, _ACTIONS["similar"]))
        action = next((key for key, value in actions.items() if value == label), None)
        if action:
            self.job.set_duplicate_choice(row_id, action, apply_to_type=apply_to_type)
            if apply_to_type:
                self.refresh()
                return
            self._render_summary(self.job.duplicate_choices())

    def _select_match(self, absolute, index):
        row = self._rows[absolute]
        self._match_indices[row.get("row_id")] = index
        self._render_page(self.job.duplicate_choices())

    def _load_thumbnails(self, start, rows):
        self._thumbnail_generation += 1
        generation = self._thumbnail_generation
        requests = []
        for offset, row in enumerate(rows):
            absolute = start + offset
            matches = row.get("matches") or []
            selected = matches[min(self._match_indices.get(row.get("row_id"), 0), max(0, len(matches) - 1))] if matches else {}
            requests.extend(((absolute, "incoming", row.get("incoming_path")),
                             (absolute, "match", selected.get("path"))))
        if self._thumbnail_busy:
            self._pending_thumbnails = (start, rows)
            return
        self._thumbnail_busy = True
        def read_all():
            return [(index, side, _thumbnail(path)) for index, side, path in requests]
        def loaded(results):
            self._thumbnail_busy = False
            if self._closed:
                return
            if generation == self._thumbnail_generation:
                for index, side, image in results:
                    label = self._labels.get((index, side))
                    if label is None or not label.winfo_exists():
                        continue
                    if image is None:
                        label.configure(text="Photo preview unavailable", image=None)
                    else:
                        reference = ctk.CTkImage(light_image=image, dark_image=image, size=image.size)
                        self._images[(index, side)] = reference
                        label.configure(text="", image=reference)
            pending = getattr(self, "_pending_thumbnails", None)
            if pending:
                self._pending_thumbnails = None
                self._load_thumbnails(*pending)
        def failed(message):
            self._thumbnail_busy = False
            if not self._closed:
                self.status.configure(text=f"Could not load one or more photo previews: {message}")
        self._thumbnail_task.run(read_all, loaded, failed)

    def _render_summary(self, choices):
        labels = {match_type: dict(options) for match_type, options in _ACTIONS.items()}
        totals = {label: 0 for options in labels.values() for label in options.values()}
        for row in self._rows:
            row_id = row.get("row_id")
            match_type = row.get("match_type", "similar")
            default = row.get("default_action", _ACTIONS.get(match_type, _ACTIONS["similar"])[0][0])
            action = choices.get(row_id, default)
            occurrences = max(1, int(row.get("occurrences", 1)))
            label = labels.get(match_type, labels["similar"]).get(action, action)
            totals[label] = totals.get(label, 0) + occurrences
        result = "Decision totals by incoming occurrence: " + (" • ".join(
            f"{label}: {count}" for label, count in totals.items()) or "No duplicate groups")
        self.summary.configure(text=result)

    def previous_page(self):
        if self._page > 0:
            self._page -= 1
            self.refresh()

    def next_page(self):
        if self._page + 1 < (len(self._rows) + _PAGE_SIZE - 1) // _PAGE_SIZE:
            self._page += 1
            self.refresh()

    def submit(self):
        if not self._current():
            self.status.configure(text="This import is no longer waiting for duplicate decisions.")
            self.refresh()
            return
        try:
            self.job.submit_duplicate_choices()
            self.close()
        except Exception as exc:
            self.status.configure(text=f"Could not continue import: {exc}")

    def cancel_import(self):
        if self._current():
            self.job.cancel()
            self.cancel_button.configure(state="disabled", text="Cancelling…")

    def close(self):
        self._closed = True
        self._thumbnail_generation += 1
        try:
            if _WINDOWS.get(self.job_id) is self:
                del _WINDOWS[self.job_id]
        except Exception:
            pass
        # Destroy only the review window; the import job and saved decisions
        # remain available if the progress panel opens a replacement window.
        try:
            self.destroy()
        except Exception:
            pass


__all__ = ["DuplicateReviewWindow"]
