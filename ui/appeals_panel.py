"""Dedicated asynchronous appeal inbox and case review workspace."""
import queue
import json
import io
from concurrent.futures import ThreadPoolExecutor
from tkinter import messagebox
import customtkinter as ctk
from PIL import Image, ImageOps
from core.appeal_categories import CATEGORIES, BY_CODE, BY_OPTION, SELECT_CATEGORY, category_display, validate_category
from core.discipline import display_local_datetime as ts
from core.evidence_integrity import digest, original_evidence, supporting_evidence, INTEGRITY_HELP
from ui.components import COLOR_BG, COLOR_SURFACE, COLOR_TEXT, COLOR_ACCENT, COLOR_DANGER

MUTED = '#9EB3C4'
BORDER = '#2D4356'
CARD = '#182C3D'
STATUS_COLORS = {
    'pending': ('#F5C76A', '#3B3426'),
    'approved': ('#6DE0BC', '#193D38'),
    'rejected': ('#FFA0A8', '#402D3A'),
}


def _forensic_previews(data, report):
    """Prepare bounded, oriented display images on the background reader."""
    try:
        source=Image.open(io.BytesIO(data))
        source_orientation=int(source.getexif().get(274,1) or 1)
        source=ImageOps.exif_transpose(source).convert('RGB')
        source.load()
    except Exception:
        return {}
    source.thumbnail((700,340),Image.Resampling.LANCZOS)
    modes={'Original':source.copy()}
    model=report.get('model') or {}
    orientation=int(model.get('orientation') or source_orientation)
    coordinates=model.get('map_coordinates','encoded_pixels_before_exif_orientation')
    def oriented_map(blob):
        if not blob or coordinates!='encoded_pixels_before_exif_orientation':return None
        try:
            with Image.open(io.BytesIO(blob)) as candidate:
                if candidate.format != 'PNG' or max(candidate.size)>1024:
                    return None
                image=candidate.convert('L')
        except Exception:
            return None
        transposes={2:Image.Transpose.FLIP_LEFT_RIGHT,3:Image.Transpose.ROTATE_180,
            4:Image.Transpose.FLIP_TOP_BOTTOM,5:Image.Transpose.TRANSPOSE,
            6:Image.Transpose.ROTATE_270,7:Image.Transpose.TRANSVERSE,
            8:Image.Transpose.ROTATE_90}
        if orientation in transposes:image=image.transpose(transposes[orientation])
        return image.resize(source.size,Image.Resampling.BILINEAR)
    local=oriented_map(report.get('localization_png'))
    reliable=oriented_map(report.get('reliability_png'))
    if local is not None:
        colorized=ImageOps.colorize(local,black='#062237',white='#ff654d')
        modes['Map']=colorized
        modes['Overlay']=Image.blend(source,colorized,.42)
    if reliable is not None:
        modes['Reliability']=ImageOps.colorize(reliable,black='#25214b',white='#65d7bd')
    return modes


class DecisionCategoryMenu(ctk.CTkOptionMenu):
    def destroy(self):
        # CTk 5.2.2 unregisters the menu's appearance callback but leaves its
        # scaling callback alive after case navigation destroys the menu.
        ctk.ScalingTracker.remove_widget(self._dropdown_menu._set_scaling, self._dropdown_menu)
        super().destroy()


