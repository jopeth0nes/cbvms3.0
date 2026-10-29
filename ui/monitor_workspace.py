"""Focus/Split camera views. Companion preview never owns business effects."""
from __future__ import annotations
import customtkinter as ctk
from core.preview_session import PreviewSession, source_key
from ui.camera_feed import CameraFeed


class MonitorWorkspace(ctk.CTkFrame):
    def __init__(self, master, dashboard, **kwargs):
        super().__init__(master, fg_color='transparent', **kwargs)
        self.dashboard = dashboard
        self.session = None
        self._closed = False
        self._job = None
        self._popup = None
        self._popup_feed = None
        self._last = {}
        self._failed_key = None
        self._choice_sources = {}
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        bar = ctk.CTkFrame(self, fg_color='transparent')
        bar.grid(row=0, column=0, sticky='ew', pady=(0, 6))
        self.tabs = ctk.CTkSegmentedButton(bar, values=['Focus', 'Split', 'Other camera'],
                                          command=self._layout)
        self.tabs.set('Focus')
        self.tabs.pack(side='left')
        ctk.CTkButton(bar, text='Pop out preview', width=125, command=self.pop_out).pack(side='right')
        options = ctk.CTkFrame(self, fg_color='transparent')
        options.grid(row=1, column=0, sticky='ew', pady=(0, 6))
        ctk.CTkLabel(options, text='Second camera:').pack(side='left')
        self.selector = ctk.CTkOptionMenu(options, values=['No other camera'],
                                          command=self._choose, width=170)
        self.selector.pack(side='left', padx=8)
        ctk.CTkButton(options, text='Retry preview', width=100, command=self.retry).pack(side='right')
        self.body = ctk.CTkFrame(self, fg_color='transparent')
        self.body.grid(row=2, column=0, sticky='nsew')
        self.body.grid_rowconfigure(0, weight=1)
        self.primary = ctk.CTkFrame(self.body)
        self.secondary = ctk.CTkFrame(self.body)
        for tile in (self.primary, self.secondary):
            tile.grid_columnconfigure(0, weight=1)
            tile.grid_rowconfigure(1, weight=1)
        self.primary_label = ctk.CTkLabel(self.primary, text='Selected source · monitoring')
        self.primary_label.grid(row=0, column=0, sticky='ew')
        self.primary_feed = CameraFeed(self.primary, width=1, height=1)
        self.primary_feed.grid(row=1, column=0, sticky='nsew')
        self.secondary_label = ctk.CTkLabel(self.secondary, text='Preview only')
        self.secondary_label.grid(row=0, column=0, sticky='ew')
        self.secondary_feed = CameraFeed(self.secondary, width=1, height=1)
        self.secondary_feed.grid(row=1, column=0, sticky='nsew')
        self.secondary_feed.bind('<Double-Button-1>', lambda _: self.pop_out())
        self._layout('Focus')
        self._job = self.after(100, self._tick)

    def _layout(self, mode):
        self.primary.grid_remove()
        self.secondary.grid_remove()
        self.body.grid_columnconfigure(0, weight=1, uniform='tiles' if mode == 'Split' else '')
        self.body.grid_columnconfigure(1, weight=1 if mode == 'Split' else 0,
                                       uniform='tiles' if mode == 'Split' else '')
        if mode != 'Other camera':
            self.primary.grid(row=0, column=0, sticky='nsew', padx=(0, 4 if mode == 'Split' else 0))
        if mode != 'Focus':
            self.secondary.grid(row=0, column=1 if mode == 'Split' else 0, sticky='nsew')
        self._last.clear()

    def _choose(self, _value):
        self.suspend()
        self._failed_key = None
        self._last.clear()

    def retry(self):
        self._choose(self.selector.get())

    def suspend(self):
        if self.session:
            return self.session.stop()
        import threading
        done = threading.Event()
        done.set()
        return done

    def pop_out(self):
        if self._popup is not None and self._popup.winfo_exists():
            self._popup.lift()
            return
        self._popup = ctk.CTkToplevel(self)
        self._popup.title('CBVMS · companion camera · PREVIEW ONLY')
        self._popup.geometry('900x600')
        self._popup.grid_columnconfigure(0, weight=1)
        self._popup.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(self._popup, text='Preview only — no recognition, attendance, or violations').grid(row=0, column=0)
        self._popup_feed = CameraFeed(self._popup)
        self._popup_feed.grid(row=1, column=0, sticky='nsew')
        self._popup.protocol('WM_DELETE_WINDOW', self.close_popup)
        self._popup.bind('<F11>', lambda _: self._popup.attributes('-fullscreen', not self._popup.attributes('-fullscreen')))
        self._popup.bind('<Escape>', lambda _: self._popup.attributes('-fullscreen', False))
        self._last.clear()

    def close_popup(self):
        if self._popup_feed:
            self._popup_feed.cleanup()
        if self._popup:
            self._popup.destroy()
        self._popup = self._popup_feed = None
        self._last.clear()

    def _tick(self):
        self._job = None
        if self._closed:
            return
        d = self.dashboard
        live = getattr(d, '_active_nav', None) == 'live' and not d._closed.is_set()
        primary = d._camera_requested_source
        available = {name: dict(src) for name, src in d._cam_sources.items()
                     if source_key(src) != source_key(primary)}
        if available != self._choice_sources:
            old = self.selector.get()
            self._choice_sources = available
            names = list(available) or ['No other camera']
            self.selector.configure(values=names)
            self.selector.set(old if old in available else names[0])
            self._failed_key = None
        source = available.get(self.selector.get())
        wanted = live and (self.tabs.get() != 'Focus' or self._popup is not None)
        # Never contend with a primary source switch or release in progress.
        wanted = wanted and d._camera_state == 'STREAMING' and source is not None
        key = source_key(source) if source else None
        if self.session and (not wanted or self.session.key != key):
            self.session.stop()
        if self.session and self.session.done.is_set():
            if not self.session.stop_event.is_set():
                self._failed_key = self.session.key
                self.secondary_label.configure(text=self.session.status)
            self.session = None
            self._last.clear()
        if wanted and self.session is None and self._failed_key != key:
            self.session = PreviewSession(source)
            self.session.start()
        mode = 'Analysis · observation only' if d._observation_only else 'Analysis enabled'
        self.primary_label.configure(text=f"{primary.get('label', 'Camera')} · {mode}")
        if not live and self._popup is not None:
            self.close_popup()
        sample = self.session.sample() if self.session and wanted else None
        status = self.session.status if self.session else ('Unavailable — Retry preview' if self._failed_key == key and key else 'Select another saved camera')
        label = source.get('label', 'Camera') if source else 'Second camera'
        preview_status = status if status.startswith('Preview only') else f'Preview only · {status}'
        self.secondary_label.configure(text=f'{label} · {preview_status}')
        if self._popup is not None:
            self._popup.title(f'CBVMS · {label} · PREVIEW ONLY')
        for name, feed in [('tile', self.secondary_feed), ('window', self._popup_feed)]:
            if feed is None or (name == 'tile' and self.tabs.get() == 'Focus'):
                continue
            if sample is None:
                feed.show_placeholder(status)
                self._last.pop(name, None)
            else:
                render_key = (sample.frame_id, feed.winfo_width(), feed.winfo_height())
                if self._last.get(name) != render_key:
                    if feed.render(sample.frame):
                        self._last[name] = render_key
        self._job = self.after(50, self._tick)

    def close(self):
        self._closed = True
        if self._job:
            self.after_cancel(self._job)
            self._job = None
        done = self.suspend()
        self.close_popup()
        self.secondary_feed.cleanup()
        return done
