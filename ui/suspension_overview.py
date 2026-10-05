"""CustomTkinter suspension reporting with reusable Canvas charts and bounded reads."""
import queue
import tkinter as tk
from tkinter import ttk

import customtkinter as ctk

from core.academics import COLLEGES, YEAR_LEVELS, NEEDS_REVIEW, courses_for, COURSES
from core.discipline import parse_db_datetime, violation_display_name
from core.suspension_analytics import (ALL, MANILA, METRICS, OUTCOMES, SUSPENSION_STATES,
    UNAVAILABLE, ReportFilter, load_report, drill_rows)
from core.report_worker import ReportWorker
from ui.components import (COLOR_BG, COLOR_SURFACE, COLOR_BORDER, COLOR_TEXT, COLOR_TEXT_MUTED,
    COLOR_ACCENT, COLOR_ACCENT_HOVER, COLOR_DANGER, heading_font, body_small_font, enable_trackpad_scroll)

GOLD = '#D6B467'
TEAL = '#45B6A5'
BLUE = '#80AECB'


def label(parent, text, size=12, color=COLOR_TEXT_MUTED, **kwargs):
    return ctk.CTkLabel(parent,text=text,font=heading_font(size,'bold' if size>=16 else 'normal'),
                       text_color=color,anchor='w',justify='left',**kwargs)


