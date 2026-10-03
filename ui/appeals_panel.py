"""Dedicated asynchronous appeal inbox and case review workspace."""
import queue
from concurrent.futures import ThreadPoolExecutor
from tkinter import messagebox
import customtkinter as ctk
from core.discipline import display_local_datetime as ts
from core.evidence_integrity import original_evidence, supporting_evidence, INTEGRITY_HELP
from ui.components import COLOR_BG, COLOR_SURFACE, COLOR_TEXT, COLOR_ACCENT, COLOR_DANGER

MUTED = '#9EB3C4'
BORDER = '#2D4356'
CARD = '#182C3D'
STATUS_COLORS = {
    'pending': ('#F5C76A', '#3B3426'),
    'approved': ('#6DE0BC', '#193D38'),
    'rejected': ('#FFA0A8', '#402D3A'),
}


class AppealsPanel(ctk.CTkFrame):
    PAGE_SIZE = 10

    def __init__(self, master, *, database, username, on_change=None, **kwargs):
        super().__init__(master, fg_color=COLOR_BG, **kwargs)
        self.database, self.username, self.on_change = database, username, on_change
        self.offset, self.generation, self.case_id = 0, 0, None
        self.busy = False
        self._read_future = None
        self.closed = False
        self.drafts = {}
        self.case = {}
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
        def run():
            try:
                self.results.put((generation, callback, operation(), None, action))
            except Exception as exc:
                self.results.put((generation, callback, None, str(exc), action))
        if not action and self._read_future:
            self._read_future.cancel()
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
                    self.status.configure(text=f'{error} Use Refresh to retry; your reason is preserved.')
                    if self.case_id:
                        self._enable_decisions(True)
                else:
                    self.status.configure(text='')
                    callback(value)
        except queue.Empty:
            pass
        self._poll_job = self.after(50, self._poll)

    def _clear(self):
        self.case = {}
        self.violation_id = None
        for window in self._case_windows:
            if window.winfo_exists():
                window.destroy()
        self._case_windows.clear()
        for parent in (self.body, self.footer):
            for widget in parent.winfo_children():
                widget.destroy()

    def _remember_reason(self):
        if self.case_id and hasattr(self, 'reason') and self.reason.winfo_exists():
            self.drafts[self.case_id] = self.reason.get('1.0', 'end-1c')

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
        ctk.CTkLabel(content,text='Student explanation',anchor='w',
            font=ctk.CTkFont(size=15, weight='bold')).grid(row=2,column=0,sticky='w',padx=18,pady=(14,4))
        ctk.CTkButton(content,text='Read full explanation',height=26,width=160,
            command=lambda:self._full_text(case['reason'])).grid(row=2,column=1,sticky='e',padx=8)
        explanation=ctk.CTkTextbox(content,height=90,wrap='word', fg_color=COLOR_BG,
            corner_radius=10, border_width=1, border_color=BORDER)
        explanation.grid(row=3,column=0,columnspan=2,sticky='ew',padx=8,pady=4)
        explanation.insert('1.0',case['reason']); explanation.configure(state='disabled')
        if case['ai_recommendation']:
            ctk.CTkLabel(content,text=f"AI advisory only: {case['ai_recommendation']}",anchor='w').grid(row=4,column=0,columnspan=2,sticky='w',padx=8)
        if case['lifecycle_origin']=='reconciliation_required':
            self.status.configure(text='Historical strike conflict: reconciliation required. Existing history has been preserved.')
        self.footer.grid_columnconfigure(0,weight=1)
        ctk.CTkLabel(self.footer,text='Administrator decision reason (required)',anchor='w',
            font=ctk.CTkFont(size=14, weight='bold')).grid(row=0,column=0,sticky='w',pady=(4,6))
        self.reason=ctk.CTkTextbox(self.footer,height=64,wrap='word', corner_radius=10,
            border_width=1, border_color=BORDER)
        self.reason.grid(row=1,column=0,sticky='ew')
        self.reason.insert('1.0',self.drafts.get(self.case_id, '') if case['status']=='pending' else case['admin_notes'] or '')
        controls=ctk.CTkFrame(self.footer,fg_color='transparent')
        controls.grid(row=2,column=0,sticky='ew',pady=5)
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

    def _enable_decisions(self, enabled):
        allowed = enabled and self.case.get('status') == 'pending'
        for name in ('approve', 'reject'):
            button = getattr(self, name, None)
            if button and button.winfo_exists():
                blocked = name == 'reject' and self.case.get('integrity_blocked')
                button.configure(state='normal' if allowed and not blocked else 'disabled')

    def _decide(self, decision):
        if self.busy or self.case.get('id') != self.case_id:
            return
        if decision == 'rejected' and self.case.get('integrity_blocked'):
            self.status.configure(text=INTEGRITY_HELP)
            return
        notes=self.reason.get('1.0','end-1c').strip()
        if not notes:
            self.status.configure(text='Enter a decision reason before saving.')
            return
        effect='resolve this violation without a strike' if decision=='approved' else 'award one strike for this violation'
        if not messagebox.askyesno('Save appeal decision',f'This will {effect}. Save the decision?',parent=self):
            return
        self._remember_reason()
        self.busy=True
        self._enable_decisions(False)
        aid=self.case_id
        vid, evidence_keys = self.violation_id, self.case['evidence_keys']
        def save():
            ok=self.database.update_appeal_decision(aid,decision,notes,decided_by=self.username,
                expected_violation_id=vid, expected_evidence_keys=evidence_keys)
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
        self.executor.shutdown(wait=False,cancel_futures=True)
        super().destroy()
