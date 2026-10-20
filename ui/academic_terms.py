"""Superadmin calendar, roster, audit and enrollment-review workspace."""
import json
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog
import customtkinter as ctk
from core.academic_calendar import instant


class AcademicTermsWindow(ctk.CTkToplevel):
    def __init__(self, master, database, actor):
        super().__init__(master)
        self.db,self.actor=database,actor
        self.title('Academic Terms and Enrollment Review')
        self.geometry('1100x760')
        self.transient(master.winfo_toplevel())
        self.terms={}
        self.selected=None
        self.preview=None
        form=ctk.CTkFrame(self);form.pack(fill='x',padx=12,pady=8)
        self.fields={}
        for column,(key,label,default) in enumerate((('semester_code','Term','semester_1'),('school_year','Academic year',''),
                ('start_date','Start YYYY-MM-DD',''),('end_date','Inclusive end YYYY-MM-DD',''))):
            ctk.CTkLabel(form,text=label).grid(row=0,column=column,padx=6)
            var=tk.StringVar(value=default);self.fields[key]=var
            widget=(ctk.CTkOptionMenu(form,variable=var,values=['semester_1','semester_2','summer']) if key=='semester_code'
                    else ctk.CTkEntry(form,textvariable=var))
            widget.grid(row=1,column=column,padx=6,pady=4)
        bar=ctk.CTkFrame(self);bar.pack(fill='x',padx=12)
        for text,command in [('New',self.new),('Save draft',lambda:self.save(False)),('Publish dates',lambda:self.save(True)),
                             ('Date impact preview',self.impact),('Refresh',self.refresh),('All Terms',lambda:self.show_all())]:
            ctk.CTkButton(bar,text=text,width=150,command=command).pack(side='left',padx=3,pady=4)
        self.tree=ttk.Treeview(self,columns=('year','term','dates','state','enrolled','reviews','open cases'),show='headings',height=8)
        for col in self.tree['columns']:
            self.tree.heading(col,text=col.title());self.tree.column(col,width=150)
        self.tree.pack(fill='x',padx=12,pady=8);self.tree.bind('<<TreeviewSelect>>',self.select)
        self.tabs=ttk.Notebook(self);self.tabs.pack(fill='both',expand=True,padx=12,pady=8)
        self.output={}
        for name in ('Roster and history','Enrollment reviews','Audit and impact','Calendar exceptions'):
            frame=ttk.Frame(self.tabs);self.tabs.add(frame,text=name)
            if name=='Roster and history':
                self.build_roster(frame)
            else:
                text=tk.Text(frame,wrap='word');text.pack(fill='both',expand=True);self.output[name]=text
        controls=ctk.CTkFrame(self);controls.pack(fill='x',padx=12,pady=6)
        ctk.CTkButton(controls,text='Inspect review evidence',command=self.evidence).pack(side='left',padx=4)
        ctk.CTkButton(controls,text='Resolve review',command=self.resolve).pack(side='left',padx=4)
        ctk.CTkButton(controls,text='Alarm cooldown',command=self.cooldown).pack(side='left',padx=4)
        ctk.CTkButton(controls,text='Resolve calendar exception',command=self.resolve_exception).pack(side='left',padx=4)
        self.refresh()

    def write(self,name,value):
        if name=='Roster and history':
            self.roster_rows=value;self.roster_page=0;self.filter_roster();return
        box=self.output[name];box.delete('1.0','end');box.insert('end',json.dumps(value,indent=2,ensure_ascii=False,default=str))

    def refresh(self):
        try:
            self.db.reconcile_term_boundaries()
            terms=self.db.get_academic_terms();self.terms={str(t['id']):t for t in terms}
            self.tree.delete(*self.tree.get_children())
            with self.db.connect() as conn:
                for t in terms:
                    count=conn.execute("SELECT COUNT(*) FROM student_term_enrollments WHERE semester_id=? AND status='confirmed'",(t['id'],)).fetchone()[0]
                    reviews=conn.execute('SELECT COUNT(*) FROM enrollment_reviews WHERE semester_id=? AND resolved_at IS NULL',(t['id'],)).fetchone()[0]
                    cases=conn.execute("""SELECT COUNT(*) FROM violations v WHERE semester_id=? AND
                        (status IN ('pending_review','unreviewed') OR EXISTS(SELECT 1 FROM appeals a
                        WHERE a.violation_id=v.id AND a.status='pending'))""",(t['id'],)).fetchone()[0]
                    state=t['publication_state']
                    if state=='published':
                        state='current' if t['is_current'] else 'upcoming' if instant(t['starts_at'])>instant() else 'past'
                    self.tree.insert('', 'end',iid=str(t['id']),values=(t['school_year'],t['semester_name'],f"{t['start_date'] or 'Unconfigured'} — {t['end_date'] or ''}",state,count,reviews,cases))
                roster=self.db.query_term_roster(semester_id=self.selected)
                self.write('Roster and history',roster)
                self.write('Enrollment reviews',[dict(r) for r in conn.execute('SELECT id,student_id,semester_id,enrollment_status,observed_at,last_seen,resolved_at,resolution_reason FROM enrollment_reviews ORDER BY id DESC')])
                self.write('Calendar exceptions',[dict(r) for r in conn.execute('SELECT * FROM term_assignment_reviews ORDER BY id DESC')])
                self.write('Audit and impact',[dict(r) for r in conn.execute('SELECT * FROM academic_audit ORDER BY id DESC LIMIT 200')])
        except Exception as exc:
            messagebox.showerror('Academic terms',str(exc),parent=self)

    def select(self,_=None):
        selected=self.tree.selection()
        if not selected:return
        self.selected=int(selected[0]);self.preview=None
        term=self.terms[selected[0]]
        for key,var in self.fields.items():var.set(term.get(key) or '')
        self.write('Roster and history',self.db.query_term_roster(semester_id=self.selected))

    def show_all(self):
        self.selected=None
        self.refresh()

    def new(self):
        self.selected=None;self.preview=None
        for key,var in self.fields.items():var.set('semester_1' if key=='semester_code' else '')

    def impact(self):
        try:
            self.preview=self.db.term_date_impact(self.selected,self.fields['start_date'].get(),self.fields['end_date'].get())
            self.write('Audit and impact',self.preview);self.tabs.select(2)
        except Exception as exc:messagebox.showerror('Impact preview',str(exc),parent=self)

    def save(self,publish):
        try:
            reason=''
            old=self.terms.get(str(self.selected),{})
            if old.get('publication_state')=='published' and any(old.get(k)!=self.fields[k].get() for k in ('start_date','end_date')):
                if not self.preview:
                    self.impact();return
                reason=simpledialog.askstring('Audited date correction','Reason (records retain their original term; review conflicts in preview):',parent=self)
                if not reason:return
            self.db.save_academic_term(actor=self.actor,term_id=self.selected,publish=publish,reason=reason,
                preview_token=(self.preview or {}).get('token'),**{k:v.get() for k,v in self.fields.items()})
            self.preview=None;self.refresh()
        except Exception as exc:messagebox.showerror('Term not saved',str(exc),parent=self)

    def evidence(self):
        review=simpledialog.askinteger('Evidence','Review ID:',parent=self)
        if not review:return
        from PIL import Image,ImageTk
        from io import BytesIO
        with self.db.connect() as conn:
            self.db._term_admin(conn,self.actor)
            rows=conn.execute('SELECT observed_at,snapshot FROM enrollment_review_events WHERE review_id=? ORDER BY observed_at',(review,)).fetchall()
            if not rows:
                rows=conn.execute('SELECT observed_at,snapshot FROM enrollment_reviews WHERE id=?',(review,)).fetchall()
        if not rows:
            messagebox.showinfo('Evidence','No incident image available.',parent=self);return
        window=ctk.CTkToplevel(self);window.title(f'Original enrollment review evidence #{review}')
        choices=[f'{i+1}: {r[0]}' for i,r in enumerate(rows)]
        label=tk.Label(window);label.pack()
        def show(choice):
            row=rows[choices.index(choice)]
            if not row[1]:
                label.configure(image='',text='No incident image available.');return
            try:
                picture=Image.open(BytesIO(row[1]));picture.thumbnail((700,500))
                label.image=ImageTk.PhotoImage(picture);label.configure(image=label.image,text='')
            except (OSError,ValueError):
                label.configure(image='',text='The stored incident image cannot be decoded.')
        ctk.CTkOptionMenu(window,values=choices,command=show).pack()
        show(choices[0])

    def resolve(self):
        review=simpledialog.askinteger('Resolve review','Review ID:',parent=self)
        if not review:return
        reason=simpledialog.askstring('Resolution','Reason (does not confirm enrollment):',parent=self)
        if not reason:return
        try:self.db.resolve_enrollment_review(review,actor=self.actor,reason=reason);self.refresh()
        except Exception as exc:messagebox.showerror('Review',str(exc),parent=self)

    def cooldown(self):
        seconds=simpledialog.askinteger('Enrollment alarm','Cooldown in seconds (1–86400):',parent=self,minvalue=1,maxvalue=86400)
        if seconds:
            try:self.db.set_enrollment_alarm_cooldown(seconds,actor=self.actor)
            except Exception as exc:messagebox.showerror('Alarm',str(exc),parent=self)


    def resolve_exception(self):
        review=simpledialog.askinteger('Calendar exception','Exception ID:',parent=self)
        if not review:return
        reason=simpledialog.askstring('Review decision','Reason for retaining original record ownership:',parent=self)
        if not reason:return
        try:self.db.resolve_term_assignment_review(review,actor=self.actor,reason=reason);self.refresh()
        except Exception as exc:messagebox.showerror('Calendar exception',str(exc),parent=self)

    def build_roster(self,parent):
        self.roster_rows=[];self.roster_page=0;self.roster_vars={};self.roster_menus={}
        filters=ttk.Frame(parent);filters.pack(fill='x')
        keys=('school_year','semester_name','college_department','course','report_year_level','report_section')
        for i,key in enumerate(keys):
            ttk.Label(filters,text=key.replace('_',' ').title()).grid(row=0,column=i,sticky='w')
            var=tk.StringVar(value='All');self.roster_vars[key]=var
            menu=ttk.Combobox(filters,textvariable=var,state='readonly',width=17,values=['All'])
            menu.grid(row=1,column=i,padx=3);menu.bind('<<ComboboxSelected>>',lambda _:self.filter_roster())
            self.roster_menus[key]=menu
        self.roster_search=tk.StringVar()
        ttk.Label(filters,text='Student ID / name').grid(row=2,column=0)
        search=ttk.Entry(filters,textvariable=self.roster_search);search.grid(row=2,column=1,columnspan=2,sticky='ew')
        search.bind('<KeyRelease>',lambda _:self.filter_roster())
        ttk.Button(filters,text='Export filtered roster',command=self.export_roster).grid(row=2,column=3)
        ttk.Button(filters,text='Prepare term draft',command=self.prepare_draft).grid(row=2,column=4)
        ttk.Button(filters,text='Correct enrollment',command=self.correct_selected_enrollment).grid(row=2,column=5)
        columns=('student_id','name','semester_name','course','report_year_level','report_section','status','capture')
        self.roster_tree=ttk.Treeview(parent,columns=columns,show='headings',height=9)
        for key in columns:self.roster_tree.heading(key,text=key.replace('_',' ').title());self.roster_tree.column(key,width=120)
        self.roster_tree.pack(fill='both',expand=True)
        self.roster_tree.bind('<Double-1>',self.roster_history)
        pager=ttk.Frame(parent);pager.pack(fill='x')
        ttk.Button(pager,text='Previous',command=lambda:self.roster_turn(-1)).pack(side='left')
        ttk.Button(pager,text='Next',command=lambda:self.roster_turn(1)).pack(side='left')
        self.roster_count=ttk.Label(pager);self.roster_count.pack(side='left',padx=10)

    def filter_roster(self):
        rows=self.roster_rows
        for key,var in self.roster_vars.items():
            choices=['All',*sorted({str(r.get(key) or 'Unverified') for r in rows})]
            self.roster_menus[key].configure(values=choices)
            if var.get() not in choices:var.set('All')
            if var.get()!='All':rows=[r for r in rows if str(r.get(key) or 'Unverified')==var.get()]
        search=self.roster_search.get().strip().casefold()
        self.roster_filtered=[r for r in rows if not search or search in (r['student_id']+' '+r['name']).casefold()]
        self.roster_page=0;self.render_roster()

    def render_roster(self):
        self.roster_tree.delete(*self.roster_tree.get_children())
        for i,r in enumerate(self.roster_filtered[self.roster_page*50:(self.roster_page+1)*50]):
            self.roster_tree.insert('','end',iid=str(self.roster_page*50+i),values=[r.get(k,'') for k in self.roster_tree['columns']])
        confirmed=sum(r['status']=='confirmed' for r in self.roster_filtered)
        self.roster_count.configure(text=f'{len(self.roster_filtered)} matching rows · {confirmed} confirmed · Page {self.roster_page+1}')

    def roster_turn(self,delta):
        self.roster_page=max(0,min(self.roster_page+delta,max(0,(len(self.roster_filtered)-1)//50)));self.render_roster()

    def export_roster(self):
        if not self.roster_filtered:return
        path=filedialog.asksaveasfilename(parent=self,defaultextension='.csv',filetypes=[('CSV','*.csv')])
        if not path:return
        import csv
        with open(path,'w',newline='',encoding='utf-8-sig') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(self.roster_filtered[0]));writer.writeheader()
            for row in self.roster_filtered:
                writer.writerow({k:("'"+str(v) if str(v).lstrip().startswith(('=','+','-','@')) else v) for k,v in row.items()})

    def roster_history(self,_=None):
        chosen=self.roster_tree.selection()
        if not chosen:return
        from ui.student_management import open_student_details
        row=self.roster_filtered[int(chosen[0])]
        open_student_details(self,self.db,row['student_id'],self.actor,self.refresh)

    def prepare_draft(self):
        if not self.selected:
            messagebox.showinfo('Draft enrollment','Select a current or upcoming term first.',parent=self);return
        student=simpledialog.askstring('Draft enrollment','Existing permanent student ID:',parent=self)
        if not student:return
        row=self.db.get_student_by_student_id(student)
        if not row:
            messagebox.showinfo('Draft enrollment','Create an identity in Enrollment or import a demographic draft first.',parent=self);return
        from ui.academic_fields import AcademicFields
        window=ctk.CTkToplevel(self);window.title('Prepare demographics — fresh capture still required')
        fields=AcademicFields(window,row);fields.pack(fill='both',padx=15,pady=15)
        term_id=self.selected
        def save():
            try:self.db.save_enrollment_draft(student,term_id,fields.values(),actor=self.actor);window.destroy();self.refresh()
            except Exception as exc:messagebox.showerror('Draft enrollment',str(exc),parent=window)
        ctk.CTkButton(window,text='Save draft only',command=save).pack(pady=8)


    def correct_selected_enrollment(self):
        chosen=self.roster_tree.selection()
        if not chosen:return
        row=self.roster_filtered[int(chosen[0])]
        if not row.get('enrollment_id'):
            messagebox.showinfo('Correction','Prepare a term draft first.',parent=self);return
        from ui.academic_fields import AcademicFields
        window=ctk.CTkToplevel(self);window.title('Audited enrollment correction')
        fields=AcademicFields(window,row);fields.pack(fill='both',padx=15,pady=10)
        status=ctk.CTkOptionMenu(window,values=['Keep current status','Withdraw enrollment']);status.pack(pady=5)
        reason=ctk.CTkEntry(window,placeholder_text='Required correction reason',width=400);reason.pack(padx=15,pady=5)
        ctk.CTkLabel(window,text='Earlier incident snapshots remain unchanged. Confirmation requires fresh capture.').pack(padx=15)
        def save():
            try:
                self.db.correct_enrollment(row['enrollment_id'],actor=self.actor,reason=reason.get(),academics=fields.values(),
                    status='withdrawn' if status.get()=='Withdraw enrollment' else None)
                window.destroy();self.refresh()
            except Exception as exc:messagebox.showerror('Correction',str(exc),parent=window)
        ctk.CTkButton(window,text='Save audited correction',command=save).pack(pady=10)
