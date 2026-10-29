"""Shared staff Campus Sightings workspace (sidebar and Records use this view)."""
from datetime import datetime, timedelta
from io import BytesIO
from PIL import Image, ImageTk

from database.security_events import UNKNOWN_COLUMNS
import tkinter as tk
from tkinter import ttk, filedialog
import customtkinter as ctk

from core.academics import COLLEGES, COURSES, YEAR_LEVELS, NEEDS_REVIEW, courses_for
from core.attendance import TITLE, MANILA, SUMMARY_COLUMNS, EVENT_COLUMNS, display_time, validate_filters, export_csv
from core.attendance_writer import writer_status, retry_writers
from ui.background_task import BackgroundTask
from ui.components import COLOR_BG, COLOR_TEXT, COLOR_TEXT_MUTED, COLOR_ACCENT, body_small_font, heading_font


class AttendancePanel(ctk.CTkFrame):
    def __init__(self, master, *, database, username='admin', unknown=False):
        super().__init__(master, fg_color='transparent')
        self.database, self.username = database, username
        self.task, self.export_task, self.settings_task = (BackgroundTask(self) for _ in range(3))
        self.snapshot_task = BackgroundTask(self)
        self.snapshot_window = None
        self.preview_generation = 0
        self.preview_id = None
        self.preview_picture = None
        self.page, self.total, self.generation = 0, 0, 0
        self._pending_exact_event_id = None
        self._pending_exact_unknown_id = None
        self.applied = None
        self.rows = []
        self.columnconfigure(0,weight=1)
        self.rowconfigure(5,weight=1)
        self.bind('<Configure>',self._resize,add='+')
        self.heading=ctk.CTkLabel(self,text=TITLE,font=heading_font(20),anchor='w')
        self.heading.grid(row=0,column=0,sticky='ew')
        self.description = ctk.CTkLabel(self,text='Confirmed identities seen by campus cameras. Each accepted event increments the count; repeated sightings '
            'within the configured cooldown are excluded across cameras. No absence, lateness, class attendance, duration or checkout is inferred. '
            'Summary classification describes its earliest accepted event; use Sighting Events for changes within a day. Legacy totals are unknown.',
            wraplength=950,justify='left',anchor='w',text_color=COLOR_TEXT_MUTED,font=body_small_font())
        self.description.grid(row=1,column=0,sticky='ew',pady=6)
        self.known_description = self.description.cget('text')
        form=self.form=ctk.CTkScrollableFrame(self,height=145,fg_color='transparent')
        form.grid(row=2,column=0,sticky='ew')
        for col in range(3):
            form.columnconfigure(col,weight=1,uniform='attendance_filters')
        today=datetime.now(MANILA).date().isoformat()
        self.filters={k:tk.StringVar(value=today if k in ('start','end') else 'All' if k in
            ('semester_id','college_department','course','report_year_level','source_id') else '')
            for k in ('start','end','semester_id','college_department','course','report_year_level','report_section','student_id','name','source_id','search')}
        self.menus={}
        self.semesters={'All':''}
        self.sources={'All':''}
        labels=('From date (Manila)','To date (Manila)','Semester','College','Course','Year Level','Section','Student ID (exact)','Name contains','Camera / source','Encounter / reference ID contains')
        values={'semester_id':['All'],'college_department':['All',*COLLEGES,NEEDS_REVIEW],
                'course':['All',*(p[1] for p in COURSES.values()),NEEDS_REVIEW],
                'report_year_level':['All',*YEAR_LEVELS],'source_id':['All']}
        self.filter_widgets = {}
        for index,((key,var),label) in enumerate(zip(self.filters.items(),labels)):
            row,col=2*(index//3),index%3
            caption = ctk.CTkLabel(form,text=label,anchor='w',font=body_small_font())
            caption.grid(row=row,column=col,sticky='ew',padx=4)
            if key in values:
                if key=='report_year_level':
                    widget=ctk.CTkComboBox(form,variable=var,values=values[key],width=140)
                else:
                    widget=ctk.CTkOptionMenu(form,variable=var,values=values[key],width=140,dynamic_resizing=False,
                        command=self._college_changed if key=='college_department' else lambda _:None)
                self.menus[key]=widget
            else:
                widget=ctk.CTkEntry(form,textvariable=var,width=140)
            widget.grid(row=row+1,column=col,sticky='ew',padx=4,pady=(0,6))
            self.filter_widgets[key] = (caption,widget)
        actions=self.actions=ctk.CTkFrame(self,fg_color='transparent')
        actions.grid(row=3,column=0,sticky='ew',pady=5)
        self.view=tk.StringVar(value='Daily Summary')
        for col in range(3):
            actions.columnconfigure(col,weight=1)
        ctk.CTkOptionMenu(actions,variable=self.view,values=['Daily Summary','Sighting Events','Unknown / Unrecognized'],command=lambda _:self.refresh()).grid(row=0,column=0,sticky='ew',padx=3,pady=3)
        for index,(text,command) in enumerate([('Apply / Retry',self.refresh),('Clear Filters',self.clear_filters),
                ('Export selected',lambda:self.export(True)),('Export all matching',lambda:self.export(False))],start=1):
            ctk.CTkButton(actions,text=text,width=115,command=command).grid(row=index//3,column=index%3,sticky='ew',padx=3,pady=3)
        self.snapshot_button = ctk.CTkButton(actions,text='Open snapshot',command=self.open_snapshot,state='disabled')
        self.snapshot_button.grid(row=1,column=2,sticky='ew',padx=3,pady=3)
        self.message=ctk.CTkLabel(self,text='Choose filters and Apply. Exports use the applied filters.',anchor='w',wraplength=950)
        self.message.grid(row=4,column=0,sticky='ew')
        self.split = tk.PanedWindow(self, orient='horizontal', bg=COLOR_BG, sashwidth=8, borderwidth=0)
        self.split.grid(row=5,column=0,sticky='nsew')
        table=ctk.CTkFrame(self.split,fg_color=COLOR_BG)
        self.split.add(table, minsize=220, stretch='always')
        self.detail=ctk.CTkFrame(self.split,fg_color=COLOR_BG,width=340)
        self.detail.columnconfigure(0,weight=1)
        self.detail.rowconfigure(1,weight=1)
        self.details=ctk.CTkLabel(self.detail,text='Select one encounter to view its saved evidence.',text_color=COLOR_TEXT,justify='left',wraplength=320)
        self.details.grid(row=0,column=0,sticky='ew',padx=8)
        self.preview=tk.Canvas(self.detail,bg=COLOR_BG,highlightthickness=0,width=320,height=180)
        self.preview.grid(row=1,column=0,sticky='nsew')
        self.preview.bind('<Configure>',lambda _:self._render_preview())
        self.preview.bind('<Button-1>',lambda _:self.open_snapshot())
        self.evidence_kind=tk.StringVar(value='Context')
        ctk.CTkOptionMenu(self.detail,variable=self.evidence_kind,values=['Context','Face crop'],
            command=lambda _:self._selection_changed(force=True)).grid(row=2,column=0,sticky='ew',padx=8)
        self.evidence_label=ctk.CTkLabel(self.detail,text='',text_color=COLOR_TEXT)
        self.evidence_label.grid(row=3,column=0,sticky='ew')
        ctk.CTkButton(self.detail,text='Enlarge saved evidence',command=self.open_snapshot).grid(row=4,column=0,sticky='ew',padx=8,pady=4)
        self.preview_state='Select one encounter.'
        table.columnconfigure(0,weight=1);table.rowconfigure(0,weight=1)
        self.tree=ttk.Treeview(table,show='headings',selectmode='extended')
        self.tree.grid(row=0,column=0,sticky='nsew')
        for orientation in ('vertical','horizontal'):
            vertical=orientation=='vertical'
            bar=ttk.Scrollbar(table,orient=orientation,command=self.tree.yview if vertical else self.tree.xview)
            bar.grid(row=0 if vertical else 1,column=1 if vertical else 0,sticky='ns' if vertical else 'ew')
            self.tree.configure(**{'yscrollcommand' if vertical else 'xscrollcommand':bar.set})
        self.tree.bind('<<TreeviewSelect>>',self._selection_changed)
        footer=ctk.CTkFrame(self,fg_color='transparent')
        footer.grid(row=6,column=0,sticky='ew',pady=5)
        self.previous=ctk.CTkButton(footer,text='Previous',width=85,command=lambda:self.change_page(-1))
        self.previous.pack(side='left')
        self.next=ctk.CTkButton(footer,text='Next',width=85,command=lambda:self.change_page(1))
        self.next.pack(side='left',padx=4)
        self.page_size=tk.StringVar(value='50')
        ctk.CTkOptionMenu(footer,variable=self.page_size,values=['25','50','100','250'],width=75,command=lambda _:self.refresh()).pack(side='left')
        self.summary=ctk.CTkLabel(footer,text='0 matching records')
        self.summary.pack(side='left',padx=8)
        settings=self.settings=ctk.CTkFrame(self,fg_color='transparent')
        settings.grid(row=7,column=0,sticky='ew',pady=3)
        ctk.CTkButton(settings,text='Retry sighting writer',width=150,command=self.retry_writer).pack(side='left')
        self.cooldown=tk.StringVar(value='300')
        ctk.CTkEntry(settings,textvariable=self.cooldown,width=65).pack(side='right')
        ctk.CTkButton(settings,text='Set student cooldown (seconds)',width=195,command=self.save_cooldown).pack(side='right',padx=4)
        self.writer_label=ctk.CTkLabel(self,text='',anchor='w',text_color=COLOR_TEXT_MUTED,wraplength=950)
        self.writer_label.grid(row=8,column=0,sticky='ew')
        self._status_job=self.after(500,self._writer_status)
        self.bind('<Destroy>',self._destroy,add='+')
        self.bind('<Unmap>',self._hidden,add='+')
        self.compact=ctk.CTkFrame(self,fg_color='transparent')
        for col in range(3): self.compact.columnconfigure(col,weight=1)
        self.period=tk.StringVar(value='All time')
        ctk.CTkOptionMenu(self.compact,variable=self.period,values=['Today','Last 7 days','All time'],command=self._period_changed).grid(row=0,column=0,sticky='ew',padx=3)
        self.camera_menu=ctk.CTkOptionMenu(self.compact,variable=self.filters['source_id'],values=['All'],command=lambda _:self.refresh())
        self.camera_menu.grid(row=0,column=1,sticky='ew',padx=3)
        search=ctk.CTkEntry(self.compact,textvariable=self.filters['search'],placeholder_text='Encounter reference')
        search.grid(row=0,column=2,sticky='ew',padx=3)
        search.bind('<Return>',lambda _:self.refresh())
        ctk.CTkButton(self.compact,text='Refresh / apply search',command=self.refresh).grid(row=1,column=0,sticky='ew',padx=3,pady=4)
        self.auto=tk.BooleanVar(value=True)
        ctk.CTkCheckBox(self.compact,text='Auto refresh (10s)',variable=self.auto).grid(row=1,column=1,padx=3)
        ctk.CTkButton(self.compact,text='Attendance views',command=self._known_view).grid(row=1,column=2,sticky='ew',padx=3)
        for col,(label,command) in enumerate((('Export selected',lambda:self.export(True)),('Export all matching',lambda:self.export(False)),('Clear filters',self.clear_filters))):
            ctk.CTkButton(self.compact,text=label,command=command).grid(row=2,column=col,sticky='ew',padx=3,pady=3)
        for child in self.compact.winfo_children():
            child.grid_configure(row=int(child.grid_info()['row'])+1)
        for col,label in enumerate(('Date range (Manila)','Recorded camera','Encounter reference / presence ID')):
            ctk.CTkLabel(self.compact,text=label,anchor='w').grid(row=0,column=col,sticky='ew',padx=3)
        self._auto_job=self.after(10000,self._auto_refresh)
        if unknown:
            self.view.set('Unknown / Unrecognized')
            self.filters['start'].set('');self.filters['end'].set('')

    def _resize(self,event):
        if event.widget in (self,self._canvas):
            orient='vertical' if event.width < 900 else 'horizontal'
            if str(self.split.cget('orient')) != orient:
                self.split.configure(orient=orient)
                if str(self.detail) in self.split.panes():
                    self.split.paneconfigure(self.detail,minsize=300 if orient=='vertical' else 260)
                self.after_idle(lambda:self.split.sash_place(0, int(self.split.winfo_width()*.6), int(self.split.winfo_height()*.45)) if self.split.winfo_exists() and len(self.split.panes()) > 1 else None)
            self.details.configure(wraplength=max(200,self.detail.winfo_width()-20))
            for widget in self.winfo_children():
                if isinstance(widget,ctk.CTkLabel):
                    widget.configure(wraplength=max(250,event.width-20))

    def _destroy(self,event):
        if event.widget is self:
            self.generation+=1
            self.preview_generation+=1
            self.after_cancel(self._auto_job)
            self._close_snapshot()
            if self._status_job:
                self.after_cancel(self._status_job)
                self._status_job=None

    def _writer_status(self):
        self.writer_label.configure(text='This session · '+writer_status(self.database))
        self._status_job=self.after(1000,self._writer_status)

    def _college_changed(self,value):
        courses=courses_for(value) if value in COLLEGES else (NEEDS_REVIEW,) if value==NEEDS_REVIEW else tuple(p[1] for p in COURSES.values())+(NEEDS_REVIEW,)
        self.menus['course'].configure(values=['All',*courses])
        if self.filters['course'].get() not in courses:
            self.filters['course'].set('All')

    def _filter_values(self):
        relevant = {'start','end','source_id','search'} if self.view.get() == 'Unknown / Unrecognized' else set(self.filters)-{'search'}
        values={key:var.get().strip() for key,var in self.filters.items() if key in relevant if var.get().strip() not in ('','All')}
        if 'semester_id' in values:
            values['semester_id']=self.semesters.get(values['semester_id'],'')
        if 'source_id' in values:
            values['source_id']=self.sources.get(values['source_id'],'')
        return validate_filters(values)

    def clear_filters(self):
        self.period.set('All time')
        for key,var in self.filters.items():
            var.set('All' if key in self.menus else '')
        self._college_changed('All')
        self.refresh()

    def change_page(self,delta):
        self.page=max(0,self.page+delta)
        self.refresh(reset_page=False)

    def refresh(self,reset_page=True, preserve=False):
        previous_applied=self.applied
        selected = self.tree.selection() if preserve else ()
        if not preserve:
            self._clear_preview()
        if reset_page:
            self.page=0
        self.generation+=1
        self.applied = None
        if not preserve:
            self.rows = []
            self.total = 0
        self.summary.configure(text='Loading…')
        self.previous.configure(state='disabled');self.next.configure(state='disabled')
        if not preserve:
            self.tree.delete(*self.tree.get_children())
        if self.snapshot_window is not None:
            self.snapshot_window.destroy()
            self.snapshot_window = None
        unknown = self.view.get() == 'Unknown / Unrecognized'
        self.snapshot_button.configure(state='normal' if unknown else 'disabled')
        self.heading.configure(text='Unknown Sightings' if unknown else TITLE)
        if unknown:
            today=datetime.now(MANILA).date()
            start,end=self.filters['start'].get(),self.filters['end'].get()
            self.period.set('All time' if not start and not end else 'Today' if start==end==today.isoformat()
                else 'Last 7 days' if start==(today-timedelta(days=6)).isoformat() and end==today.isoformat() else 'Custom dates')
            self.form.grid_remove();self.actions.grid_remove();self.settings.grid_remove()
            self.compact.grid(row=2,column=0,sticky='ew',pady=4)
            if str(self.detail) not in self.split.panes(): self.split.add(self.detail,minsize=300,stretch='always')
        else:
            self.form.grid();self.actions.grid();self.settings.grid();self.compact.grid_remove()
            if str(self.detail) in self.split.panes(): self.split.forget(self.detail)
        self.event_generate('<<SightingsModeChanged>>',when='tail')
        self.description.configure(text=(
            'Unknown / Unrecognized means the system could not match the person to a registered identity. '
            'This is not a confirmed intruder. Counts describe encounters, not unique people.' if unknown else self.known_description))
        for key,widgets in self.filter_widgets.items():
            visible = key in {'start','end','source_id','search','semester_id'} if unknown else key != 'search'
            for widget in widgets:
                widget.grid() if visible else widget.grid_remove()
        if self.task.busy:
            return  # Completion notices the generation change and schedules the latest request.
        try:
            filters = ({} if self._pending_exact_event_id is not None or self._pending_exact_unknown_id is not None
                       else dict(previous_applied[1]) if preserve and previous_applied else self._filter_values())
            view='unknown' if unknown else 'summary' if self.view.get()=='Daily Summary' else 'events'
            size=int(self.page_size.get())
        except ValueError as exc:
            self.message.configure(text=str(exc))
            return
        generation=self.generation
        page=self.page
        exact_event_id = self._pending_exact_event_id
        exact_unknown_id = self._pending_exact_unknown_id
        self.message.configure(text='Loading campus sightings…')
        self.previous.configure(state='disabled');self.next.configure(state='disabled')
        def read():
            if view == 'unknown':
                if exact_unknown_id is not None:
                    exact = self.database.query_unknown_sighting_by_id(
                        username=self.username, event_id=exact_unknown_id)
                    rows = [exact] if exact else []
                    return ({'rows':rows,'count':len(rows),'page':0,'sources':[]},
                            {'sources':[],'semesters':[]},None)
                data = self.database.query_unknown_sightings(username=self.username,filters=filters,page=page,page_size=size)
                return data, {'sources':data['sources'], 'semesters':[dict(t,semester_id=t['id']) for t in self.database.get_academic_terms()]}, None
            if exact_event_id is not None:
                exact = self.database.query_attendance_event_by_id(
                    username=self.username, event_id=exact_event_id)
                return ({'rows':[exact] if exact else [],'count':1 if exact else 0,'page':0,'page_size':size},
                        self.database.attendance_filter_options(),self.database.get_attendance_cooldown())
            return (self.database.query_attendance(view=view,filters=filters,page=page,page_size=size),
                    self.database.attendance_filter_options(),self.database.get_attendance_cooldown())
        def loaded(result):
            if generation!=self.generation:
                self.refresh(reset_page=False)
                return
            current_selection=self.tree.selection() if preserve else selected
            data,options,cooldown=result
            self.rows,self.total,self.page=data['rows'],data['count'],data['page']
            self.applied=(view,filters)
            if cooldown is not None:
                self.cooldown.set(str(cooldown))
            self.semesters={'All':'',**{f"{r['semester_name']} · {r['school_year']} (#{r['semester_id']})":str(r['semester_id']) for r in options['semesters']}}
            self.sources={'All':'',**{f"{r['source_label']} [{r['source_id']}]":r['source_id'] for r in options['sources']}}
            for key,values in (('semester_id',self.semesters),('source_id',self.sources)):
                self.menus[key].configure(values=list(values))
            self.camera_menu.configure(values=list(self.sources))
            columns=('observed_at','source_label','snapshot_available') if view=='unknown' else SUMMARY_COLUMNS if view=='summary' else EVENT_COLUMNS
            self.tree.delete(*self.tree.get_children())
            self.tree.configure(columns=columns)
            for key in columns:
                self.tree.heading(key,text='Observed (Manila)' if view=='unknown' and key=='observed_at' else key.replace('_',' ').title())
                self.tree.column(key,width=220 if key in ('first_seen','last_seen','observed_at','college_department','course','provenance') else 140,stretch=False)
            if view=='unknown':
                self.tree.column('observed_at',width=175,stretch=True)
                self.tree.column('source_label',width=150,stretch=True)
                self.tree.column('snapshot_available',width=130,stretch=True)
            for row in self.rows:
                self.tree.insert('', 'end',iid=str(row['id']),values=[(display_time(row,key)[:19] if view=='unknown' else display_time(row,key)) if key in ('first_seen','last_seen','observed_at') else
                    (f"{row[key]} new (legacy total unknown)" if key=='sighting_count' and row.get('legacy_summary') else row[key]) for key in columns])
            retained=[i for i in current_selection if self.tree.exists(i)]
            exact_id = exact_event_id
            if exact_id is not None and self.tree.exists(str(exact_id)):
                self.tree.selection_set(str(exact_id))
                self.tree.see(str(exact_id))
                if self._pending_exact_event_id == exact_id:
                    self._pending_exact_event_id = None
            elif exact_id is not None:
                if self._pending_exact_event_id == exact_id:
                    self._pending_exact_event_id = None
                self.message.configure(text='That attendance event is no longer available.')
            if exact_unknown_id is not None:
                if self.tree.exists(str(exact_unknown_id)):
                    self.tree.selection_set(str(exact_unknown_id))
                    self.tree.see(str(exact_unknown_id))
                    self._selection_changed(force=True)
                else:
                    self.message.configure(text='That Unknown Logs record is no longer available.')
                if self._pending_exact_unknown_id == exact_unknown_id:
                    self._pending_exact_unknown_id = None
            if retained and exact_event_id is None and exact_unknown_id is None:
                self.tree.selection_set(retained)
            elif preserve:
                self._clear_preview()
            pages=max(1,(self.total+size-1)//size)
            self.summary.configure(text=f'{self.total} matching {"encounters" if view=="unknown" else "records"} · Page {self.page+1} of {pages}')
            self.previous.configure(state='normal' if self.page>0 else 'disabled')
            self.next.configure(state='normal' if (self.page+1)*size<self.total else 'disabled')
            self.message.configure(text='No matching sightings.' if not self.rows else 'Times: Asia/Manila (UTC+08:00). Exports use applied filters; all matching includes every page.')
        def failed(error):
            if generation!=self.generation and not self.task.busy:
                self.refresh(reset_page=False)
            else:
                self._clear_preview('Query failed. Refresh to retry.')
                self.tree.delete(*self.tree.get_children())
                self.summary.configure(text='Report unavailable')
                self.message.configure(text=f'Could not load attendance: {error}')
        self.task.run(read,loaded,failed)

    def open_exact_event(self, event_id: int) -> None:
        """Show one event by immutable ID without date, search, or page filters."""
        self.view.set('Sighting Events')
        self._pending_exact_unknown_id = None
        self._pending_exact_event_id = int(event_id)
        self.refresh()

    def open_exact_unknown(self, event_id: int) -> None:
        """Select an authorized unknown event by ID with no stale date filters."""
        self.view.set('Unknown / Unrecognized')
        self._pending_exact_event_id = None
        self.filters['start'].set('')
        self.filters['end'].set('')
        self.filters['source_id'].set('All')
        self.filters['search'].set('')
        self._pending_exact_unknown_id = int(event_id)
        self.refresh()

    def retry_writer(self):
        count=retry_writers(self.database)
        self.message.configure(text=f'Restarted {count} failed writer(s). Database lock retries run automatically; see writer status below.')

    def save_cooldown(self):
        try:
            seconds=int(self.cooldown.get())
        except ValueError:
            self.message.configure(text='Enter cooldown as whole seconds.')
            return
        self.settings_task.run(lambda:self.database.set_attendance_cooldown(seconds,actor=self.username),
            lambda _:self.message.configure(text=f'Cooldown saved: {seconds} seconds per student across cameras. Existing events are unchanged.'),
            lambda error:self.message.configure(text=f'Could not save cooldown: {error}'))

    def export(self,selected=False):
        if self.applied is None or self.task.busy:
            self.message.configure(text='Apply filters and wait for the report before exporting.')
            return
        ids=[int(i) for i in self.tree.selection()] if selected else None
        if selected and not ids:
            self.message.configure(text='Select one or more rows to export.')
            return
        view,filters=self.applied
        path=filedialog.asksaveasfilename(parent=self,defaultextension='.csv',initialfile=f'campus_sightings_{view}.csv',filetypes=[('CSV','*.csv')])
        if not path:
            return
        generation=self.generation
        def message(text):
            if generation == self.generation:
                self.message.configure(text=text)
        self.export_task.run(lambda:export_csv(self.database,path,view=view,filters=dict(filters),selected_ids=ids,username=self.username),
            lambda count:message(f'Exported {count} {"selected" if selected else "matching"} records with filters and timezone metadata.'),
            lambda error:message(f'Export failed: {error}'))

    def _known_view(self):
        self.view.set('Daily Summary');self.refresh()

    def _period_changed(self,value):
        today=datetime.now(MANILA).date()
        self.filters['start'].set('' if value=='All time' else (today-timedelta(days=6 if value=='Last 7 days' else 0)).isoformat())
        self.filters['end'].set('' if value=='All time' else today.isoformat())
        self.refresh()

    def _auto_refresh(self):
        if self.winfo_ismapped() and self.auto.get() and self.view.get()=='Unknown / Unrecognized' and not self.task.busy:
            self.refresh(reset_page=False,preserve=True)
        self._auto_job=self.after(10000,self._auto_refresh)

    def _hidden(self,event):
        if event.widget is self:
            self._clear_preview()

    def _clear_preview(self,state='Select one encounter to view saved evidence.'):
        self.preview_generation+=1
        self.preview_id=None;self.preview_picture=None;self.preview_state=state
        self.evidence_label.configure(text='')
        self.details.configure(text=state)
        self._close_snapshot()
        self._render_preview()

    def _render_preview(self):
        self.preview.delete('all')
        w=max(1,self.preview.winfo_width());h=max(1,self.preview.winfo_height())
        if self.preview_picture is None:
            self.preview.create_text(w/2,h/2,text=self.preview_state,fill='white',width=max(100,w-20))
        else:
            picture=self.preview_picture.copy();picture.thumbnail((w,h))
            self._preview_photo=ImageTk.PhotoImage(picture)
            self.preview.create_image(w/2,h/2,image=self._preview_photo)

    def _selection_changed(self,event=None,force=False):
        selected=self.tree.selection()
        if self.view.get()!='Unknown / Unrecognized' or len(selected)!=1:
            self._clear_preview('Select one encounter for preview; multiple rows can be exported.')
            return
        event_id=int(selected[0])
        if self.preview_id==event_id and not force: return
        self._clear_preview('Loading saved detection evidence…')
        self.preview_id=event_id
        row=next((r for r in self.rows if r['id']==event_id),None)
        if row is None: return
        self.details.configure(text=f"{display_time(row,'observed_at').replace(' PST ', ' Asia/Manila ')}\n{row['source_label']} · {row['source_id']}\nReference: {row['reference_id']}\nPresence: {row['presence_id']}\nUnknown / Unrecognized")
        self._load_preview()

    def _load_preview(self):
        if self.snapshot_task.busy: return
        generation=self.preview_generation;event_id=self.preview_id
        if event_id is None: return
        context=self.evidence_kind.get()=='Context'
        def read():
            data=self.database.unknown_snapshot(event_id,username=self.username,kind='context') if context else None
            label='Saved detection context'
            if not data:
                data=self.database.unknown_snapshot(event_id,username=self.username)
                label='Saved detection crop'
            if not data: return None,label
            with Image.open(BytesIO(data)) as original:
                if original.width*original.height > 25_000_000: raise ValueError('Unreadable image: exceeds preview limit')
                original.load();original.thumbnail((1280,960))
                return original.convert('RGB'),label
        def loaded(result):
            if generation!=self.preview_generation:
                self._load_preview();return
            self.preview_picture,label=result
            self.preview_state='No snapshot was saved for this encounter.'
            self.evidence_label.configure(text=label if self.preview_picture else '')
            self._render_preview()
        def failed(error):
            if generation!=self.preview_generation:
                if not self.snapshot_task.busy: self._load_preview()
                return
            self.preview_state=f'Unable to read saved evidence: {error}'
            self._render_preview()
        self.snapshot_task.run(read,loaded,failed)

    def open_snapshot(self):
        if self.preview_picture is None: return
        self._close_snapshot()
        window=self.snapshot_window=ctk.CTkToplevel(self)
        window.title(f"Saved detection evidence — event {self.preview_id}")
        window.protocol('WM_DELETE_WINDOW',self._close_snapshot)
        picture=self.preview_picture.copy();picture.thumbnail((1000,750))
        photo=ctk.CTkImage(light_image=picture,dark_image=picture,size=picture.size)
        ctk.CTkLabel(window,text='',image=photo).pack(padx=12,pady=12)

    def _close_snapshot(self):
        if self.snapshot_window is not None:
            self.snapshot_window.destroy()
            self.snapshot_window = None
