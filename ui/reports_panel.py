"""Read-only, term-aware reports workspace for administrative users."""
import csv
import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import customtkinter as ctk

from core.report_csv import ROSTER, read_csv, write_csv, import_roster
from core.report_snapshot import SECTIONS, create_snapshot, export_snapshot_csv, dependent_options, is_current_generation, sort_snapshot
from core.reports import open_snapshot_print_preview
from core.report_worker import ReportWorker
from ui.background_task import BackgroundTask
from ui.multi_select_checklist import MultiSelectChecklist
from ui.components import COLOR_BG, COLOR_SURFACE, COLOR_ACCENT, COLOR_TEXT, COLOR_TEXT_MUTED, body_small_font


def action_grid(index):
    return index // 4, index % 4


def checklist_grid(index):
    label_row=2+(index//4)*2
    return (label_row,index%4),(label_row+1,index%4)


class ReportsPanel(ctk.CTkFrame):
    PAGE_SIZE = 200
    def __init__(self, master, *, database, **kwargs):
        super().__init__(master, fg_color=COLOR_BG, **kwargs)
        self.database=database; self.worker=ReportWorker(); self.export_worker=ReportWorker(); self.export_generation=0; self.output_busy=False; self.import_task=BackgroundTask(self); self.evidence_task=BackgroundTask(self); self.generation=0
        self.snapshot=None; self.applied={}; self.pending=None; self.page=0; self.sort_key='student_id'; self.sort_reverse=False
        self._all_options={}
        self.grid_columnconfigure(0,weight=1); self.grid_rowconfigure(3,weight=1)
        actions=ctk.CTkFrame(self,fg_color='transparent');actions.grid(row=0,column=0,sticky='ew',pady=(0,6))
        for i,(label,fn) in enumerate([('Apply filters',self.apply_filters),('Reset',self.reset_filters),('Export CSV',self.export_csv),('Print / Save as PDF',self.print_report),('Import roster CSV',self.import_csv),('Import discipline snapshot',self.import_discipline_snapshot),('Roster template',self.export_template)]):
            row,col=action_grid(i);button=ctk.CTkButton(actions,text=label,command=fn);button.grid(row=row,column=col,sticky='ew',padx=3,pady=2)
            if label=='Export CSV':self.export_button=button
            if label=='Print / Save as PDF':self.print_button=button
        for col in range(4):actions.grid_columnconfigure(col,weight=1,uniform='report_action')
        self.filter_toggle=ctk.CTkButton(actions,text='Hide filters',command=self.toggle_filters)
        self.filter_toggle.grid(row=1,column=3,sticky='ew',padx=3,pady=2)
        self.applied_summary=ctk.CTkLabel(self,text='Applied filters: All',anchor='w',justify='left',width=1,wraplength=900,text_color=COLOR_TEXT_MUTED)
        self.applied_summary.grid(row=1,column=0,sticky='ew',padx=8,pady=(0,5))
        self.bind('<Configure>', self._resize_summaries, add='+')
        self.filter_box=ctk.CTkScrollableFrame(self,fg_color=COLOR_SURFACE,corner_radius=10,height=255);self.filter_box.grid(row=2,column=0,sticky='ew',pady=(0,8))
        for col in range(4):self.filter_box.grid_columnconfigure(col,weight=1)
        self.sections={}
        for i,name in enumerate(SECTIONS):
            var=tk.BooleanVar(value=name=='Student Directory')
            ctk.CTkCheckBox(self.filter_box,text=name,variable=var,command=self._update_filter_availability).grid(row=i//3,column=i%3,padx=8,pady=5,sticky='w');self.sections[name]=var
        self.checklists={}
        for i,(key,title) in enumerate((('academic_years','Academic year'),('term_ids','Semester / Term'),('departments','College / Department'),('courses','Course'),('year_levels','Year level'),('sections','Section'))):
            (label_row,col),(control_row,_)=checklist_grid(i)
            ctk.CTkLabel(self.filter_box,text=title,anchor='w',wraplength=205,text_color=COLOR_TEXT_MUTED,font=body_small_font()).grid(row=label_row,column=col,sticky='ew',padx=8)
            callback=self._update_dependent_options if key in ('academic_years','departments') else None
            control=MultiSelectChecklist(self.filter_box,title,[],on_change=callback,on_removed=self._removed_options);control.grid(row=control_row,column=col,sticky='ew',padx=8,pady=(2,5));self.checklists[key]=control
        for i,(key,title) in enumerate((('statuses','Enrollment / violation / suspension status'),('appeal_statuses','Appeal status'),('categories','Violation category'),('enrollment_statuses','Enrollment at detection'))):
            ctk.CTkLabel(self.filter_box,text=title,anchor='w',wraplength=205,text_color=COLOR_TEXT_MUTED,font=body_small_font()).grid(row=6,column=i,sticky='ew',padx=8)
            control=MultiSelectChecklist(self.filter_box,title,[],on_removed=self._removed_options);control.grid(row=7,column=i,sticky='ew',padx=8,pady=(2,5));self.checklists[key]=control
        self.date_from=tk.StringVar();self.date_to=tk.StringVar();self.search=tk.StringVar()
        self.date_entries=[]
        for i,(title,var,placeholder) in enumerate((('From date · YYYY-MM-DD',self.date_from,'YYYY-MM-DD'),('To date · inclusive Manila date',self.date_to,'YYYY-MM-DD'),('Student ID / name search',self.search,'Search'))):
            col=i;ctk.CTkLabel(self.filter_box,text=title,anchor='w',wraplength=205,text_color=COLOR_TEXT_MUTED,font=body_small_font()).grid(row=8,column=col,sticky='ew',padx=8)
            entry=ctk.CTkEntry(self.filter_box,textvariable=var,placeholder_text=placeholder);entry.grid(row=9,column=col,sticky='ew',padx=8,pady=(2,5))
            if i<2:self.date_entries.append(entry)
        self.status=ctk.CTkLabel(self.filter_box,text='OR within each filter · AND between filters · All is unrestricted; Clear means no matches.',anchor='w',wraplength=1050,text_color=COLOR_TEXT_MUTED)
        self.status.grid(row=10,column=0,columnspan=4,sticky='ew',padx=8,pady=4)
        self.tabs=ttk.Notebook(self);self.tabs.grid(row=3,column=0,sticky='nsew')
        self.tabs.bind('<<NotebookTabChanged>>',self._tab_changed)
        self.tables={};self.table_rows={};self.pages={}
        self.pager=ctk.CTkFrame(self,fg_color='transparent');self.pager.grid(row=4,column=0,sticky='ew')
        self.page_label=ctk.CTkLabel(self.pager,text='No report loaded');self.page_label.pack(side='left',padx=6)
        ctk.CTkButton(self.pager,text='Previous',width=80,command=lambda:self.show_page(-1)).pack(side='right',padx=3)
        ctk.CTkButton(self.pager,text='Next',width=80,command=lambda:self.show_page(1)).pack(side='right',padx=3)
        self.after(50,self._poll)
        self._update_filter_availability()
        self._apply_after_catalog=True;self.refresh_options()

    def refresh(self):
        """Reload catalogs and reapply the existing applied criteria."""
        self._refresh_filters=dict(self.applied) if self.snapshot else None
        self._apply_after_catalog=True
        self.refresh_options()

    def _resize_summaries(self, event):
        # Tk events use physical pixels; CTk dimensions use unscaled units.
        # Listening to the parent avoids a label-size feedback loop at 125%.
        width=max(240, int(event.width / self._get_widget_scaling())-32)
        for label in (self.applied_summary, getattr(self, 'status', None)):
            if label is not None and label.cget('wraplength') != width:
                label.configure(wraplength=width)

    def toggle_filters(self):
        if self.filter_box.winfo_ismapped():
            self.filter_box.grid_remove()
            self.filter_toggle.configure(text='Show filters')
        else:
            self.filter_box.grid()
            self.filter_toggle.configure(text='Hide filters')

    def _poll(self):
        try: generation,value,error=self.worker.results.get_nowait()
        except Exception: generation=None
        if generation is not None and is_current_generation(self.generation,generation):
            if error:self.status.configure(text=f'Report failed: {error}')
            elif value[0]=='catalog':
                self._set_options_from_snapshot(value[1])
                if self._apply_after_catalog:
                    self._apply_after_catalog=False
                    saved=getattr(self,'_refresh_filters',None)
                    sections=self.snapshot.selected_sections if self.snapshot and saved is not None else None
                    self.apply_filters(filters=saved,sections=sections)
                    self._refresh_filters=None
            elif value[0]=='report':
                self.pending=None
                self.snapshot=value[1];self.applied=dict(value[1].filters)
                self._update_filter_availability()
                self._show(value[1])
        try: export_generation,export_value,export_error=self.export_worker.results.get_nowait()
        except Exception: export_generation=None
        if export_generation is not None and is_current_generation(self.export_generation,export_generation):
            self.output_busy=False
            self.export_button.configure(state='normal');self.print_button.configure(state='normal')
            if export_error:self.status.configure(text=f'Export / print failed: {export_error}')
            else:self.status.configure(text=export_value)
        try:
            if self.winfo_exists():self.after(50,self._poll)
        except tk.TclError:pass

    def refresh_options(self):
        self.generation+=1;gen=self.generation
        self.worker.offer(gen,lambda:('catalog',create_snapshot(self.database,{},SECTIONS)))
        # options refresh returns through the normal snapshot path
        self._options_generation=gen

    def _set_options_from_snapshot(self,snapshot):
        terms={}
        for term in snapshot.terms:
            label=f"{term.get('school_year') or 'Unverified year'} · {term.get('semester_name') or 'Unverified term'} [#{term['id']}]"
            terms[label]=term['id']
        for section in snapshot.sections:
            for row in section.rows:
                term_obj=next((t for t in snapshot.terms if t['id']==row.get('semester_id')),None)
                if term_obj is not None:
                    label=f"{row.get('school_year') or 'Unverified year'} · {row.get('semester_name') or 'Unverified term'} [#{row['semester_id']}]"
                    terms[label]=row['semester_id']
        self._course_pairs={(str(r.get('college_department')),str(r.get('course'))) for s in snapshot.sections for r in s.rows if r.get('college_department') and r.get('course')}
        self._all_options['term_labels']=terms
        def opts(field):return sorted({str(r.get(field)) for s in snapshot.sections for r in s.rows if r.get(field) not in (None,'')},key=str.casefold)
        imported_section=next((s for s in snapshot.sections if s.name=='Imported Discipline Snapshot'),None)
        imported_years={str(r.get('school_year')) for r in (imported_section.rows if imported_section else ()) if r.get('school_year')}
        years=sorted({str(t.get('school_year')) for t in snapshot.terms if t.get('school_year')} | imported_years,key=str.casefold)
        for k,v in (('academic_years',years),('term_ids',terms.keys()),('departments',opts('college_department')),('courses',opts('course')),('year_levels',opts('year_level')),('sections',opts('section'))):self.checklists[k].set_options(v)
        for k,field in (('statuses','status'),('appeal_statuses','appeal_status'),('categories','category'),('enrollment_statuses','enrollment_status')):self.checklists[k].set_options(opts(field))
        self._update_dependent_options()

    def _removed_options(self, removed):
        self.status.configure(text='Some checklist options no longer match the selected year/department and were removed: '+', '.join(map(str,removed)))

    def _update_dependent_options(self):
        if not self.checklists:return
        years=self.checklists['academic_years'].get()
        mapping=self._all_options.get('term_labels',{})
        terms=[label for label,_ in dependent_options([(label,label.split(' · ',1)[0]) for label in mapping],years,key_index=1)]
        self.checklists['term_ids'].set_options(terms)
        depts=self.checklists['departments'].get()
        pairs=getattr(self,'_course_pairs',set())
        courses=sorted({course for _,course in dependent_options(pairs,depts)},key=str.casefold)
        self.checklists['courses'].set_options(courses)

    def _draft_filters(self):
        years=self.checklists['academic_years'].get(); terms=self.checklists['term_ids'].get()
        mapping=self._all_options.get('term_labels',{})
        term_ids=None if terms is None else [mapping[t] for t in terms if t in mapping]
        if years is not None and term_ids is not None:
            term_ids=[tid for label,tid in mapping.items() if label in terms and label.split(' · ',1)[0] in years]
        return {'academic_years':years,'term_ids':term_ids,'departments':self.checklists['departments'].get(),
                'courses':self.checklists['courses'].get(),'year_levels':self.checklists['year_levels'].get(),
                'sections':self.checklists['sections'].get(),'statuses':self.checklists['statuses'].get(),
                'appeal_statuses':self.checklists['appeal_statuses'].get(),'date_from':self.date_from.get().strip() or None,
                'categories':self.checklists['categories'].get(),'enrollment_statuses':self.checklists['enrollment_statuses'].get(),
                'date_to':self.date_to.get().strip() or None,'search':self.search.get().strip(),
                'term_labels':None if terms is None else list(terms)}

    def apply_filters(self,filters=None,sections=None):
        sections=tuple(sections if sections is not None else (name for name,var in self.sections.items() if var.get()))
        if not sections:messagebox.showinfo('Reports','Select at least one report section.',parent=self);return
        self._apply_after_catalog=False
        filters=dict(self._draft_filters() if filters is None else filters);self.pending=filters;self.generation+=1;gen=self.generation
        self.status.configure(text='Reading one consistent database snapshot…')
        sort_order=(self.sort_key,self.sort_reverse)
        self.worker.offer(gen,lambda:('report',create_snapshot(self.database,filters,sections,sort_order=sort_order)))

    def _show(self,snapshot):
        if self.snapshot is None:self._set_options_from_snapshot(snapshot)
        self.snapshot=snapshot
        style=ttk.Style(self)
        style.theme_use('clam')
        style.configure('Reports.Treeview',background=COLOR_BG,foreground=COLOR_TEXT,fieldbackground=COLOR_BG,
                        rowheight=25,borderwidth=0,font=('TkDefaultFont',10))
        style.configure('Reports.Treeview.Heading',background=COLOR_SURFACE,foreground=COLOR_TEXT,
                        relief='flat',font=('TkDefaultFont',10,'bold'))
        style.map('Reports.Treeview',background=[('selected',COLOR_ACCENT)],foreground=[('selected',COLOR_TEXT)])
        style.map('Reports.Treeview.Heading',background=[('active',COLOR_ACCENT)])
        for tab in self.tabs.tabs():
            child=self.tabs.nametowidget(tab);self.tabs.forget(tab);child.destroy()
        self.tables.clear();self.table_rows.clear();self.pages.clear()
        for section in snapshot.sections:
            frame=ctk.CTkFrame(self.tabs,fg_color=COLOR_SURFACE);self.tabs.add(frame,text=section.name)
            total=len(section.rows);self.table_rows[section.name]=section.rows;self.pages[section.name]=0
            columns=section.columns
            holder=ttk.Frame(frame);holder.pack(fill='both',expand=True)
            tree=ttk.Treeview(holder,columns=columns,show='headings',height=10,style='Reports.Treeview')
            vertical=ttk.Scrollbar(holder,orient='vertical',command=tree.yview);horizontal=ttk.Scrollbar(holder,orient='horizontal',command=tree.xview)
            tree.configure(yscrollcommand=vertical.set,xscrollcommand=horizontal.set)
            tree.grid(row=0,column=0,sticky='nsew');vertical.grid(row=0,column=1,sticky='ns');horizontal.grid(row=1,column=0,sticky='ew')
            holder.grid_rowconfigure(0,weight=1);holder.grid_columnconfigure(0,weight=1)
            tree.bind('<Double-1>',lambda event,n=section.name:self.open_evidence(event,n))
            for key in columns:
                tree.heading(key,text=key.replace('_',' ').title(),command=lambda k=key:self.sort_by(k))
                tree.column(key,width=150,minwidth=90,stretch=True)
            self.tables[section.name]=tree
            self._render_page(section.name)
        self.status.configure(text='Applied: OR within each filter, AND between filters. Status applies to enrollment/discipline sections; appeal status applies to Violations and imported snapshots. Dates use Asia/Manila: violation occurrence, enrollment confirmation, or suspension start date. Directory rows have no term ownership and are excluded by term/year filters.')
        details=' · '.join(f'{s.name}: {s.record_count} records / {s.distinct_students} students' for s in snapshot.sections)
        source='Live database'+(' plus imported snapshot' if 'Imported Discipline Snapshot' in snapshot.selected_sections else '')
        self.applied_summary.configure(text=f"Applied: {self._filter_description(snapshot.filters)} · Source: {source} · Generated {snapshot.generated_at.astimezone(__import__('zoneinfo').ZoneInfo('Asia/Manila')).strftime('%Y-%m-%d %H:%M:%S %z')} Asia/Manila · {details}")
        self._tab_changed()

    def _render_page(self,name):
        tree=self.tables[name];tree.delete(*tree.get_children());rows=self.table_rows[name];page=self.pages[name]
        for row in rows[page*self.PAGE_SIZE:(page+1)*self.PAGE_SIZE]:
            key=str(row.get('record_id') or row.get('enrollment_id') or row.get('student_id'))
            tree.insert('','end',iid=key,values=[row.get(c,'') for c in tree['columns']])
        self.page_label.configure(text=f'{name}: page {page+1} · {len(rows)} records')

    @staticmethod
    def _filter_description(filters):
        labels={'academic_years':'Years','term_ids':'Term IDs','term_labels':'Terms','departments':'Departments','courses':'Courses','year_levels':'Year levels','sections':'Sections','statuses':'Statuses','appeal_statuses':'Appeals','date_from':'From','date_to':'To','search':'Search'}
        labels.update(categories='Violation categories', enrollment_statuses='Enrollment at detection')
        def value(v):
            if v is None:return 'All'
            if isinstance(v,(list,tuple)):return ', '.join(map(str,v)) if v else 'None (no matches)'
            return str(v) if v else 'None'
        return ' · '.join(f"{labels.get(k,k)}: {value(v)}" for k,v in filters.items())

    def show_page(self,delta):
        name=self.tabs.tab(self.tabs.select(),'text') if self.tabs.tabs() else None
        if name not in self.tables:return
        max_page=max(0,(len(self.table_rows[name])-1)//self.PAGE_SIZE);self.pages[name]=min(max(0,self.pages[name]+delta),max_page);self._render_page(name)

    def _tab_changed(self,_event=None):
        if not self.tabs.tabs():return
        name=self.tabs.tab(self.tabs.select(),'text')
        if name in self.pages:self.page_label.configure(text=f'{name}: page {self.pages[name]+1} · {len(self.table_rows[name])} records')

    def _update_filter_availability(self):
        if not hasattr(self,'checklists'):return
        selected={name for name,var in self.sections.items() if var.get()}
        has_term=bool(selected & {'Semester Enrollment Roster','Violations','Suspensions','Imported Discipline Snapshot'})
        self.checklists['academic_years'].set_enabled(has_term);self.checklists['term_ids'].set_enabled(has_term)
        self.checklists['statuses'].set_enabled(has_term)
        self.checklists['appeal_statuses'].set_enabled(bool(selected & {'Violations','Imported Discipline Snapshot'}))
        self.checklists['categories'].set_enabled(bool(selected & {'Violations','Imported Discipline Snapshot'}))
        self.checklists['enrollment_statuses'].set_enabled('Violations' in selected)
        for entry in getattr(self,'date_entries',[]):entry.configure(state='normal' if has_term else 'disabled')

    def sort_by(self,key):
        self.sort_reverse=not self.sort_reverse if key==self.sort_key else False;self.sort_key=key
        if self.snapshot:
            self.snapshot=sort_snapshot(self.snapshot,key,self.sort_reverse)
            for section in self.snapshot.sections:
                self.table_rows[section.name]=section.rows;self.pages[section.name]=0;self._render_page(section.name)
            self._tab_changed()

    def reset_filters(self):
        for control in self.checklists.values():control.set(None)
        self.date_from.set('');self.date_to.set('');self.search.set('')
        for name,var in self.sections.items():var.set(name=='Student Directory')
        self._update_dependent_options()
        self._update_filter_availability()
        self.apply_filters()

    def export_csv(self):
        if not self.snapshot:return
        if self.output_busy:self.status.configure(text='An export or print is already running.');return
        snapshot=self.snapshot;multiple=len(snapshot.sections)>1
        path=filedialog.asksaveasfilename(parent=self,defaultextension='.zip' if multiple else '.csv',
            initialfile='cbvms_reports.zip' if multiple else 'cbvms_report.csv',filetypes=[('ZIP' if multiple else 'CSV', '*.'+('zip' if multiple else 'csv'))])
        if path:
            self.output_busy=True;self.export_button.configure(state='disabled');self.print_button.configure(state='disabled')
            self.export_generation+=1;gen=self.export_generation
            self.export_worker.offer(gen,lambda:(export_snapshot_csv(snapshot,path),f'Exported applied snapshot to {path}')[1])
            self.status.configure(text='Exporting applied snapshot…')

    def print_report(self):
        if not self.snapshot:return
        if self.output_busy:self.status.configure(text='An export or print is already running.');return
        snapshot=self.snapshot;self.export_generation+=1;gen=self.export_generation
        self.output_busy=True;self.export_button.configure(state='disabled');self.print_button.configure(state='disabled')
        self.export_worker.offer(gen,lambda:(open_snapshot_print_preview(snapshot),'Opened print preview for applied snapshot')[1])
        self.status.configure(text='Preparing print preview…')

    def open_evidence(self,event,section_name):
        if section_name!='Violations':return
        tree=self.tables.get(section_name)
        if not tree:return
        item=tree.identify_row(event.y)
        if not item:return
        row=next((r for r in self.table_rows.get(section_name,()) if str(r.get('record_id'))==item),None)
        if not row or not row.get('student_id'):return
        def display(record):
            blob=record.get('snapshot') if record else None
            if not blob:messagebox.showinfo('Evidence','No original incident image available.',parent=self);return
            try:
                from PIL import Image,ImageTk
                import io
                window=ctk.CTkToplevel(self);window.title(f"Original incident #{row['record_id']}")
                image=Image.open(io.BytesIO(blob));image.thumbnail((800,600))
                label=tk.Label(window);label.image=ImageTk.PhotoImage(image);label.configure(image=label.image);label.pack()
            except Exception as exc:messagebox.showerror('Evidence',str(exc),parent=self)
        self.evidence_task.run(lambda:self.database.get_student_original_evidence(int(row['record_id']),row['student_id']),
            display,lambda error:messagebox.showerror('Evidence',error,parent=self))

    def export_template(self):
        path=filedialog.asksaveasfilename(parent=self,defaultextension='.csv',initialfile='roster_template.csv',filetypes=[('CSV files','*.csv')])
        if path:write_csv(path,'Roster',[])

    def import_csv(self):
        path=filedialog.askopenfilename(parent=self,filetypes=[('CSV files','*.csv')])
        if not path:return
        def preview(rows):
            detail=f'{len(rows)} permanent-student demographic rows. This preserves existing photos, face data, accounts and standing. It does not confirm term enrollment.'
            if not messagebox.askyesno('Import preview',detail+'\n\nImport this roster?',parent=self):return
            self.import_task.run(lambda:import_roster(self.database,rows),
            lambda _:(setattr(self,'_apply_after_catalog',True),self.refresh_options(),self.status.configure(text=f'Imported {len(rows)} demographic rows.')),
                lambda error:messagebox.showerror('Import failed',error,parent=self))
        self.import_task.run(lambda:read_csv(path,'Roster'),preview,
            lambda error:messagebox.showerror('Import failed',error,parent=self))

    def import_discipline_snapshot(self):
        path=filedialog.askopenfilename(parent=self,filetypes=[('CSV files','*.csv')])
        if not path:return
        def preview(rows):
            detail=(f'{len(rows)} imported historical discipline rows. This replaces the previously saved imported snapshot only after confirmation. '
                    'It does not confirm enrollment, add strikes, apply suspensions, or replace live authoritative discipline. Foreign term IDs are not trusted.')
            if not messagebox.askyesno('Import discipline snapshot',detail+'\n\nReplace the saved imported snapshot?',parent=self):return
            payload=json.dumps(rows,ensure_ascii=False)
            def commit():
                with self.database.connect() as conn:
                    conn.execute('INSERT INTO report_csv_snapshot(id,payload) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',(payload,))
            self.import_task.run(commit,lambda _:(self.sections['Imported Discipline Snapshot'].set(True),setattr(self,'_apply_after_catalog',True),self.refresh_options(),self.status.configure(text=f'Imported {len(rows)} rows as a separate read-only snapshot.')),
                lambda error:messagebox.showerror('Import failed',error,parent=self))
        self.import_task.run(lambda:read_csv(path,'Discipline'),preview,
            lambda error:messagebox.showerror('Import failed',error,parent=self))

    def destroy(self):
        if hasattr(self,'worker'):self.worker.close()
        if hasattr(self,'export_worker'):self.export_worker.close()
        super().destroy()
