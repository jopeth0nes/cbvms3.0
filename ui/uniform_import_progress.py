"""Tk-only view of a bounded, window-independent import job snapshot."""
import customtkinter as ctk

from core.uniform_dataset import CATEGORIES
from ui.components import COLOR_ACCENT, COLOR_TEXT_MUTED


class ImportProgressPanel(ctk.CTkFrame):
    def __init__(self, master, *, completed):
        super().__init__(master)
        self.completed = completed
        self.job = None
        self._review_window = None
        self._review_shown = None
        self._poll_id = None
        self._last = None
        self._finished_job = None
        self._closed = False
        self._animating = False
        self._mode = None
        self.grid_columnconfigure(1, weight=1)
        self.activity = ctk.CTkProgressBar(self, width=65, mode='indeterminate', progress_color=COLOR_ACCENT)
        self.activity.grid(row=0, column=0, padx=10, pady=8)
        self.heading = ctk.CTkLabel(self, text='', anchor='w', justify='left')
        self.heading.grid(row=0, column=1, sticky='ew', padx=6)
        self.cancel_button = ctk.CTkButton(self, text='Cancel import', width=115, command=self.cancel)
        self.cancel_button.grid(row=0, column=2, padx=10)
        self.review_button = ctk.CTkButton(self, text='Review duplicates', command=self.review_duplicates)
        self.review_button.grid(row=4, column=0, columnspan=3, pady=6)
        self.review_button.grid_remove()
        self.progress = ctk.CTkProgressBar(self, mode='indeterminate', progress_color=COLOR_ACCENT)
        self.progress.grid(row=1, column=0, columnspan=3, sticky='ew', padx=10)
        self.detail = ctk.CTkLabel(self, text='', anchor='w', justify='left', wraplength=900)
        self.detail.grid(row=2, column=0, columnspan=3, sticky='ew', padx=10, pady=3)
        self.counts = ctk.CTkLabel(self, text='', anchor='w', justify='left', wraplength=900,
                                   text_color=COLOR_TEXT_MUTED)
        self.counts.grid(row=3, column=0, columnspan=3, sticky='ew', padx=10, pady=(0, 6))

    @property
    def active(self):
        return self.job is not None and self.job.active

    def attach(self, job):
        if self._closed:
            return
        if self._poll_id is not None:
            self.after_cancel(self._poll_id)
            self._poll_id = None
        if self.job is not None and self.job.job_id != job.job_id:
            if self._review_window is not None and self._review_window.winfo_exists():
                self._review_window.destroy()
            self._review_window = None
        self.job = job
        self._last = None
        self.grid()
        self._poll()

    def _poll(self):
        self._poll_id = None
        if self._closed or self.job is None:
            return
        state = self.job.snapshot()
        if state.get('job_id') != self.job.job_id:
            return
        if state != self._last:
            self._last = state
            self._render(state)
        if state.get('active'):
            self._poll_id = self.after(80, self._poll)
        elif self._finished_job != self.job.job_id:
            self._finished_job = self.job.job_id
            self.completed(state, self.summary(state))

    @staticmethod
    def summary(state):
        outcome = 'Import cancelled' if state.get('cancelled') else 'Import finished'
        if state.get('error'):
            outcome = 'Import stopped' if not state.get('cancelled') else outcome
        remaining = state.get('unprocessed')
        remaining_text = str(remaining) if remaining is not None else 'unknown (discovery incomplete)'
        text = (f"{outcome}. Imported: {state.get('imported', 0)}; duplicate occurrences: {state.get('duplicates', 0)}; added source references: {state.get('source_added', 0)}; "
                f"skipped files: {state.get('skipped', 0)}; failed files: {state.get('failed', 0)}; "
                f"left unprocessed: {remaining_text}. "
                f"Training-ready: {state.get('training_ready', 0)}; quarantined: {state.get('quarantined', 0)}; "
                f"torso processing failed: {state.get('processing_failed', 0)}; awaiting torso processing: {state.get('awaiting', 0)}.")
        if state.get('error'):
            text += '\n' + state['error']
        return text

    def _render(self, state):
        active = bool(state.get('active'))
        phase = state.get('phase', 'discovery')
        category = CATEGORIES.get(state.get('category'), '')
        total = state.get('total')
        processed = state.get('processed', 0)
        if active and not self._animating:
            self.activity.start()
        elif not active and self._animating:
            self.activity.stop()
            self.activity.set(0)
        self._animating = active
        unknown = active and (phase == 'discovery' or total is None)
        mode = 'indeterminate' if unknown else 'determinate'
        if mode != self._mode:
            self.progress.stop()
            self.progress.configure(mode=mode)
            if unknown:
                self.progress.start()
            self._mode = mode
        cancelled = bool(state.get('cancelled'))
        self.cancel_button.configure(state='normal' if active and phase in {'discovery', 'importing', 'checking_duplicates', 'waiting_duplicates'} and not cancelled else 'disabled',
                                     text='Cancelling…' if active and cancelled and phase != 'processing' else 'Cancel import')
        current = str(state.get('current') or '').replace('\n', ' ').replace('\r', ' ')
        if phase == 'discovery' and active:
            title = f'{category} • Finding photos…'
            detail = f"Discovered: {state.get('discovered', 0)} • {current[-220:]}"
        elif phase == 'waiting_duplicates' and active:
            title = f'{category} • Waiting for duplicate decisions'
            detail = 'Review the grouped matches, then continue importing. No incoming sources have been committed.'
            self.review_button.grid()
            if self._review_shown != self.job.job_id:
                self._review_shown = self.job.job_id
                self.review_duplicates()
        elif phase == 'processing' and active:
            count, target = state.get('processing_done', 0), state.get('processing_total', 0)
            title = f'{category} • Processing torso crops…'
            percent = 100 * count / target if target else 0
            self.progress.set(count / target if target else 0)
            detail = (f"{'Import cancelled; completed saves retained.' if cancelled else 'File import finished.'} "
                      f'Torso processing: {count}/{target} photos ({percent:.0f}%).')
        elif active:
            title = f'{category} • ' + ('Checking photos for duplicates…' if phase == 'checking_duplicates' else 'Importing photos…')
            if phase == 'checking_duplicates':
                processed = state.get('checked', 0)
            percent = 100 * processed / total if total else 0
            self.progress.set(processed / total if total else 0)
            detail = f'{processed}/{total if total is not None else "?"} files ({percent:.0f}%) • {current[-220:]}'
        else:
            self.progress.stop()
            self.progress.set(processed / total if total else 0)
            title = f'{category} • ' + ('Cancelled' if cancelled else 'Stopped' if state.get('error') else 'Finished')
            detail = self.summary(state)
        if phase != 'waiting_duplicates':
            self.review_button.grid_remove()
        self.heading.configure(text=title)
        self.detail.configure(text=detail)
        self.counts.configure(text=(
            f"Newly saved unique photos: {state.get('imported', 0)} • Duplicate occurrences: {state.get('duplicates', 0)} • "
            f"Skipped files: {state.get('skipped', 0)} • Failed files: {state.get('failed', 0)}\n"
            f"Awaiting torso processing: {state.get('awaiting', 0)} • Training-ready: {state.get('training_ready', 0)} • "
            f"Quarantined: {state.get('quarantined', 0)} • Torso processing failed: {state.get('processing_failed', 0)}"))

    def review_duplicates(self):
        if self.job is None or self.job.snapshot()['phase'] != 'waiting_duplicates':
            return
        if self._review_window is not None and self._review_window.winfo_exists():
            self._review_window.lift()
            return
        from ui.uniform_duplicate_review import DuplicateReviewWindow
        self._review_window = DuplicateReviewWindow(self, self.job)

    def cancel(self):
        if self.job is not None and self.job.active:
            self.job.cancel()
            self.cancel_button.configure(state='disabled', text='Cancelling…')

    def destroy(self):
        self._closed = True
        if self._review_window is not None and self._review_window.winfo_exists():
            self._review_window.destroy()
        if self.job is not None and self.job.active:
            self.job.cancel()
        if self._poll_id is not None:
            self.after_cancel(self._poll_id)
            self._poll_id = None
        self.activity.stop()
        self.progress.stop()
        super().destroy()
