"""Focus/Split camera views. Companion preview never owns business effects."""
from __future__ import annotations
import customtkinter as ctk
from core.preview_session import PreviewSession, source_key
from ui.camera_feed import CameraFeed
from ui.camera_view_dialog import CameraViewDialog, validated_selection


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
        self._dialog = None
        self._main_keys = None
        self._popup_keys = None
        self._popup_primary_feed = None
        self._popup_primary_tile = None
        self._popup_secondary_tile = None
        self._popup_labels = {}
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        bar = ctk.CTkFrame(self, fg_color='transparent')
        bar.grid(row=0, column=0, sticky='ew', pady=(0, 6))
        self.tabs = ctk.CTkSegmentedButton(bar, values=['Focus', 'Split', 'Other camera'],
                                          command=self._layout)
        self.tabs.set('Focus')
        self.tabs.pack(side='left')
        ctk.CTkButton(bar, text='Open camera view', width=150, command=self.open_view_dialog).pack(side='right')
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
        self.primary_label = ctk.CTkLabel(self.primary, text='Selected source · monitoring', wraplength=280)
        self.primary_label.grid(row=0, column=0, sticky='ew')
        self.primary_feed = CameraFeed(self.primary, width=1, height=1)
        self.primary_feed.grid(row=1, column=0, sticky='nsew')
        self.secondary_label = ctk.CTkLabel(self.secondary, text='Preview only', wraplength=280)
        self.secondary_label.grid(row=0, column=0, sticky='ew')
        self.secondary_feed = CameraFeed(self.secondary, width=1, height=1)
        self.secondary_feed.grid(row=1, column=0, sticky='nsew')
        self.secondary_feed.bind('<Double-Button-1>', lambda _: self.open_view_dialog())
        self._layout('Focus')
        self._job = self.after(100, self._tick)

    def _layout(self, mode):
        self._main_keys = None
        self._arrange(mode)

    def _arrange(self, mode):
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

    def sources(self):
        return {source_key(src): dict(src) for src in getattr(self.dashboard, '_cam_sources', {}).values()}

    def open_view_dialog(self):
        if self._closed:
            return
        if self._dialog is not None and self._dialog.winfo_exists():
            self._dialog.deiconify()
            self._dialog.lift()
            self._dialog.focus_set()
            return
        from api.camera_store import get_camera_view_preference
        sources = self.sources()
        fallback = source_key(self.dashboard._camera_requested_source)
        keys, destination = validated_selection(get_camera_view_preference(), sources, fallback)
        self._dialog = CameraViewDialog(self, sources, keys, destination,
                                        self.source_status, self.accept_view, self._dialog_closed)

    def _dialog_closed(self, dialog):
        if dialog is self._dialog:
            self._dialog = None

    def accept_view(self, keys, destination):
        sources = self.sources()
        if self._closed or not 1 <= len(keys) <= 2 or any(k not in sources for k in keys):
            return False
        if destination not in ('Live Monitor', 'Separate window'):
            return False
        # The existing owner supports one primary plus one companion. Reject
        # conflicting third-source subscriptions rather than showing wrong pixels.
        if hasattr(self, 'dashboard'):
            primary = source_key(self.dashboard._camera_requested_source)
            retained = list(self._popup_keys or ()) if destination == 'Live Monitor' else list(self._main_keys or ())
            if destination == 'Separate window' and self._main_keys is None and self.tabs.get() != 'Focus':
                current = self._choice_sources.get(self.selector.get())
                if current:
                    retained.append(source_key(current))
            if len({k for k in [*keys, *retained] if k != primary}) > 1:
                return False
        if destination == 'Live Monitor':
            self._main_keys = tuple(keys)
        else:
            self.pop_out(keys)
        self._failed_key = None
        self._last.clear()
        from api.camera_store import save_camera_view_preference
        try:
            save_camera_view_preference(keys, destination)
        except OSError:
            # A read-only preference directory must not prevent opening a view.
            pass
        return True

    def source_status(self, key):
        if key not in self.sources():
            return 'Offline · source removed'
        d = self.dashboard
        if key == source_key(d._camera_requested_source):
            if d._camera_state != 'STREAMING':
                return 'Connecting' if d._camera_state in ('CONNECTING', 'STARTING', 'STOPPING', 'RECONNECTING', 'WAITING_FOR_FIRST_FRAME') else 'Offline · retry source'
            if not getattr(d, '_primary_video_fresh', False):
                return 'Connecting · waiting for fresh video'
            return 'Analysis active · observation only' if d._observation_only else 'Analysis active'
        if self.session and self.session.key == key and not self.session.stop_event.is_set():
            if self.session.done.is_set():
                return 'Offline · Retry preview'
            status = self.session.status
            return status if status.startswith('Preview only') else 'Preview only · ' + status
        if self._failed_key == key:
            return 'Preview only · Offline · Retry preview'
        return 'Available to open · connection not checked'

    @staticmethod
    def _visible(feed):
        return (feed is not None and feed.winfo_exists() and feed.winfo_ismapped()
                and feed.winfo_toplevel().state() not in ('withdrawn', 'iconic'))

    def primary_targets(self):
        targets = [self.primary_feed]
        if self._popup_keys and source_key(self.dashboard._camera_requested_source) in self._popup_keys:
            targets.append(self._popup_primary_feed)
        return [feed for feed in targets if self._visible(feed)]

    def primary_render_key(self):
        return tuple((str(feed), feed.winfo_width(), feed.winfo_height()) for feed in self.primary_targets())

    def render_primary(self, frame):
        # The dashboard supplies its existing annotated frame; no second analysis.
        rendered = False
        for feed in self.primary_targets():
            rendered = feed.render(frame) or rendered
        return rendered

    def primary_placeholder(self, text):
        for feed in self.primary_targets():
            feed.show_placeholder(text)

    def _sync_presentation(self, primary, available):
        key = source_key(primary)
        sources = self.sources()
        if self._main_keys is not None:
            keys = tuple(k for k in self._main_keys if k in sources)
            self._main_keys = keys or (key,)
            mode = 'Split' if len(self._main_keys) == 2 else 'Focus' if key in self._main_keys else 'Other camera'
            if self.tabs.get() != mode:
                self.tabs.set(mode)
                self._arrange(mode)
        signature = (key, tuple(self._popup_keys or ()), tuple(sources))
        if self._popup is not None and self._popup_keys is not None and signature != getattr(self, '_popup_layout_signature', None):
            self._popup_layout_signature = signature
            self._popup_keys = tuple(k for k in self._popup_keys if k in sources)
            if not self._popup_keys:
                self.close_popup()
            else:
                show_primary = key in self._popup_keys
                show_secondary = any(k != key for k in self._popup_keys)
                for tile, show, column in ((self._popup_primary_tile, show_primary, 0),
                                          (self._popup_secondary_tile, show_secondary, int(show_primary))):
                    if show:
                        tile.grid(row=1, column=column, sticky='nsew')
                    else:
                        tile.grid_remove()
                self._popup.grid_columnconfigure(1, weight=int(show_primary and show_secondary), uniform='views')
        selected = list(self._main_keys or ()) + list(self._popup_keys or ())
        other = next((k for k in selected if k != key and k in sources), None)
        if other is not None:
            name = next((name for name, src in available.items() if source_key(src) == other), None)
            if name:
                self.selector.set(name)
        if self._dialog is not None:
            self._dialog.update_status(self.source_status, sources)

    def pop_out(self, keys=None):
        if self._closed:
            return
        if keys is None:
            other = next(iter(self._choice_sources.values()), None)
            keys = [source_key(other)] if other else []
        self._popup_keys = tuple(keys)
        self._popup_layout_signature = None
        if self._popup is not None and self._popup.winfo_exists():
            self._last.clear()
            self._popup.deiconify()
            self._popup.lift()
            return
        self._popup = ctk.CTkToplevel(self)
        self._popup.title('CBVMS · companion camera · PREVIEW ONLY')
        self._popup.geometry('900x600')
        self._popup.grid_columnconfigure(0, weight=1)
        self._popup.grid_rowconfigure(1, weight=1)
        # A default CTkFont remains reachable through destroyed widget cycles.
        # Worker-triggered GC can run Font.__del__ off Tk and deadlock against
        # Thread.start on Tk. A scaled tuple has no Python Tk font finalizer.
        theme_font = ctk.ThemeManager.theme['CTkFont']
        ctk.CTkLabel(self._popup, text='Camera view · F11 fullscreen · Escape to exit',
                     font=(theme_font['family'], theme_font['size'])).grid(row=0, column=0, columnspan=2)
        self._popup.grid_columnconfigure(0, weight=1, uniform='views')
        self._popup_labels = {}
        for role in ('primary', 'secondary'):
            tile = ctk.CTkFrame(self._popup)
            tile.grid_columnconfigure(0, weight=1)
            tile.grid_rowconfigure(1, weight=1)
            label = ctk.CTkLabel(tile, text='', font=(theme_font['family'], theme_font['size']), wraplength=280)
            label.grid(row=0, column=0, sticky='ew')
            self._popup_labels[role] = label
            feed = CameraFeed(tile, width=1, height=1)
            feed.grid(row=1, column=0, sticky='nsew')
            setattr(self, '_popup_' + role + '_tile', tile)
            if role == 'primary':
                self._popup_primary_feed = feed
            else:
                self._popup_feed = feed
            ctk.CTkButton(tile, text='Retry', width=70, font=(theme_font['family'], theme_font['size']),
                          command=lambda r=role: self.retry_source(r)).grid(row=2, column=0)
        # Legacy callers open the companion; presentation is reconciled next tick.
        self._popup_secondary_tile.grid(row=1, column=0, sticky='nsew')
        popup = self._popup
        self._popup.protocol('WM_DELETE_WINDOW', lambda: self._close_popup_instance(popup))
        self._popup.bind('<Destroy>', self._popup_destroyed, add='+')
        self._popup.bind('<F11>', lambda _: self._fullscreen_popup_instance(popup, None))
        self._popup.bind('<Escape>', lambda _: self._fullscreen_popup_instance(popup, False))
        self._last.clear()

    def retry_source(self, role):
        if role == 'primary':
            self.dashboard._deferred_start_camera()
        else:
            self.retry()

    def _close_popup_instance(self, popup):
        if popup is self._popup:
            self.close_popup()

    def _fullscreen_popup_instance(self, popup, fullscreen):
        if popup is self._popup and popup.winfo_exists():
            popup.attributes('-fullscreen', not popup.attributes('-fullscreen')
                             if fullscreen is None else fullscreen)

    def close_popup(self):
        popup, feed = self._popup, self._popup_feed
        primary_feed = getattr(self, '_popup_primary_feed', None)
        self._popup = self._popup_feed = self._popup_primary_feed = None
        self._popup_keys = None
        self._popup_primary_tile = self._popup_secondary_tile = None
        self._popup_labels = {}
        if primary_feed is not None and primary_feed.winfo_exists():
            primary_feed.cleanup()
        self._last.pop('window', None)
        if feed is not None and feed.winfo_exists():
            feed.cleanup()
        if popup is not None and popup.winfo_exists():
            popup.destroy()

    def _popup_destroyed(self, event):
        # Also handle direct/native destruction, not only WM_DELETE_WINDOW.
        if event.widget is getattr(self, '_popup_primary_feed', None):
            self._popup_primary_feed._photo = None
            self._popup_primary_feed._photo_size = None
        if event.widget is self._popup_feed:
            # Destroy events run on Tk even when the native close bypasses our
            # normal cleanup. Do not leave a PhotoImage for worker-thread GC.
            self._popup_feed._photo = None
            self._popup_feed._photo_size = None
        if event.widget is self._popup:
            self._popup = self._popup_feed = self._popup_primary_feed = None
            self._popup_keys = None
            self._popup_primary_tile = self._popup_secondary_tile = None
            self._popup_labels = {}
            self._last.pop('window', None)

    def _tick(self):
        if self._job is not None:
            self.after_cancel(self._job)
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
        if hasattr(self, "_sync_presentation"):
            self._sync_presentation(primary, available)
        source = available.get(self.selector.get())
        popup_needs_other = self._popup is not None and (getattr(self, '_popup_keys', None) is None or
            any(k != source_key(primary) for k in self._popup_keys))
        wanted = live and (self.tabs.get() != 'Focus' or popup_needs_other)
        # Never contend with a primary source switch or release in progress.
        primary_released = getattr(d, '_camera_worker_done', None)
        safe_to_preview = d._camera_state == 'STREAMING' or (d._camera_state == 'ERROR' and
            primary_released is not None and primary_released.is_set())
        wanted = wanted and safe_to_preview and source is not None
        key = source_key(source) if source else None
        config_changed = (hasattr(self, 'sources') and self.session is not None
                          and self.session.source != source)
        if self.session and (not wanted or self.session.key != key or config_changed):
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
        if hasattr(self, 'source_status'):
            mode = self.source_status(source_key(primary))
        primary_text = f"{primary.get('label', 'Camera')} · {mode}"
        if self.primary_label.cget('text') != primary_text:
            self.primary_label.configure(text=primary_text)
        if not live and self._popup is not None:
            self.close_popup()
        if not live and getattr(self, '_dialog', None) is not None:
            self._dialog.cancel()
        status = self.session.status if self.session else ('Unavailable — Retry preview' if self._failed_key == key and key else 'Select another saved camera')
        # A minimized/hidden consumer still owns the session, but needs no pixel
        # copy, conversion or PhotoImage upload. One poll owns both visible views;
        # mapping/resize events do not create additional render timers.
        visible = []
        for name, feed in [('tile', self.secondary_feed), ('window', self._popup_feed)]:
            if feed is None or not feed.winfo_exists() or not feed.winfo_ismapped():
                self._last.pop(name, None)
                continue
            if name == 'tile' and (not live or self.tabs.get() == 'Focus' or
                    (hasattr(self, '_visible') and not self._visible(feed))):
                continue
            if name == 'window' and self._popup.state() in ('iconic', 'withdrawn'):
                self._last.pop(name, None)
                continue
            visible.append((name, feed))
        sample = self.session.sample() if self.session and wanted and visible and self.session.key == key else None
        if sample is None and visible and self.session and wanted and status.startswith('Preview only'):
            status = 'Preview only · Connecting — waiting for fresh video'
        label = source.get('label', 'Camera') if source else 'Second camera'
        preview_status = status if status.startswith('Preview only') else f'Preview only · {status}'
        secondary_text = f'{label} · {preview_status}'
        if self.secondary_label.cget('text') != secondary_text:
            self.secondary_label.configure(text=secondary_text)
        if self._popup is not None:
            names = [src.get('label', 'Camera') for k, src in self.sources().items()
                     if k in (self._popup_keys or ())] if hasattr(self, 'sources') else [label]
            title = 'CBVMS · ' + ' + '.join(names) + ' · Camera view'
            for role, text in [('primary', primary_text), ('secondary', secondary_text)]:
                widget = getattr(self, '_popup_labels', {}).get(role)
                if widget is not None and widget.cget('text') != text:
                    widget.configure(text=text)
            if self._popup.title() != title:
                self._popup.title(title)
        for name, feed in visible:
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
        if self._dialog is not None:
            self._dialog.cancel()
        done = self.suspend()
        self.close_popup()
        self.secondary_feed.cleanup()
        return done
