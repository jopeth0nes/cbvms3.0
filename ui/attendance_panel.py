"""Shared staff Campus Sightings workspace (sidebar and Records use this view)."""
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog
import customtkinter as ctk

from core.academics import COLLEGES, COURSES, YEAR_LEVELS, NEEDS_REVIEW, courses_for
from core.attendance import TITLE, MANILA, SUMMARY_COLUMNS, EVENT_COLUMNS, display_time, validate_filters, export_csv
from core.attendance_writer import writer_status, retry_writers
from ui.background_task import BackgroundTask
from ui.components import COLOR_BG, COLOR_TEXT_MUTED, COLOR_ACCENT, body_small_font, heading_font


class AttendancePanel(ctk.CTkFrame):
    def __init__(self, master, *, database, username='admin'):
        super().__init__(master, fg_color='transparent')
        self.database, self.username = database, username
        self.task, self.export_task, self.settings_task = (BackgroundTask(self) for _ in range(3))
        self.page, self.total, self.generation = 0, 0, 0
        self.applied = None
        self.rows = []
        self.columnconfigure(0,weight=1)
        self.rowconfigure(5,weight=1)
        self.bind('<Configure>',self._resize,add='+')
        ctk.CTkLabel(self,text=TITLE,font=heading_font(20),anchor='w').grid(row=0,column=0,sticky='ew')
        ctk.CTkLabel(self,text='Confirmed identities seen by campus cameras. Each accepted event increments the count; repeated sightings '
            'within the configured cooldown are excluded across cameras. No absence, lateness, class attendance, duration or checkout is inferred. '
            'Summary classification describes its earliest accepted event; use Sighting Events for changes within a day. Legacy totals are unknown.',
            wraplength=950,justify='left',anchor='w',text_color=COLOR_TEXT_MUTED,font=body_small_font()).grid(row=1,column=0,sticky='ew',pady=6)
        form=ctk.CTkScrollableFrame(self,height=145,fg_color='transparent')
        form.grid(row=2,column=0,sticky='ew')
        for col in range(3):
            form.columnconfigure(col,weight=1,uniform='attendance_filters')
        today=datetime.now(MANILA).date().isoformat()
        self.filters={k:tk.StringVar(value=today if k in ('start','end') else 'All' if k in
            ('semester_id','college_department','course','report_year_level','source_id') else '')
            for k in ('start','end','semester_id','college_department','course','report_year_level','report_section','student_id','name','source_id')}
        self.menus={}
        self.semesters={'All':''}
        self.sources={'All':''}
        labels=('From date (Manila)','To date (Manila)','Semester','College','Course','Year Level','Section','Student ID (exact)','Name contains','Camera / source')
        values={'semester_id':['All'],'college_department':['All',*COLLEGES,NEEDS_REVIEW],
                'course':['All',*(p[1] for p in COURSES.values()),NEEDS_REVIEW],
                'report_year_level':['All',*YEAR_LEVELS],'source_id':['All']}
        for index,((key,var),label) in enumerate(zip(self.filters.items(),labels)):
            row,col=2*(index//3),index%3
            ctk.CTkLabel(form,text=label,anchor='w',font=body_small_font()).grid(row=row,column=col,sticky='ew',padx=4)
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
        actions=ctk.CTkFrame(self,fg_color='transparent')
        actions.grid(row=3,column=0,sticky='ew',pady=5)
        self.view=tk.StringVar(value='Daily Summary')
        for col in range(3):
            actions.columnconfigure(col,weight=1)
        ctk.CTkOptionMenu(actions,variable=self.view,values=['Daily Summary','Sighting Events'],command=lambda _:self.refresh()).grid(row=0,column=0,sticky='ew',padx=3,pady=3)
        for index,(text,command) in enumerate([('Apply / Retry',self.refresh),('Clear Filters',self.clear_filters),
                ('Export selected',lambda:self.export(True)),('Export all matching',lambda:self.export(False))],start=1):
            ctk.CTkButton(actions,text=text,width=115,command=command).grid(row=index//3,column=index%3,sticky='ew',padx=3,pady=3)
        self.message=ctk.CTkLabel(self,text='Choose filters and Apply. Exports use the applied filters.',anchor='w',wraplength=950)
        self.message.grid(row=4,column=0,sticky='ew')
        table=ctk.CTkFrame(self,fg_color=COLOR_BG)
        table.grid(row=5,column=0,sticky='nsew')
        table.columnconfigure(0,weight=1);table.rowconfigure(0,weight=1)
        self.tree=ttk.Treeview(table,show='headings',selectmode='extended')
        self.tree.grid(row=0,column=0,sticky='nsew')
        for orientation in ('vertical','horizontal'):
            vertical=orientation=='vertical'
            bar=ttk.Scrollbar(table,orient=orientation,command=self.tree.yview if vertical else self.tree.xview)
            bar.grid(row=0 if vertical else 1,column=1 if vertical else 0,sticky='ns' if vertical else 'ew')
            self.tree.configure(**{'yscrollcommand' if vertical else 'xscrollcommand':bar.set})
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
        settings=ctk.CTkFrame(self,fg_color='transparent')
        settings.grid(row=7,column=0,sticky='ew',pady=3)
        ctk.CTkButton(settings,text='Retry sighting writer',width=150,command=self.retry_writer).pack(side='left')
        self.cooldown=tk.StringVar(value='300')
        ctk.CTkEntry(settings,textvariable=self.cooldown,width=65).pack(side='right')
        ctk.CTkButton(settings,text='Set cooldown (seconds)',width=155,command=self.save_cooldown).pack(side='right',padx=4)
        self.writer_label=ctk.CTkLabel(self,text='',anchor='w',text_color=COLOR_TEXT_MUTED,wraplength=950)
        self.writer_label.grid(row=8,column=0,sticky='ew')
        self._status_job=self.after(500,self._writer_status)
        self.bind('<Destroy>',self._destroy,add='+')

    def _resize(self,event):
        if event.widget is self:
            for widget in self.winfo_children():
                if isinstance(widget,ctk.CTkLabel):
                    widget.configure(wraplength=max(250,event.width-20))

    def _destroy(self,event):
        if event.widget is self:
            self.generation+=1
            if self._status_job:
                self.after_cancel(self._status_job)
                self._status_job=None

    def _writer_status(self):
        self.writer_label.configure(text=writer_status(self.database))
        self._status_job=self.after(1000,self._writer_status)

    def _college_changed(self,value):
        courses=courses_for(value) if value in COLLEGES else (NEEDS_REVIEW,) if value==NEEDS_REVIEW else tuple(p[1] for p in COURSES.values())+(NEEDS_REVIEW,)
        self.menus['course'].configure(values=['All',*courses])
        if self.filters['course'].get() not in courses:
            self.filters['course'].set('All')

    def _filter_values(self):
        values={key:var.get().strip() for key,var in self.filters.items() if var.get().strip() not in ('','All')}
        if 'semester_id' in values:
            values['semester_id']=self.semesters.get(values['semester_id'],'')
        if 'source_id' in values:
            values['source_id']=self.sources.get(values['source_id'],'')
        return validate_filters(values)

    def clear_filters(self):
        for key,var in self.filters.items():
            var.set('All' if key in self.menus else '')
        self._college_changed('All')
        self.refresh()

    def change_page(self,delta):
        self.page=max(0,self.page+delta)
        self.refresh(reset_page=False)

    def refresh(self,reset_page=True):
        if reset_page:
            self.page=0
        self.generation+=1
        if self.task.busy:
            return  # Completion notices the generation change and schedules the latest request.
        try:
            filters=self._filter_values()
            view='summary' if self.view.get()=='Daily Summary' else 'events'
            size=int(self.page_size.get())
        except ValueError as exc:
            self.message.configure(text=str(exc))
            return
        generation=self.generation
        page=self.page
        self.message.configure(text='Loading campus sightings…')
        self.previous.configure(state='disabled');self.next.configure(state='disabled')
        def read():
            return (self.database.query_attendance(view=view,filters=filters,page=page,page_size=size),
                    self.database.attendance_filter_options(),self.database.get_attendance_cooldown())
        def loaded(result):
            if generation!=self.generation:
                self.refresh(reset_page=False)
                return
            data,options,cooldown=result
            self.rows,self.total,self.page=data['rows'],data['count'],data['page']
            self.applied=(view,filters)
            self.cooldown.set(str(cooldown))
            self.semesters={'All':'',**{f"{r['semester_name']} · {r['school_year']} (#{r['semester_id']})":str(r['semester_id']) for r in options['semesters']}}
            self.sources={'All':'',**{f"{r['source_label']} [{r['source_id']}]":r['source_id'] for r in options['sources']}}
            for key,values in (('semester_id',self.semesters),('source_id',self.sources)):
                self.menus[key].configure(values=list(values))
            columns=SUMMARY_COLUMNS if view=='summary' else EVENT_COLUMNS
            self.tree.delete(*self.tree.get_children())
            self.tree.configure(columns=columns)
            for key in columns:
                self.tree.heading(key,text=key.replace('_',' ').title())
                self.tree.column(key,width=220 if key in ('first_seen','last_seen','observed_at','college_department','course','provenance') else 140,stretch=False)
            for row in self.rows:
                self.tree.insert('', 'end',iid=str(row['id']),values=[display_time(row,key) if key in ('first_seen','last_seen','observed_at') else
                    (f"{row[key]} new (legacy total unknown)" if key=='sighting_count' and row.get('legacy_summary') else row[key]) for key in columns])
            pages=max(1,(self.total+size-1)//size)
            self.summary.configure(text=f'{self.total} matching records · Page {self.page+1} of {pages}')
            self.previous.configure(state='normal' if self.page>0 else 'disabled')
            self.next.configure(state='normal' if (self.page+1)*size<self.total else 'disabled')
            self.message.configure(text='No matching sightings.' if not self.rows else 'Times: Asia/Manila (UTC+08:00). Exports use applied filters; all matching includes every page.')
        def failed(error):
            if generation!=self.generation and not self.task.busy:
                self.refresh(reset_page=False)
            else:
                self.message.configure(text=f'Could not load attendance: {error}')
        self.task.run(read,loaded,failed)

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
        self.export_task.run(lambda:export_csv(self.database,path,view=view,filters=dict(filters),selected_ids=ids),
            lambda count:self.message.configure(text=f'Exported {count} {"selected" if selected else "matching"} records with filters and timezone metadata.'),
            lambda error:self.message.configure(text=f'Export failed: {error}'))