class AppealsPanel(ctk.CTkFrame):
    PAGE_SIZE = 10

    def __init__(self, master, *, database, username, on_change=None, **kwargs):
        super().__init__(master, fg_color=COLOR_BG, **kwargs)
        self.database, self.username, self.on_change = database, username, on_change
        self.offset, self.generation, self.case_id = 0, 0, None
        self.busy = False
        self._read_future = None
        self._next_read = None
        self.closed = False
        self.drafts = {}
        self.case = {}
        self._forensic_frame = None
        self._forensic_poll_job = None
        self.violation_id = None
        self._case_windows = []
        self.inbox_scroll = 0.
        self.listing = None
        self.results = queue.Queue()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='admin-appeals')
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        header = ctk.CTkFrame(self, fg_color='#1B3144', corner_radius=18,
                              border_width=1, border_color=BORDER)
        header.grid(row=0, column=0, sticky='ew', padx=18, pady=(18, 12))
        titles = ctk.CTkFrame(header, fg_color='transparent')
        titles.pack(side='left', fill='x', expand=True, padx=22, pady=18)
        ctk.CTkLabel(titles, text='STUDENT AFFAIRS  /  CASE MANAGEMENT',
                     text_color='#6DE0BC', font=ctk.CTkFont(size=11, weight='bold'),
                     anchor='w').pack(fill='x')
        self.heading = ctk.CTkLabel(titles, text='Appeals', anchor='w',
                                    wraplength=340, font=ctk.CTkFont(size=30, weight='bold'))
        self.heading.pack(fill='x', pady=(3, 2))
        self.subtitle = ctk.CTkLabel(titles,
            text='Every appeal deserves a clear, considered decision.', anchor='w',
            wraplength=300, justify='left', text_color=MUTED, font=ctk.CTkFont(size=13))
        self.subtitle.pack(fill='x')
        self.back = ctk.CTkButton(header, text='Back to Appeals', command=self.show_inbox)
        self.refresh_button = ctk.CTkButton(header, text='Refresh', width=90, command=self.refresh)
        self.refresh_button.configure(height=36, corner_radius=10, fg_color='#29485E',
                                      hover_color='#365D76')
        self.refresh_button.pack(side='right', padx=20)
        self.toolbar = ctk.CTkFrame(self, fg_color=COLOR_SURFACE, corner_radius=12)
        self.toolbar.grid(row=1, column=0, sticky='ew', padx=18)
        self.filter = ctk.CTkOptionMenu(self.toolbar, values=['Pending', 'History', 'Approved', 'Rejected', 'All'],
                                     command=lambda _: self.search_inbox())
        self.filter.configure(height=36, corner_radius=8)
        self.filter.pack(side='left', padx=(12, 0), pady=12)
        ctk.CTkButton(self.toolbar, text='Appeal History', width=115,
                      command=self.show_history).pack(side='left', padx=(8, 0))
        self.search = ctk.CTkEntry(self.toolbar, placeholder_text='Search student name or ID...', width=240,
                                   height=36, corner_radius=8, border_color=BORDER)
        self.search.pack(side='left', fill='x', expand=True, padx=8)
        self.search.bind('<Return>', lambda _: self.search_inbox())
        ctk.CTkButton(self.toolbar, text='Search', width=80, height=36,
                      command=self.search_inbox).pack(side='left', padx=(0,12))
        self.body = ctk.CTkFrame(self, fg_color='transparent')
        self.body.grid(row=2, column=0, sticky='nsew', padx=18, pady=12)
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(0, weight=1)
        self.footer = ctk.CTkFrame(self, fg_color='transparent')
        self.footer.grid(row=3, column=0, sticky='ew', padx=18, pady=(0,12))
        self.status = ctk.CTkLabel(self, text='', anchor='w', wraplength=800)
        self.status.grid(row=4, column=0, sticky='ew', padx=12)
        self._poll_job = self.after(50, self._poll)

    def _request(self, operation, callback, *, action=False):
        if self.closed or (self.busy and not action):
            return
        generation = self.generation
        self.status.configure(text='Saving decision…' if action else 'Loading…')
        if not action and self._read_future and not self._read_future.done():
            self._next_read=(generation,operation,callback)
            return
        def run():
            try:
                self.results.put((generation, callback, operation(), None, action))
            except Exception as exc:
                self.results.put((generation, callback, None, str(exc), action))
        future = self.executor.submit(run)
        if not action:
            self._read_future = future

    def _poll(self):
        if self.closed:
            return
        try:
            while True:
                generation, callback, value, error, action = self.results.get_nowait()
                if action:
                    self.busy = False
                if generation != self.generation:
                    if action and self.winfo_ismapped():
                        self.refresh()
                    continue
                if error:
                    self.status.configure(text=f'{error} Use Refresh to retry; your category and reason are preserved.')
                    if self.case_id:
                        self._enable_decisions(True)
                else:
                    self.status.configure(text='')
                    callback(value)
        except queue.Empty:
            pass
        if self._next_read and (self._read_future is None or self._read_future.done()) and not self.busy:
            generation,operation,callback=self._next_read
            self._next_read=None
            if generation==self.generation:
                self._request(operation,callback)
        self._poll_job = self.after(50, self._poll)

    def _clear(self):
        if self._forensic_poll_job is not None:
            self.after_cancel(self._forensic_poll_job)
            self._forensic_poll_job = None
        self.case = {}
        self._forensic_frame = None
        self.violation_id = None
        for window in self._case_windows:
            if window.winfo_exists():
                window.destroy()
        self._case_windows.clear()
        for parent in (self.body, self.footer):
            for widget in parent.winfo_children():
                widget.destroy()

    def _remember_reason(self):
        if (self.case_id and self.case.get('status') == 'pending'
                and hasattr(self, 'reason') and self.reason.winfo_exists()):
            selected = BY_OPTION.get(self.category.get())
            self.drafts[self.case_id] = dict(reason=self.reason.get('1.0', 'end-1c'),
                                            category=selected.code if selected else '')

    def on_show(self):
        self.refresh()

    def on_hide(self):
        self._remember_reason()
        self.generation += 1
        self._clear()

    def refresh(self):
        if self.busy:
            return
        if self.case_id:
            self.open_case(self.case_id)
        else:
            self.show_inbox()

    def search_inbox(self):
        self.offset = 0
        self.inbox_scroll = 0.
        self.show_inbox()

    def show_history(self):
        if self.busy:
            return
        self.filter.set('History')
        self.search_inbox()

    def show_inbox(self):
        if self.busy:
            return
        self._remember_reason()
        self.case_id = None
        self.generation += 1
        self.heading.configure(text='Appeal History' if self.filter.get() == 'History' else 'Appeals')
        self.back.pack_forget()
        self.toolbar.grid()
        self._clear()
        status, search, offset = self.filter.get().lower(), self.search.get(), self.offset
        self._request(lambda: self.database.get_appeal_inbox(username=self.username,
            status=status, search=search, offset=offset, limit=self.PAGE_SIZE), self._inbox_loaded)

    def _inbox_loaded(self, result):
        self.subtitle.configure(text=f"{result['total']} appeals in this view  /  {self.filter.get()}" )
        listing = self.listing = ctk.CTkScrollableFrame(self.body, fg_color=COLOR_BG,
            scrollbar_button_color=BORDER, scrollbar_button_hover_color='#49677D')
        listing.grid(row=0, column=0, sticky='nsew')
        listing.grid_columnconfigure(0, weight=1)
        if not result['rows']:
            empty = ctk.CTkFrame(listing, fg_color=CARD, corner_radius=16)
            empty.grid(row=0, column=0, sticky='ew', pady=20)
            ctk.CTkLabel(empty, text='All clear', font=ctk.CTkFont(size=24, weight='bold')).pack(pady=(32,8))
            ctk.CTkLabel(empty, text='No appeals match this filter. Try another status or student.',
                         text_color=MUTED).pack(pady=(0,32))
        for index, row in enumerate(result['rows']):
            decision_details = ''
            if row['status'] in ('approved', 'rejected'):
                decision_details = (f"\nDecided: {ts(row.get('decided_at'))}"
                                    f" by {row.get('decided_by') or 'Not recorded'}"
                                    f"\nDecision category: {category_display(row)}"
                                    f"\nReason: {row.get('admin_notes') or 'Not recorded'}")
            tone, tint = STATUS_COLORS.get(row['status'], (MUTED, BORDER))
            card = ctk.CTkFrame(listing, fg_color=CARD, corner_radius=14,
                                border_width=1, border_color=BORDER)
            card.grid(row=index, column=0, sticky='ew', pady=(0, 12), padx=2)
            card.grid_columnconfigure(1, weight=1)
            name = row['student_name'] or 'Unknown student'
            initials = ''.join(word[0] for word in name.split()[:2]).upper()
            ctk.CTkLabel(card, text=initials, width=44, height=44, corner_radius=12,
                fg_color='#28475D', text_color='#C5E2F4',
                font=ctk.CTkFont(size=16, weight='bold')).grid(row=0, column=0,
                    rowspan=2, padx=(16,12), pady=(18,8), sticky='n')
            ctk.CTkLabel(card, text=name, anchor='w', font=ctk.CTkFont(size=17, weight='bold')).grid(
                row=0, column=1, sticky='ew', pady=(16,0))
            ctk.CTkLabel(card, text=f"{row['student_id']}  /  Appeal #{row['id']}  /  Violation #{row['violation_id']}",
                         anchor='w', text_color=MUTED, font=ctk.CTkFont(size=12)).grid(row=1, column=1, sticky='ew')
            ctk.CTkLabel(card, text=row['status'].upper(), text_color=tone,
                         fg_color=tint, corner_radius=8, width=100, height=28,
                         font=ctk.CTkFont(size=11, weight='bold')).grid(row=0, column=2, padx=16, pady=(18,0))
            label = ctk.CTkLabel(card, anchor='w', justify='left', text=row['violation_type'],
                                 font=ctk.CTkFont(size=14, weight='bold'))
            label.grid(row=2, column=1, sticky='ew', pady=(12,4))
            metadata = ctk.CTkLabel(card, anchor='w', justify='left', text_color=MUTED,
                font=ctk.CTkFont(size=12), text=f"Submitted: {ts(row['submitted_at'])}{decision_details}")
            metadata.grid(row=3, column=1, sticky='ew', pady=(0,16), padx=(0,12))
            def resize_card(event, title=label, details=metadata):
                width = max(140, event.width - 230)
                title.configure(wraplength=width)
                details.configure(wraplength=width)
            card.bind('<Configure>', resize_card)
            ctk.CTkButton(card, text='Open Case  →', width=114, height=34, corner_radius=9,
                fg_color='#29485E', hover_color='#365D76',
                command=lambda aid=row['id']: self.open_case(aid)).grid(row=3, column=2, padx=16, pady=(0,16), sticky='s')
        ctk.CTkButton(self.footer, text='Previous', width=100,
            state='normal' if self.offset else 'disabled', command=lambda: self._page(-1)).pack(side='left')
        ctk.CTkLabel(self.footer, text=f"Page {self.offset//self.PAGE_SIZE+1} · {result['total']} appeals").pack(side='left', padx=16)
        ctk.CTkButton(self.footer, text='Next', width=100,
            state='normal' if self.offset+self.PAGE_SIZE < result['total'] else 'disabled',
            command=lambda: self._page(1)).pack(side='right')
        listing.update_idletasks()
        listing._parent_canvas.yview_moveto(self.inbox_scroll)

    def _page(self, delta):
        self.inbox_scroll = 0.
        self.offset = max(0,self.offset+delta*self.PAGE_SIZE)
        self.show_inbox()

    def open_alert(self, category, record_id=None):
        if record_id is not None:
            self.open_case(record_id)
        else:
            self.show_inbox()

    def open_case(self, appeal_id):
        if self.busy:
            return
        if self.case_id is None and self.listing is not None and self.listing.winfo_exists():
            self.inbox_scroll = self.listing._parent_canvas.yview()[0]
        self._remember_reason()
        self.case_id = appeal_id
        self.generation += 1
        self.heading.configure(text=f'Appeal #{appeal_id}')
        self.back.pack(side='bottom', anchor='w', padx=22, pady=(0,8),
                       before=self.heading.master)
        self.toolbar.grid_remove()
        self._clear()
        generation = self.generation
        def load():
            case = self.database.get_appeal_case(appeal_id, username=self.username)
            if case['id'] != appeal_id:
                raise ValueError('Appeal response does not match the selected case.')
            case['original'] = original_evidence(case)
            case['supporting'] = [supporting_evidence(e) for e in case['evidence']]
            case['evidence_keys'] = tuple(e['key'] for e in [case['original'], *case['supporting']])
            case['integrity_blocked'] = any(e['blocked'] for e in [case['original'], *case['supporting']])
            case['images'] = [case['original']['image'],
                              case['supporting'][0]['image'] if case['supporting'] else None]
            case['forensics'] = []
            for item in case['evidence']:
                try:
                    case['forensics'].append((item['id'], self.database.get_evidence_forensics_history(
                        item['id'], username=self.username)))
                except Exception:
                    case['forensics'].append((item['id'], []))
            case['_request'] = (generation, appeal_id, case['violation_id'])
            return case
        self._request(load, self._case_loaded)

    def _case_loaded(self, case):
        if (self.closed or case.get('_request') != (self.generation, self.case_id, case['violation_id'])
                or case['id'] != self.case_id
                or (self.violation_id is not None and self.violation_id != case['violation_id'])):
            return
        self.violation_id = case['violation_id']
        self.case = case
        self.subtitle.configure(text='Review the evidence, read the explanation, and record your decision.')
        self.heading.configure(text=f"Appeal #{case['id']} · Violation #{case['violation_id']}")
        # Only the evidence/details area scrolls at small sizes. Decisions stay in a fixed footer.
        content = ctk.CTkScrollableFrame(self.body, fg_color=CARD, corner_radius=16,
            border_width=1, border_color=BORDER)
        content.grid(row=0, column=0, sticky='nsew')
        content.grid_columnconfigure((0,1), weight=1, uniform='evidence')
        details = ctk.CTkLabel(content, justify='left', anchor='w', text=(
            f"{case['student_name']} · {case['student_id']} · {case['course'] or ''} {case['year_and_section'] or ''}\n"
            f"{case['violation_type']} · Violation: {case['violation_status']} · Appeal: {case['status'].title()}\n"
            f"Detected: {ts(case['detection_time'])}\n"
            f"Published: {ts(case['appeal_opened_at'], 'Not recorded (legacy)')}\n"
            f"Submitted: {ts(case['submitted_at'])} · Decision: {ts(case['decided_at'])}\n"
            f"Appeal deadline: {ts(case['appeal_deadline'])} · Active strike: {'Yes' if case['strike_active'] else 'No'}"))
        details.configure(font=ctk.CTkFont(size=14), text_color=COLOR_TEXT)
        details.grid(row=0,column=0,columnspan=2,sticky='ew',padx=18,pady=18)
        content.bind('<Configure>',lambda e: details.configure(wraplength=max(240,e.width-30)), add='+')
        sources = [case['original'], *(case['supporting'] or [supporting_evidence({})])]
        image_columns = [ctk.CTkFrame(content, fg_color='transparent') for _ in range(2)]
        for col, container in enumerate(image_columns):
            container.grid(row=1, column=col, sticky='nsew')
        for col, source in enumerate(sources):
            label, picture = source['label'], source['image']
            box = ctk.CTkFrame(image_columns[min(col, 1)],fg_color='transparent')
            box.pack(fill='both', expand=True, padx=6, pady=4)
            caption = ctk.CTkLabel(box,text=label, wraplength=220,
                font=ctk.CTkFont(size=13, weight='bold'), text_color='#6DE0BC')
            caption.pack(fill='x')
            box.bind('<Configure>', lambda e, text=caption: text.configure(
                wraplength=max(120, int(self._reverse_widget_scaling(e.width))-12)), add='+')
            if picture:
                thumbnail=picture.copy();thumbnail.thumbnail((240,140))
                ref=ctk.CTkImage(thumbnail,size=thumbnail.size)
                button=ctk.CTkButton(box,text='',image=ref,height=140,fg_color='transparent',
                    command=lambda p=picture,title=label,key=source['key']:self._enlarge(p,title,key))
                button.pack(); button._ref=ref; button._evidence_key=source['key']
                ctk.CTkLabel(box,text='Click image to enlarge',font=ctk.CTkFont(size=12)).pack()
            else:
                warning = ctk.CTkLabel(box,text=source['warning'] or 'Image unavailable',height=140,wraplength=220)
                warning.pack(fill='x')
                box.bind('<Configure>', lambda e, text=warning: text.configure(
                    wraplength=max(120, int(self._reverse_widget_scaling(e.width))-12)), add='+')
        forensic_row = ctk.CTkFrame(content, fg_color='#20384A', corner_radius=10)
        forensic_row.grid(row=2, column=0, columnspan=2, sticky='ew', padx=10, pady=(10, 4))
        self._forensic_frame = forensic_row
        self._render_forensics(forensic_row, case)
        ctk.CTkLabel(content,text='Student explanation',anchor='w',
            font=ctk.CTkFont(size=15, weight='bold')).grid(row=3,column=0,sticky='w',padx=18,pady=(14,4))
        ctk.CTkButton(content,text='Read full explanation',height=26,width=160,
            command=lambda:self._full_text(case['reason'])).grid(row=3,column=1,sticky='e',padx=8)
        explanation=ctk.CTkTextbox(content,height=90,wrap='word', fg_color=COLOR_BG,
            corner_radius=10, border_width=1, border_color=BORDER)
        explanation.grid(row=4,column=0,columnspan=2,sticky='ew',padx=8,pady=4)
        explanation.insert('1.0',case['reason']); explanation.configure(state='disabled')
        if case['ai_recommendation']:
            ctk.CTkLabel(content,text=f"AI advisory only: {case['ai_recommendation']}",anchor='w').grid(row=5,column=0,columnspan=2,sticky='w',padx=8)
        if case['lifecycle_origin']=='reconciliation_required':
            self.status.configure(text='Historical strike conflict: reconciliation required. Existing history has been preserved.')
        self.footer.grid_columnconfigure(0,weight=1)
        draft = self.drafts.get(self.case_id, {})
        category_row = ctk.CTkFrame(self.footer, fg_color='transparent')
        category_row.grid(row=0,column=0,sticky='ew',pady=(0,4))
        category_row.grid_columnconfigure(1,weight=1)
        ctk.CTkLabel(category_row,text='Decision category (required)',anchor='w',
            font=ctk.CTkFont(size=13,weight='bold')).grid(row=0,column=0,padx=(0,10))
        self.category = DecisionCategoryMenu(category_row,width=160,height=30,dynamic_resizing=False,
            values=[SELECT_CATEGORY,*(category.option for category in CATEGORIES)],
            command=lambda _:self._enable_decisions(not self.busy))
        self.category.grid(row=0,column=1,sticky='ew')
        selected = BY_CODE.get(draft.get('category'))
        self.category.set((selected.option if selected else SELECT_CATEGORY)
                          if case['status']=='pending' else category_display(case))
        ctk.CTkLabel(self.footer,text='Administrator decision reason (required)',anchor='w',
            font=ctk.CTkFont(size=14, weight='bold')).grid(row=1,column=0,sticky='w',pady=(2,4))
        self.reason=ctk.CTkTextbox(self.footer,height=54,wrap='word', corner_radius=10,
            border_width=1, border_color=BORDER)
        self.reason.grid(row=2,column=0,sticky='ew')
        self.reason.insert('1.0',draft.get('reason', '') if case['status']=='pending' else case['admin_notes'] or '')
        controls=ctk.CTkFrame(self.footer,fg_color='transparent')
        controls.grid(row=3,column=0,sticky='ew',pady=5)
        controls.grid_columnconfigure((0,1),weight=1)
        self.approve=ctk.CTkButton(controls,text='Approve Appeal',fg_color=COLOR_ACCENT,
                                 command=lambda:self._decide('approved'))
        self.reject=ctk.CTkButton(controls,text='Reject Appeal',fg_color=COLOR_DANGER,
                                command=lambda:self._decide('rejected'))
        self.approve.grid(row=0,column=0,sticky='ew',padx=(0,8))
        self.reject.grid(row=0,column=1,sticky='ew')
        ctk.CTkLabel(controls,text='Resolves without a strike',font=ctk.CTkFont(size=12)).grid(row=1,column=0)
        ctk.CTkLabel(controls,text='Awards one strike',font=ctk.CTkFont(size=12)).grid(row=1,column=1)
        self._enable_decisions(True)
        if case['integrity_blocked']:
            self.status.configure(text=INTEGRITY_HELP)
        if case['status']!='pending':
            self._enable_decisions(False)
            self.reason.configure(state='disabled')
            self.status.configure(text=f"{case['status'].title()} by {case['decided_by']} · {ts(case['decided_at'])}")
        if any(r.get('status') in ('pending','analyzing') for _, history in case.get('forensics', []) for r in history):
            self._schedule_forensics_poll(case['id'])

    def _render_forensics(self, frame, case):
        for child in frame.winfo_children():
            child.destroy()
        ctk.CTkLabel(frame, text='Evidence Integrity Analysis', anchor='w',
                     font=ctk.CTkFont(size=15, weight='bold')).pack(fill='x', padx=12, pady=(8, 2))
        ctk.CTkLabel(frame, text='Forensic analysis is advisory. Review the original evidence, supporting image and appeal explanation before making a decision.',
                     anchor='w', justify='left', wraplength=680, text_color=MUTED).pack(fill='x', padx=12, pady=(0, 6))
        for evidence_id, runs in case.get('forensics', []):
            latest = runs[0] if runs else None
            if not latest:
                summary = 'Analysis unavailable · manual evidence review remains available'
            elif latest['status'] == 'pending':
                summary = 'Queued for analysis'
            elif latest['status'] == 'analyzing':
                summary = 'Analysis running'
            elif latest['status'] == 'error':
                summary = f"Analysis failed ({latest.get('error_code') or 'worker error'})"
            else:
                summary = latest['classification'].replace('_', ' ').title()
            if latest:
                provenance = latest.get('provenance', {})
                c2pa_status = provenance.get('c2pa', {}).get('status', 'unavailable')
                c2pa_label = {
                    'absent':'no signed manifest','valid':'signature and trust verified',
                    'invalid':'credential verification anomaly','untrusted':'signature valid; signer trust unknown',
                    'unavailable':'verification unavailable','error':'verification failed'
                }.get(c2pa_status,'verification status unknown')
                model = latest.get('model', {})
                model_status = model.get('status', 'not_configured')
                model_label = {'not_configured':'not configured','experimental':'experimental, uncalibrated',
                    'error':'model check failed'}.get(model_status,'model status unknown')
                model_name = model.get('name') or model.get('model_name') or 'Model'
                model_version = model.get('version') or latest.get('analyzer_version') or 'unknown version'
                checked_at = latest.get('completed_at') or latest.get('created_at') or 'date unavailable'
                hash_state = latest.get('hash_status', 'mismatch').replace('_', ' ')
                hash_label = latest.get('sha256') or 'unavailable'
                summary += f" · C2PA {c2pa_label} · {model_name} {model_version} ({model_label}) · SHA-256 {hash_state}: {hash_label[:16]} · {checked_at}"
                if latest.get('model_raw_score') is not None:
                    summary += ' · raw model score (uncalibrated)'
                if latest.get('reliability') not in (None, {}, ''):
                    summary += ' · reliability details available'
            row = ctk.CTkFrame(frame, fg_color='transparent')
            row.pack(fill='x', padx=8, pady=2)
            row.grid_columnconfigure(0, weight=1)
            label = ctk.CTkLabel(row, text=f'Evidence #{evidence_id} · {summary}', anchor='w', justify='left', wraplength=560)
            label.grid(row=0, column=0, sticky='ew', padx=4, pady=3)
            if latest:
                ctk.CTkButton(row, text='View details', width=100, height=28,
                    command=lambda eid=evidence_id,rid=latest['id']: self._forensics_details(eid,rid)).grid(row=0,column=1,padx=3)
            if len(runs) > 1:
                ctk.CTkButton(row, text=f'History ({len(runs)})', width=100, height=28,
                    command=lambda eid=evidence_id: self._forensics_history(eid)).grid(row=0,column=2,padx=3)
            if case.get('status') == 'pending' and (not latest or latest['status'] in ('complete','error')):
                ctk.CTkButton(row, text='Re-run', width=76, height=28,
                    command=lambda eid=evidence_id: self._retry_forensics(eid)).grid(row=0,column=3,padx=3)

    def _schedule_forensics_poll(self, appeal_id):
        if self._forensic_poll_job is not None:
            self.after_cancel(self._forensic_poll_job)
        self._forensic_poll_job = self.after(2500, lambda: self._poll_forensics(appeal_id))

    def _poll_forensics(self, appeal_id):
        if self._forensic_poll_job is not None:
            self.after_cancel(self._forensic_poll_job)
            self._forensic_poll_job = None
        if not self.closed and self.case_id == appeal_id and not self.busy:
            generation = self.generation
            evidence_ids = [item['id'] for item in self.case.get('evidence', [])]
            def load():
                refreshed=[]
                for evidence_id in evidence_ids:
                    try:
                        refreshed.append((evidence_id,self.database.get_evidence_forensics_history(
                            evidence_id,username=self.username)))
                    except Exception:
                        refreshed.append((evidence_id,[]))
                return refreshed
            def loaded(refreshed):
                if self.case_id != appeal_id or generation != self.generation or not self._forensic_frame:
                    return
                self.case['forensics']=refreshed
                self._render_forensics(self._forensic_frame,self.case)
                if any(r.get('status') in ('pending','analyzing') for _, history in refreshed for r in history):
                    self._schedule_forensics_poll(appeal_id)
            self._request(load,loaded)

    def _retry_forensics(self, evidence_id):
        self._remember_reason()
        aid = self.case_id
        self._request(lambda: self.database.retry_evidence_forensics(evidence_id, username=self.username),
                      lambda _run: self.open_case(aid))

    def _forensics_history(self, evidence_id):
        self._request(lambda: self.database.get_evidence_forensics_history(evidence_id, username=self.username),
                      lambda runs: self._show_forensics_history(evidence_id, runs))

    def _show_forensics_history(self, evidence_id, runs):
        window=ctk.CTkToplevel(self)
        window.title(f'Evidence analysis history · #{evidence_id}')
        window.geometry('520x360')
        self._case_windows.append(window)
        listing=ctk.CTkScrollableFrame(window,fg_color=COLOR_BG)
        listing.pack(fill='both',expand=True,padx=12,pady=12)
        for index, report in enumerate(runs):
            ctk.CTkButton(listing,text=f"{report['status']} · {report.get('classification','inconclusive')} · {report.get('created_at','date unavailable')}",
                anchor='w',command=lambda rid=report['id']:self._forensics_details(evidence_id,rid)).pack(fill='x',pady=3)

    def _forensics_details(self, evidence_id, run_id):
        appeal_id=self.case_id
        def load():
            report=self.database.get_evidence_forensics_report(run_id,username=self.username)
            item=next((e for e in self.case.get('evidence',[]) if e.get('id')==evidence_id),None)
            if (not report or not item or report.get('evidence_id')!=evidence_id
                    or report.get('appeal_id')!=appeal_id):
                return report,None
            data=item.get('file_data')
            if (not report.get('valid') or not data
                    or digest(data)!=report.get('sha256')):
                return report,None
            return report,_forensic_previews(bytes(data),report)
        self._request(load,lambda result:self._show_forensics_details(result,evidence_id,run_id,appeal_id))

    def _show_forensics_details(self, result, evidence_id, run_id, appeal_id):
        if self.closed or self.case_id!=appeal_id:
            return
        report,modes=result
        window = ctk.CTkToplevel(self)
        window.title(f"Evidence analysis · #{evidence_id}")
        window.geometry('760x640')
        self._case_windows.append(window)
        if not report or not modes:
            ctk.CTkLabel(window,text='Evidence hash is unavailable or no longer matches this analysis. Technical signals and maps are withheld.',
                justify='left',wraplength=680).pack(fill='x',padx=16,pady=16)
            return
        summary={key:report.get(key) for key in ('status','classification','hash_status','sha256',
            'analyzer_version','created_at','completed_at','provenance','model','model_raw_score',
            'model_calibrated','reliability','facts','signals','risk','error_code')}
        advisory=ctk.CTkLabel(window,text='Forensic analysis is advisory. Review the original evidence, supporting image and appeal explanation before making a decision.',
            justify='left',wraplength=700,text_color=MUTED)
        advisory.pack(fill='x',padx=14,pady=(12,6))
        summary_box=ctk.CTkTextbox(window,height=125,wrap='word')
        summary_box.pack(fill='x',padx=14,pady=(0,8))
        summary_box.insert('1.0',json.dumps(summary,indent=2,ensure_ascii=False))
        summary_box.configure(state='disabled')
        display=ctk.CTkLabel(window,text='');display.pack(fill='both',expand=True,padx=12,pady=8)
        current={'image':None}
        def show_mode(name):
            picture=modes[name]
            ref=ctk.CTkImage(picture,size=picture.size)
            display.configure(image=ref,text='');display.image_ref=ref
            current['image']=name
            legend=('Model indication, not proof. Higher map intensity marks stronger model response; raw output is uncalibrated.'
                    if name in ('Map','Overlay','Reliability') else 'Original supporting image, oriented using its EXIF display transform.')
            legend_label.configure(text=legend)
        toggles=ctk.CTkFrame(window,fg_color='transparent');toggles.pack(fill='x',padx=12)
        legend_label=ctk.CTkLabel(window,text='',wraplength=700,text_color=MUTED)
        legend_label.pack(fill='x',padx=14,pady=(4,10))
        for name in ('Original','Overlay','Map','Reliability'):
            if name in modes:
                ctk.CTkButton(toggles,text=name,width=100,command=lambda selected=name:show_mode(selected)).pack(side='left',padx=4)
        show_mode('Original')

    def _enable_decisions(self, enabled):
        allowed = enabled and not self.busy and self.case.get('status') == 'pending'
        selected = BY_OPTION.get(self.category.get()) if hasattr(self,'category') and self.category.winfo_exists() else None
        for widget_name in ('category','reason'):
            widget=getattr(self,widget_name,None)
            if widget and widget.winfo_exists():
                widget.configure(state='normal' if allowed else 'disabled')
        for name in ('approve', 'reject'):
            button = getattr(self, name, None)
            if button and button.winfo_exists():
                blocked = ((name == 'reject' and self.case.get('integrity_blocked')) or
                           (selected is not None and selected.decision != ('approved' if name=='approve' else 'rejected')))
                button.configure(state='normal' if allowed and not blocked else 'disabled')

    def _decide(self, decision):
        if self.busy or self.case.get('id') != self.case_id or self.case.get('status')!='pending':
            return
        if decision == 'rejected' and self.case.get('integrity_blocked'):
            self.status.configure(text=INTEGRITY_HELP)
            return
        selected=BY_OPTION.get(self.category.get())
        try:
            category=validate_category(selected.code if selected else None,decision)
        except ValueError as exc:
            self.status.configure(text=str(exc))
            return
        notes=self.reason.get('1.0','end-1c').strip()
        if not notes:
            self.status.configure(text='Enter a decision reason before saving.')
            return
        effect='resolve this violation without a strike' if decision=='approved' else 'award one strike for this violation'
        if not messagebox.askyesno('Save appeal decision',f'Category: {category.label}\nThis will {effect}. Save the decision?',parent=self):
            return
        self._remember_reason()
        self.busy=True
        self._enable_decisions(False)
        aid=self.case_id
        vid, evidence_keys = self.violation_id, self.case['evidence_keys']
        def save():
            ok=self.database.update_appeal_decision(aid,decision,notes,decided_by=self.username,
                expected_violation_id=vid, expected_evidence_keys=evidence_keys, decision_category_code=category.code)
            outcome=self.database.get_appeal_case(aid,username=self.username)
            if not ok and outcome['status']=='pending':
                raise ValueError('Decision could not be saved. Refresh to check the evidence integrity, case association, and administrator access.')
            return outcome
        def saved(outcome):
            self.drafts.pop(aid,None)
            self.open_case(aid)  # reload actual outcome if another administrator won
            if self.on_change:
                self.on_change()
        self._request(save,saved,action=True)

    def _enlarge(self,picture,title,key=None):
        window=ctk.CTkToplevel(self); window.title(title); window.geometry('900x700')
        self._case_windows.append(window)
        window._evidence_key = key
        window._evidence_image = picture
        window.grid_columnconfigure(0,weight=1);window.grid_rowconfigure(0,weight=1)
        label=ctk.CTkLabel(window,text='');label.grid(row=0,column=0,sticky='nsew')
        def resize(event):
            if event.widget!=window:return
            copy=picture.copy();copy.thumbnail((max(50,event.width-30),max(50,event.height-30)))
            label._ref=ctk.CTkImage(copy,size=copy.size);label.configure(image=label._ref)
        window.bind('<Configure>',resize)

    def _full_text(self,text):
        window=ctk.CTkToplevel(self);window.title('Student explanation');window.geometry('650x400')
        self._case_windows.append(window)
        box=ctk.CTkTextbox(window,wrap='word');box.pack(fill='both',expand=True,padx=12,pady=12)
        box.insert('1.0',text);box.configure(state='disabled')

    def destroy(self):
        self.closed=True
        self.generation+=1
        self.after_cancel(self._poll_job)
        if self._forensic_poll_job is not None:
            self.after_cancel(self._forensic_poll_job)
            self._forensic_poll_job = None
        self.executor.shutdown(wait=False,cancel_futures=True)
        super().destroy()