class Chart(ctk.CTkFrame):
    """Canvas charts use real values, redraw in place, and wrap long labels."""
    def __init__(self, master, title, subtitle, *, command=None):
        super().__init__(master,fg_color=COLOR_SURFACE,corner_radius=14,border_width=1,border_color=COLOR_BORDER)
        self.title=label(self,title,17,COLOR_TEXT)
        self.title.pack(fill='x',padx=18,pady=(16,4))
        self.subtitle=label(self,subtitle,11,wraplength=420)
        self.subtitle.pack(fill='x',padx=18,pady=(0,8))
        self.canvas=tk.Canvas(self,bg=COLOR_SURFACE,highlightthickness=0,height=220)
        self.canvas.pack(fill='both',expand=True,padx=16,pady=(0,12))
        self.canvas.bind('<Configure>',self.redraw)
        self.rows=[]
        self.series=None
        self.command=command
        self.canvas.bind('<Button-1>',self._click)
        self.hits=[]
        self.point_hits=[]
        self.canvas.bind('<Motion>',self._hover)
        self.canvas.bind('<Leave>',lambda e:self.canvas.delete('hint'))

    def _hover(self,event):
        c=self.canvas
        c.delete('hint')
        for x,y,text in self.point_hits:
            if abs(event.x-x)<=9 and abs(event.y-y)<=9:
                tx=max(8,min(c.winfo_width()-195,event.x+10))
                ty=max(22,min(c.winfo_height()-50,event.y+12))
                item=c.create_text(tx+8,ty+5,text=text,anchor='nw',fill=COLOR_TEXT,
                    font=('Helvetica',11),tags='hint',width=180)
                bounds=c.bbox(item)
                box=c.create_rectangle(bounds[0]-6,bounds[1]-4,bounds[2]+6,bounds[3]+4,
                    fill=COLOR_BG,outline=COLOR_BORDER,tags='hint')
                c.tag_lower(box,item)
                break

    def set_bars(self, rows, subtitle=None):
        self.rows=rows
        self.series=None
        if subtitle: self.subtitle.configure(text=subtitle)
        self.canvas.configure(height=max(110,len(rows)*66+12))
        self.redraw()

    def set_trend(self, series, subtitle):
        self.series=series
        self.subtitle.configure(text=subtitle)
        self.canvas.configure(height=215)
        self.redraw()

    def _click(self,event):
        for y1,y2,key in self.hits:
            if y1<=event.y<=y2 and self.command:
                self.command(key)
                break

    def redraw(self,event=None):
        c=self.canvas
        c.delete('all'); self.hits=[]; self.point_hits=[]
        w=max(120,c.winfo_width()); h=max(100,c.winfo_height())
        self.subtitle.configure(wraplength=max(140,int(self._reverse_widget_scaling(w))-8))
        scale=self._get_widget_scaling()
        font=('Helvetica',max(10,round(11*scale)))
        if self.series is None:
            if not self.rows:
                c.create_text(w/2,50,text='No matching records in this scope.',fill=COLOR_TEXT_MUTED,font=font,width=w-30)
                return
            maximum=max(v for _,v in self.rows) or 1
            step=max(66,round(66*scale))
            y=8
            for index,(name,value) in enumerate(self.rows):
                text_id=c.create_text(0,y,text=name,fill=COLOR_TEXT,anchor='nw',width=w-60,font=font)
                row_height=max(step,c.bbox(text_id)[3]-y+28)
                c.create_text(w-2,y,text=f'{value:,}',fill=GOLD,anchor='ne',font=font)
                bottom=y+row_height-20
                c.create_rectangle(0,bottom,w,bottom+7,fill=COLOR_BORDER,width=0)
                c.create_rectangle(0,bottom,max(2,(w-1)*value/maximum),bottom+7,fill=TEAL,width=0)
                self.hits.append((y,y+row_height,name))
                y+=row_height
            self.canvas.configure(height=y)
            return
        keys=sorted({k for _,rows,_ in self.series for k,_ in rows})[-24:]
        if not keys:
            c.create_text(w/2,h/2,text='No dated records in this scope.',fill=COLOR_TEXT_MUTED,font=font,width=w-30)
            return
        maximum=max((v for _,rows,_ in self.series for k,v in rows if k in keys),default=1) or 1
        left,right,top,bottom=34,w-8,32,h-36
        for frac in (0,.5,1):
            y=bottom-frac*(bottom-top)
            c.create_line(left,y,right,y,fill=COLOR_BORDER)
            c.create_text(left-7,y,text=f'{maximum*frac:g}',fill=COLOR_TEXT_MUTED,anchor='e',font=font)
        for i,(name,rows,color) in enumerate(self.series):
            c.create_text(left+i*(w-40)/len(self.series),8,text='● '+name,fill=color,anchor='w',font=font)
            values=dict(rows)
            points=[]
            for index,key in enumerate(keys):
                x=left+(right-left)*index/max(1,len(keys)-1)
                y=bottom-values.get(key,0)/maximum*(bottom-top)
                points.extend((x,y))
                c.create_oval(x-3,y-3,x+3,y+3,fill=color,outline=color)
                self.point_hits.append((x,y,f'{name} · {key}\n{values.get(key,0):,} records'))
            if len(points)>=4: c.create_line(*points,fill=color,width=2)
        for index in sorted({0,len(keys)//2,len(keys)-1}):
            x=left+(right-left)*index/max(1,len(keys)-1)
            c.create_text(x,bottom+18,text=keys[index],anchor='w' if index==0 else 'e' if index==len(keys)-1 else 'center',
                          fill=COLOR_TEXT_MUTED,font=font)


class SuspensionsWorkspace(ctk.CTkFrame):
    def __init__(self, master, *, database, username, **kwargs):
        super().__init__(master,fg_color=COLOR_BG,**kwargs)
        self.database,self.username=database,username
        self.worker=ReportWorker()
        self._generation=0
        self._poll_job=self._debounce=None
        self._visible=False
        self._closed=False
        self._allowed=False
        self._pending_student=None
        self.report=None
        self.records=None
        self._drill=None
        self._drill_page=0
        self._layout_width=0
        self._syncing=False
        self._filters_open=False
        self.grid_columnconfigure(0,weight=1)
        self.grid_rowconfigure(2,weight=1)
        header=ctk.CTkFrame(self,fg_color='transparent')
        header.grid(row=0,column=0,sticky='ew',pady=(0,10))
        label(header,'Suspensions',26,COLOR_TEXT).pack(side='left')
        self.refresh_button=ctk.CTkButton(header,text='Refresh',width=100,command=self.refresh,
            fg_color=COLOR_BORDER,hover_color=COLOR_ACCENT_HOVER)
        self.refresh_button.pack(side='right')
        self.nav=ctk.CTkSegmentedButton(self,values=['Overview','Analytics','Student Records'],
            command=self._navigate,selected_color=COLOR_ACCENT,unselected_color=COLOR_SURFACE)
        self.nav.grid(row=1,column=0,sticky='w',pady=(0,12))
        self.nav.set('Overview')
        self.host=ctk.CTkFrame(self,fg_color='transparent')
        self.host.grid(row=2,column=0,sticky='nsew'); self.host.grid_columnconfigure(0,weight=1); self.host.grid_rowconfigure(0,weight=1)
        self.overview=ctk.CTkScrollableFrame(self.host,fg_color=COLOR_BG)
        self.overview.grid(row=0,column=0,sticky='nsew'); self.overview.grid_columnconfigure(0,weight=1)
        enable_trackpad_scroll(self.overview)
        self._build_overview()
        self.record_host=ctk.CTkFrame(self.host,fg_color='transparent')
        self.record_host.grid_columnconfigure(0,weight=1); self.record_host.grid_rowconfigure(1,weight=1)
        self.records_scroll=ctk.CTkFrame(self.record_host,fg_color=COLOR_BG)
        self.records_scroll.grid_columnconfigure(0,weight=1); self.records_scroll.grid_rowconfigure(0,weight=1)
        self.records_canvas=tk.Canvas(self.records_scroll,bg=COLOR_BG,highlightthickness=0)
        self.records_canvas.grid(row=0,column=0,sticky='nsew')
        self.records_container=ctk.CTkFrame(self.records_canvas,fg_color=COLOR_BG)
        self.records_container.grid_columnconfigure(0,weight=1); self.records_container.grid_rowconfigure(0,weight=1)
        self.records_window=self.records_canvas.create_window(0,0,anchor='nw',window=self.records_container)
        for vertical in (True,False):
            bar=ctk.CTkScrollbar(self.records_scroll,orientation='vertical' if vertical else 'horizontal',
                command=self.records_canvas.yview if vertical else self.records_canvas.xview)
            bar.grid(row=0 if vertical else 1,column=1 if vertical else 0,sticky='ns' if vertical else 'ew')
            self.records_canvas.configure(**{'yscrollcommand' if vertical else 'xscrollcommand':bar.set})
        self.records_canvas.bind('<Configure>',self._size_records)
        self.back=ctk.CTkButton(self.record_host,text='← Back to matching records',command=self._back_to_drill,
                              fg_color=COLOR_BORDER,width=190)
        self.drill_frame=ctk.CTkFrame(self.record_host,fg_color=COLOR_SURFACE)
        self.drill_frame.grid_columnconfigure(0,weight=1); self.drill_frame.grid_rowconfigure(2,weight=1)
        self.drill_title=label(self.drill_frame,'Matching records',18,COLOR_TEXT,wraplength=550)
        self.drill_title.grid(row=0,column=0,sticky='ew',padx=16,pady=12)
        self.drill_scope=label(self.drill_frame,'',11,wraplength=550)
        self.drill_scope.grid(row=1,column=0,sticky='ew',padx=16,pady=(0,12))
        table=ctk.CTkFrame(self.drill_frame,fg_color=COLOR_BG)
        table.grid(row=2,column=0,sticky='nsew',padx=16)
        table.grid_columnconfigure(0,weight=1); table.grid_rowconfigure(0,weight=1)
        columns=('record','student','date','status','group','category','term')
        from ui.suspensions_panel import configure_table_style
        configure_table_style()
        self.drill_tree=ttk.Treeview(table,columns=columns,show='headings',style='Suspensions.Treeview',selectmode='browse')
        for key,title,width in zip(columns,('Record / Student ID','Student','Date · Manila','Outcome / Status','Current course','Category','Recorded term'),(190,200,160,200,260,140,180)):
            self.drill_tree.heading(key,text=title); self.drill_tree.column(key,width=width,minwidth=90)
        self.drill_tree.grid(row=0,column=0,sticky='nsew')
        for vertical in (True,False):
            bar=ttk.Scrollbar(table,orient='vertical' if vertical else 'horizontal',command=self.drill_tree.yview if vertical else self.drill_tree.xview)
            bar.grid(row=0 if vertical else 1,column=1 if vertical else 0,sticky='ns' if vertical else 'ew')
            self.drill_tree.configure(**{'yscrollcommand' if vertical else 'xscrollcommand':bar.set})
        actions=ctk.CTkFrame(self.drill_frame,fg_color='transparent'); actions.grid(row=3,column=0,sticky='ew',padx=16,pady=16)
        ctk.CTkButton(actions,text='Previous',width=80,command=lambda:self._page(-1)).pack(side='left')
        ctk.CTkButton(actions,text='Next',width=70,command=lambda:self._page(1)).pack(side='left',padx=6)
        self.open_record=ctk.CTkButton(actions,text='Open student',width=130,command=self._open_drill_student)
        self.open_record.pack(side='right')
        ctk.CTkButton(self.drill_frame,text='Browse all students',command=self._directory,
            fg_color=COLOR_BORDER).grid(row=4,column=0,sticky='w',padx=16,pady=(0,12))
        self.drill_tree.bind('<Double-1>',lambda e:self._open_drill_student())
        self.drill_tree.bind('<<TreeviewSelect>>',lambda e:self._drill_selection())
        self.bind('<Configure>',self._resize,add='+')

    def _build_overview(self):
        p=self.overview
        self.intro=label(p,'Discipline at a glance',21,COLOR_TEXT)
        self.intro.grid(row=0,column=0,sticky='ew',padx=4,pady=(2,4))
        self.scope=label(p,'Loading reporting scope…',12,wraplength=700)
        self.scope.grid(row=1,column=0,sticky='ew',padx=4,pady=(0,12))
        toolbar=ctk.CTkFrame(p,fg_color=COLOR_SURFACE,corner_radius=12)
        toolbar.grid(row=2,column=0,sticky='ew',pady=(0,10)); toolbar.grid_columnconfigure(0,weight=1)
        self.period_var=tk.StringVar(value='Current academic term')
        self.period_menu=ctk.CTkOptionMenu(toolbar,variable=self.period_var,
            values=['Current academic term','All Data','Custom date range','Unassigned term'],dynamic_resizing=False,
            command=lambda _:self._changed())
        self.period_menu.grid(row=0,column=0,sticky='ew',padx=12,pady=12)
        self.filter_button=ctk.CTkButton(toolbar,text='Filters +',width=95,command=self._toggle_filters,fg_color=COLOR_BORDER)
        self.filter_button.grid(row=0,column=1,padx=(0,12),pady=12)
        self.filters_frame=ctk.CTkFrame(p,fg_color=COLOR_SURFACE)
        self.variables={}; self.filter_controls=[]; self.menus={}
        specs=[('college','College / department',[ALL,*COLLEGES,NEEDS_REVIEW,UNAVAILABLE]),
               ('course','Course',[ALL,*[x[1] for x in COURSES.values()],NEEDS_REVIEW,UNAVAILABLE]),
               ('year','Year level',[ALL,*YEAR_LEVELS,NEEDS_REVIEW,UNAVAILABLE]),
               ('category','Violation category',[ALL]),('outcome','Violation outcome',[ALL,*OUTCOMES]),
               ('suspension_status','Suspension status',[ALL,*SUSPENSION_STATES]),
               ('interval','Chart buckets',['Month','Week','Day']),('rank','Rank groups by',list(METRICS.values()))]
        for key,title,values in specs:
            cell=ctk.CTkFrame(self.filters_frame,fg_color='transparent')
            label(cell,title,11).pack(fill='x',pady=(0,4))
            var=tk.StringVar(value=values[0]); self.variables[key]=var
            menu=ctk.CTkOptionMenu(cell,values=values,variable=var,dynamic_resizing=False,width=140,
                command=lambda _,k=key:self._changed(k))
            menu.pack(fill='x'); self.menus[key]=menu; self.filter_controls.append(cell)
        for key,title in (('start','From · YYYY-MM-DD (Manila)'),('end','Through · YYYY-MM-DD (Manila)')):
            cell=ctk.CTkFrame(self.filters_frame,fg_color='transparent')
            label(cell,title,11).pack(fill='x',pady=(0,4))
            var=tk.StringVar(); self.variables[key]=var
            entry=ctk.CTkEntry(cell,textvariable=var,placeholder_text='YYYY-MM-DD')
            entry.pack(fill='x'); var.trace_add('write',lambda *_:self._changed())
            self.filter_controls.append(cell)
        cell=ctk.CTkFrame(self.filters_frame,fg_color='transparent')
        ctk.CTkButton(cell,text='Reset filters',command=self.reset_filters,fg_color=COLOR_BORDER).pack(fill='x',pady=(18,0))
        self.filter_controls.append(cell)
        self.status=label(p,'',12,wraplength=700)
        self.status.grid(row=4,column=0,sticky='ew',padx=4,pady=(0,10))
        self.cards_frame=ctk.CTkFrame(p,fg_color='transparent'); self.cards_frame.grid(row=5,column=0,sticky='ew')
        self.cards=[]; self.card_values={}; self.card_bases={}
        cards=[('active','Currently suspended','Distinct students · current snapshot',GOLD),
            ('suspensions','Suspensions started','Records · suspension start date',TEAL),
            ('students','Students suspended','Distinct students · start date',TEAL),
            ('recorded','Recorded violations','Detections · recorded date',BLUE),
            ('finalized','Finalized violations','Active strikes · award date',GOLD)]
        for key,title,basis,color in cards:
            card=ctk.CTkFrame(self.cards_frame,fg_color=COLOR_SURFACE,corner_radius=14,border_width=1,border_color=COLOR_BORDER)
            title_label=label(card,title,14,COLOR_TEXT); title_label.pack(fill='x',padx=16,pady=(14,4))
            value=label(card,'—',32,color); value.pack(fill='x',padx=16)
            basis_label=label(card,basis,11,wraplength=220); basis_label.pack(fill='x',padx=16,pady=(2,10))
            button=ctk.CTkButton(card,text='View records  →',command=lambda k=key:self._drill_open(k),
                anchor='w',height=26,fg_color='transparent',text_color=color,hover_color=COLOR_BORDER)
            button.pack(fill='x',padx=8,pady=(0,8))
            for widget in (title_label,value,basis_label): widget.bind('<Button-1>',lambda e,k=key:self._drill_open(k))
            self.cards.append(card); self.card_values[key]=value; self.card_bases[key]=basis_label
        self.chart_frame=ctk.CTkFrame(p,fg_color='transparent'); self.chart_frame.grid(row=6,column=0,sticky='ew',pady=12)
        self.violation_chart=Chart(self.chart_frame,'Violation activity','Detections and active finalized outcomes have different date bases.')
        self.suspension_chart=Chart(self.chart_frame,'Suspensions started','Suspension records by start date · Asia/Manila')
        self.college_chart=Chart(p,'College / department','Highest count in the selected period',command=lambda v:self._drill_open(self._metric(),'college',v))
        self.college_chart.grid(row=7,column=0,sticky='ew',pady=(0,12))
        self.analytics=ctk.CTkFrame(p,fg_color='transparent'); self.analytics.grid_columnconfigure(0,weight=1)
        self.course_chart=Chart(self.analytics,'Course comparison','',command=lambda v:self._drill_open(self._metric(),'course',v))
        self.course_chart.grid(row=0,column=0,sticky='ew',pady=(0,12))
        self.year_chart=Chart(self.analytics,'Year-level comparison','',command=lambda v:self._drill_open(self._metric(),'year',v))
        self.year_chart.grid(row=1,column=0,sticky='ew',pady=(0,12))
        self.outcome_chart=Chart(self.analytics,'Recorded violation outcomes','Recorded date · current outcome',command=lambda v:self._drill_open('recorded','status',v))
        self.outcome_chart.grid(row=2,column=0,sticky='ew',pady=(0,12))
        self.state_chart=Chart(self.analytics,'Suspension record status','Start date · status as of refresh',command=lambda v:self._drill_open('suspensions','status',v))
        self.state_chart.grid(row=3,column=0,sticky='ew',pady=(0,12))
        self.more=ctk.CTkButton(p,text='Explore courses, year levels & outcomes  →',command=lambda:self._navigate('Analytics'),fg_color=COLOR_BORDER)
        self.more.grid(row=8,column=0,sticky='ew',pady=(0,12))
        self.note=label(p,'',11,wraplength=700)
        self.note.grid(row=10,column=0,sticky='ew',padx=4,pady=(2,18))
        self._term_options={}
        self._category_options={ALL:ALL}

    def _metric(self):
        return next(k for k,v in METRICS.items() if v==self.variables['rank'].get())

    def _filter(self):
        period={'Current academic term':'current','All Data':'all','Custom date range':'custom','Unassigned term':'unassigned'}.get(
            self.period_var.get(),self._term_options.get(self.period_var.get(),'current'))
        values={k:v.get() for k,v in self.variables.items()}
        values['rank']=self._metric()
        values['category']=self._category_options.get(values['category'],values['category'])
        return ReportFilter(period=period,**values)

    def _changed(self,key=None):
        if self._syncing or self._closed: return
        self.report=None
        self._generation+=1  # Invalidate before debounce; old reads must never render.
        if key=='college':
            college=self.variables['college'].get()
            courses=([x[1] for x in COURSES.values()] if college==ALL else
                     [college] if college in (NEEDS_REVIEW,UNAVAILABLE) else list(courses_for(college)))
            self.menus['course'].configure(values=[ALL,*courses])
            self.variables['course'].set(ALL)
        if self.period_var.get()=='Custom date range' and not self._filters_open:
            self._toggle_filters()
        if self._debounce is not None: self.after_cancel(self._debounce)
        self.status.configure(text='Filters changed · updating…')
        self._debounce=self.after(250,self.refresh)

    def reset_filters(self):
        self._syncing=True
        for key,var in self.variables.items():
            var.set('Month' if key=='interval' else METRICS['recorded'] if key=='rank' else '' if key in ('start','end') else ALL)
        self.period_var.set('Current academic term'); self._syncing=False
        self._changed('college')

    def _toggle_filters(self):
        self._filters_open=not self._filters_open
        self.filter_button.configure(text='Filters −' if self._filters_open else 'Filters +')
        if self._filters_open: self.filters_frame.grid(row=3,column=0,sticky='ew',pady=(0,12))
        else: self.filters_frame.grid_remove()
        self._layout(force=True)

    def refresh(self):
        self._debounce=None
        if self._closed or not self._visible: return
        if self.nav.get()=='Student Records' and self.records is not None and self._allowed and self.records_scroll.winfo_ismapped():
            self.records.refresh(); return
        self._generation+=1; generation=self._generation
        try:
            filters=self._filter(); filters.bounds()
        except ValueError as exc:
            self.status.configure(text=str(exc),text_color=COLOR_DANGER); return
        self.status.configure(text='Loading a consistent read-only snapshot…',text_color=COLOR_TEXT_MUTED)
        self.worker.offer(generation,lambda:load_report(self.database,self.username,filters))
        if self._poll_job is None: self._poll_job=self.after(40,self._poll)

    def _poll(self):
        self._poll_job=None
        try:
            generation,data,error=self.worker.results.get_nowait()
        except queue.Empty:
            generation=None
        if generation==self._generation and self._visible:
            if error:
                self.report=None; self._allowed=False
                for value in self.card_values.values(): value.configure(text='—')
                self.status.configure(text=f'Could not load report: {error}  Use Refresh to retry.',text_color=COLOR_DANGER)
                for chart in self._charts(): chart.set_bars([])
                if self.nav.get()=='Student Records':
                    self.drill_scope.configure(text=f'Refresh failed: {error}. Use Refresh to retry. Existing rows are from the previous snapshot.')
                    self.open_record.configure(state='disabled')
            else:
                self._allowed=True; self.report=data; self._render()
                if self._drill is not None and self.nav.get()=='Student Records':
                    self._drill=(*self._drill[:3],data)
                    self._drill_page=0
                    self._render_drill()
                if self._pending_student is not None:
                    sid=self._pending_student; self._pending_student=None
                    self.select_student(sid)
        if self._visible and not self._closed: self._poll_job=self.after(80,self._poll)

    def _charts(self):
        return (self.violation_chart,self.suspension_chart,self.college_chart,self.course_chart,self.year_chart,self.outcome_chart,self.state_chart)

    def _render(self):
        r=self.report
        self._term_options={f"{t['semester_name']} · {t['school_year']} [#{t['id']}]":f"term:{t['id']}" for t in r['terms']}
        self.period_menu.configure(values=['Current academic term','All Data','Custom date range','Unassigned term',*self._term_options])
        self._category_options={violation_display_name(code):code for code in r['categories']}
        self.menus['category'].configure(values=[ALL,*self._category_options])
        self.menus['year'].configure(values=list(dict.fromkeys([ALL,*YEAR_LEVELS,*r['years'],NEEDS_REVIEW,UNAVAILABLE])))
        self.scope.configure(text=r['scope'])
        self.status.configure(text=f"Refreshed {r['refreshed_at']}"+(' · No matching period records.' if not any(r['counts'][k] for k in METRICS) else ''),text_color=COLOR_TEXT_MUTED)
        for key,value in self.card_values.items(): value.configure(text=f"{r['counts'][key]:,}")
        interval=r['filters'].interval
        self.violation_chart.set_trend([('Recorded',r['trends']['recorded'],BLUE),('Finalized',r['trends']['finalized'],GOLD)],
            f'{interval} · detected date / active strike award date · Manila\nLatest 24 buckets; tables include the full scope.')
        self.suspension_chart.set_trend([('Starts',r['trends']['suspensions'],TEAL)],f'{interval} · suspension start date · Manila\nLatest 24 buckets; tables include the full scope.')
        rank=METRICS[r['filters'].rank]
        basis='recorded date' if r['filters'].rank=='recorded' else 'active strike award date' if r['filters'].rank=='finalized' else 'suspension start date'
        for chart,field in ((self.college_chart,'college'),(self.course_chart,'course'),(self.year_chart,'year')):
            chart.set_bars(r['rankings'][field],f'{rank} · {basis}\nHighest count in the selected period · click a bar for records')
        self.outcome_chart.set_bars(r['outcomes'])
        self.state_chart.set_bars(r['suspension_states'])
        outcomes=dict(r['outcomes'])
        self.card_bases['recorded'].configure(text=f"Recorded date · {outcomes.get('Pending review',0)+outcomes.get('Appeal pending',0)} pending\n{outcomes.get('Resolved / dismissed',0)} resolved / dismissed")
        self.note.configure(text=(f"Recorded outcomes: {outcomes.get('Pending review',0)} pending review · "
            f"{outcomes.get('Appeal pending',0)} appeal pending · {outcomes.get('Resolved / dismissed',0)} resolved / dismissed.\n\n"
            'Grouping uses current student classifications; discipline records have no historical academic snapshots. '
            'Counts are not rates. Missing student records remain in an unavailable-classification group.\n'
            'Current suspension snapshot ignores period and status filters; college, course, year and linked category still apply. '
            'Violation outcome filters apply to violations only; suspension status filters apply to suspension-start measures only.\n'
            'Finalized = currently active strikes, excluding dismissed/resolved records and approved or pending appeals, by stored award date. '
            'Escalation replacements can create several historical suspension records for one student.\n'
            f"Suspension terms use the automatic award or related violation; {r['unassigned_suspensions']} selected records are unassigned. "
            f"Undated records excluded from trends: {sum(r['undated'][k] for k in ('recorded','finalized','suspensions'))}."))
        self._layout(force=True)

    def _resize(self,event=None):
        if event is None or event.widget is self: self._layout()

    def _layout(self,force=False):
        width=int(self._reverse_widget_scaling(self.winfo_width()))
        if not force and abs(width-self._layout_width)<25: return
        self._layout_width=width
        columns=3 if width>=820 else 2 if width>=560 else 1
        for col in range(3): self.cards_frame.grid_columnconfigure(col,weight=1 if col<columns else 0,uniform='cards' if col<columns else '')
        for index,card in enumerate(self.cards): card.grid(row=index//columns,column=index%columns,sticky='nsew',padx=4,pady=4)
        filter_cols=2 if width>=620 else 1
        for col in range(2): self.filters_frame.grid_columnconfigure(col,weight=1 if col<filter_cols else 0)
        for index,cell in enumerate(self.filter_controls): cell.grid(row=index//filter_cols,column=index%filter_cols,sticky='ew',padx=12,pady=8)
        chart_cols=2 if width>=900 else 1
        for col in range(2): self.chart_frame.grid_columnconfigure(col,weight=1 if col<chart_cols else 0,uniform='charts' if col<chart_cols else '')
        for index,chart in enumerate((self.violation_chart,self.suspension_chart)):
            chart.grid(row=index//chart_cols,column=index%chart_cols,sticky='nsew',padx=4,pady=4)
        for item in (self.scope,self.status,self.note,self.drill_title,self.drill_scope): item.configure(wraplength=max(200,width-65))

    def _navigate(self,page):
        self.nav.set(page)
        if page=='Student Records':
            if not self._allowed:
                self.nav.set('Overview'); return
            self.overview.grid_remove(); self.record_host.grid(row=0,column=0,sticky='nsew')
            if self._drill is not None: self._back_to_drill()
            else: self._show_records()
        else:
            self.record_host.grid_remove(); self.overview.grid(row=0,column=0,sticky='nsew')
            self.intro.configure(text='Trends & comparisons' if page=='Analytics' else 'Discipline at a glance')
            if page=='Analytics':
                self.analytics.grid(row=9,column=0,sticky='ew'); self.more.grid_remove()
            else:
                self.analytics.grid_remove(); self.more.grid()

    def _show_records(self,sid=None):
        from ui.suspensions_panel import SuspensionsPanel
        if self.records is None:
            self.records=SuspensionsPanel(self.records_container,database=self.database,username=self.username,embedded=True)
        self.drill_frame.grid_remove()
        if self._drill is not None: self.back.grid(row=0,column=0,sticky='w',pady=(0,8))
        else: self.back.grid_remove()
        self.records_scroll.grid(row=1,column=0,sticky='nsew')
        self.records.grid(row=0,column=0,sticky='nsew')
        self._size_records()
        if sid is not None: self.records.on_show(sid)
        elif not self.records._students or (self.records.student_id and not self.records._ready): self.records.on_show()

    def select_student(self,sid):
        if not self._allowed:
            self._pending_student=sid; self.refresh(); return
        self.nav.set('Student Records'); self.overview.grid_remove()
        self.record_host.grid(row=0,column=0,sticky='nsew')
        self._show_records(str(sid))

    def _drill_open(self,metric,field=None,value=None):
        if self.report is None or not self._allowed: return
        self.drill_tree.selection_remove(*self.drill_tree.selection())
        self._drill=(metric,field,value,self.report)
        self._drill_page=0
        self._navigate('Student Records')

    def _size_records(self,event=None):
        scale=self._get_widget_scaling()
        width=max(round(680*scale),self.records_canvas.winfo_width())
        height=max(round(1000*scale),self.records_canvas.winfo_height())
        self.records_canvas.itemconfigure(self.records_window,width=width,height=height)
        self.records_canvas.configure(scrollregion=(0,0,width,height))

    def _directory(self):
        self._drill=None
        self._show_records()

    def _back_to_drill(self):
        self.records_scroll.grid_remove()
        self.back.grid_remove()
        self.drill_frame.grid(row=1,column=0,sticky='nsew')
        self._render_drill()

    def _render_drill(self):
        if self._drill is None: return
        metric,field,value,report=self._drill
        rows=drill_rows(report,metric,field=field,value=value)
        self._drill_rows=rows
        self.drill_title.configure(text=f"{METRICS.get(metric,'Currently suspended students')}"+(f' · {value}' if field else ''))
        start=self._drill_page*40
        scope = ('Current snapshot · ignores period and status selectors' if metric=='active' else report['scope'])
        self.drill_scope.configure(text=f"{scope}\n{len(rows)} matches · {start+1 if rows else 0}–{min(start+40,len(rows))} · "
            f"Snapshot: {report['refreshed_at']}\nOpen student shows their full history. Missing student classifications remain visible here.")
        selection=self.drill_tree.selection()
        self.drill_tree.delete(*self.drill_tree.get_children())
        date_title='Awarded' if metric=='finalized' else 'Detected' if metric=='recorded' else 'Started'
        self.drill_tree.heading('date',text=f'{date_title} · Manila')
        terms={t['id']:f"{t['semester_name']} · {t['school_year']}" for t in report['terms']}
        for index,row in enumerate(rows[start:start+40],start):
            at=parse_db_datetime(row['date'])
            self.drill_tree.insert('','end',iid=str(index),values=(f"{row['kind']} #{row['id']} · {row['student_id']}",row['name'],
                at.astimezone(MANILA).strftime('%Y-%m-%d %H:%M') if at else 'Date unavailable',row['status'],row['course'],violation_display_name(row['category']),terms.get(row['term_id'],'Unassigned')))
        if selection and self.drill_tree.exists(selection[0]):
            self.drill_tree.selection_set(selection[0]); self.drill_tree.see(selection[0])
        self._drill_selection()

    def _drill_selection(self):
        selected=self.drill_tree.selection()
        available=bool(selected and self._drill_rows[int(selected[0])]['available'])
        self.open_record.configure(state='normal' if available else 'disabled')

    def _page(self,delta):
        pages=max(1,(len(getattr(self,'_drill_rows',[]))+39)//40)
        self._drill_page=max(0,min(pages-1,self._drill_page+delta)); self._render_drill()

    def _open_drill_student(self):
        selected=self.drill_tree.selection()
        if selected:
            row=self._drill_rows[int(selected[0])]
            if row['available']: self.select_student(row['student_id'])

    def on_show(self,student_id=None):
        self._visible=True
        if student_id is not None: self._pending_student=str(student_id)
        self._allowed=False
        self._navigate('Overview')
        self.refresh()

    def on_hide(self):
        self._visible=False; self._generation+=1
        for name in ('_poll_job','_debounce'):
            job=getattr(self,name)
            if job is not None: self.after_cancel(job); setattr(self,name,None)
        if self.records is not None: self.records.on_hide()

    def destroy(self):
        if not self._closed:
            self.on_hide(); self._closed=True; self.worker.close()
        super().destroy()
