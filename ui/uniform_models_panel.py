"""Native Evaluation & Models controls; expensive work never runs on Tk."""
from collections import deque
import json
import threading
from tkinter import filedialog, messagebox
import customtkinter as ctk

from ui.background_task import BackgroundTask
from ui.scrolling import ScrollableFrame
from ui.components import COLOR_BG, COLOR_TEXT_MUTED, heading_font


def format_report(report):
    """Show measured values verbatim; null metrics stay visibly unavailable."""
    if report.get('status') != 'completed':
        return f"Evaluation: {report.get('status', 'unavailable')}\n{report.get('failure') or report.get('error') or report.get('message') or 'No completed metrics.'}"
    summary = {key:report.get(key) for key in ('dataset_sha256','baseline','candidate','coverage',
        'per_model','campus_comparison','policy_version','limitations','synthetic','experimental')}
    def display_terms(value):
        labels = {'other_clothing': 'Wrong Uniform', 'Other Clothing': 'Wrong Uniform'}
        if isinstance(value, dict):
            return {labels.get(key, key): display_terms(item) for key, item in value.items()}
        if isinstance(value, list):
            return [display_terms(item) for item in value]
        if isinstance(value, str):
            return labels.get(value, value)
        return value
    summary = display_terms(summary)
    return ('Classification quality and uniform-policy correctness are separate.\n'
            'Per-class precision/recall include sample denominators and Wilson 95% intervals.\n'
            'Legacy binary labels do not establish Organizational Shirt or Campus Uniform classification recall.\n'
            'null = unavailable/undefined; unable-to-assess outcomes are not Wrong Uniform.\n\n'
            + json.dumps(summary,indent=2,ensure_ascii=False))


class UniformModelsWindow(ctk.CTkToplevel):
    def __init__(self, master, service, *, training_busy=lambda:False):
        super().__init__(master)
        self.title('Evaluation & Models')
        self.geometry('1000x830')
        self.minsize(820,550)
        self.transient(master.winfo_toplevel())
        self.service,self.training_busy = service,training_busy
        self.task = BackgroundTask(self)
        self.snapshot,self.loaded_report = {},{}
        self.cancelled = threading.Event()
        self.progress_updates = deque(maxlen=1)
        self.evaluating = False
        self._closed = False
        self._poll = None
        self.protocol('WM_DELETE_WINDOW',self.destroy)
        self.bind('<Escape>',lambda e:self.destroy())
        body = ScrollableFrame(self,fg_color=COLOR_BG)
        body.pack(fill='both',expand=True)
        ctk.CTkLabel(body,text='Evaluation & Models',font=heading_font()).pack(anchor='w',padx=14,pady=10)
        self.summary = ctk.CTkLabel(body,text='Reading model versions…',anchor='w',justify='left',wraplength=900)
        self.summary.pack(fill='x',padx=14,pady=6)
        self.logo_status = ctk.CTkLabel(body, text=(
            'Optional logo verifier: Not trained · Disabled\n'
            'Review photographed printed-logo boxes in Uniform Check. Reference artwork is separate. '
            'Enablement needs a trained bundle and a torso-only / torso-plus-logo comparison on identical held-out '
            'footage, with explicit classification, policy, unassessed-rate and added-latency criteria. '
            'A crest alone does not identify the garment or grant department/day permission.'),
            anchor='w', justify='left', wraplength=900, text_color=COLOR_TEXT_MUTED)
        self.logo_status.pack(fill='x', padx=14, pady=6)
        self.note = ctk.CTkLabel(body,text=(
            'Independent evaluation needs human-reviewed held-out examples and complete training provenance for both models. '
            'It uses the same examples for both models and never trains on the final test set.\n'
            'Choose the policy confidence before final testing; it is locked for that test set and model pair. '
            'Evaluation can compete with live monitoring for CPU. Pause monitoring if practical. '
            'Training and evaluation cannot run together. Closing this window requests evaluation cancellation.\n'
            'Activation requires a current superadmin session, an independent real-data report covering every garment category, '
            'explicit policy labels for compliant and violation examples, and a written acceptance review. '
            'No accuracy threshold is supplied by the application.'),anchor='w',justify='left',wraplength=900,text_color=COLOR_TEXT_MUTED)
        self.note.pack(fill='x',padx=14,pady=8)
        self.candidates = self._picker(body,'Candidate version',self.selection_changed)
        self.candidate_details = ctk.CTkLabel(body,text='No candidate selected.',anchor='w',justify='left',wraplength=900)
        self.candidate_details.pack(fill='x',padx=14,pady=4)
        self.sets = self._picker(body,'Independent final-test set',self.selection_changed)
        imports = ctk.CTkFrame(body,fg_color='transparent'); imports.pack(fill='x',padx=10,pady=5)
        self.import_button = ctk.CTkButton(imports,text='Import evaluation manifest',command=self.import_set)
        self.import_button.pack(side='left',padx=4)
        self.inventory_button = ctk.CTkButton(imports,text='Import training provenance',command=self.import_inventory)
        self.inventory_button.pack(side='left',padx=4)
        self.refresh_button = ctk.CTkButton(imports,text='Refresh',width=90,command=self.refresh)
        self.refresh_button.pack(side='left',padx=4)
        ctk.CTkLabel(body,text='Policy confidence (0.5–1; choose before final testing)',anchor='w').pack(fill='x',padx=14)
        self.threshold = ctk.CTkEntry(body,placeholder_text='No default confidence is assumed')
        self.threshold.pack(fill='x',padx=14,pady=4)
        self.threshold.bind('<KeyRelease>',lambda e:self._buttons())
        self.ack = ctk.CTkCheckBox(body,text='I understand CPU contention and will not tune against this final test set',command=self._buttons)
        self.ack.pack(anchor='w',padx=14,pady=6)
        actions = ctk.CTkFrame(body,fg_color='transparent'); actions.pack(fill='x',padx=10,pady=6)
        self.evaluate_button = ctk.CTkButton(actions,text='Evaluate candidate',command=self.evaluate,state='disabled')
        self.evaluate_button.pack(side='left',padx=4)
        self.cancel_button = ctk.CTkButton(actions,text='Cancel evaluation',command=self.cancel,state='disabled')
        self.cancel_button.pack(side='left',padx=4)
        self.progress = ctk.CTkProgressBar(body); self.progress.set(0); self.progress.pack(fill='x',padx=14,pady=6)
        self.status = ctk.CTkLabel(body,text='',anchor='w',justify='left',wraplength=900)
        self.status.pack(fill='x',padx=14,pady=4)
        self.reports = self._picker(body,'Saved evaluation report',self.load_report)
        self.metrics = ctk.CTkTextbox(body,height=300,wrap='word')
        self.metrics.pack(fill='x',padx=14,pady=6)
        self._text('No completed evaluation selected. No metrics are available.')
        self.criteria = ctk.CTkEntry(body,placeholder_text='Acceptance criteria decided by the responsible reviewer')
        self.criteria.pack(fill='x',padx=14,pady=4)
        self.rationale = ctk.CTkEntry(body,placeholder_text='Written acceptance rationale, or reason for rollback')
        self.rationale.pack(fill='x',padx=14,pady=4)
        for entry in (self.criteria,self.rationale): entry.bind('<KeyRelease>',lambda e:self._buttons())
        self.accepted = ctk.CTkCheckBox(body,text='I reviewed this report, its uncertainty, Campus Uniform comparison and missing coverage',command=self._buttons)
        self.accepted.pack(anchor='w',padx=14,pady=6)
        deployment = ctk.CTkFrame(body,fg_color='transparent'); deployment.pack(fill='x',padx=10,pady=8)
        self.activate_button = ctk.CTkButton(deployment,text='Activate Candidate',command=self.activate,state='disabled')
        self.activate_button.pack(side='left',padx=4)
        self.rollback_button = ctk.CTkButton(deployment,text='Roll Back',command=self.rollback,state='disabled')
        self.rollback_button.pack(side='left',padx=4)
        self.recover_button = ctk.CTkButton(deployment,text='Check switch recovery',command=self.recover,state='disabled')
        self.recover_button.pack(side='left',padx=4)
        self.refresh()

    def _picker(self,parent,title,command):
        ctk.CTkLabel(parent,text=title,anchor='w').pack(fill='x',padx=14)
        widget = ctk.CTkOptionMenu(parent,values=['None available'],command=command)
        widget.pack(fill='x',padx=14,pady=4)
        return widget

    def _text(self,text):
        self.metrics.configure(state='normal'); self.metrics.delete('1.0','end'); self.metrics.insert('1.0',text); self.metrics.configure(state='disabled')

    def _candidate(self):
        return next((r for r in self.snapshot.get('candidates',[]) if r['run_id']==self.candidates.get().split(' • ')[0]),None)

    def _set(self):
        return next((r for r in self.snapshot.get('sets',[]) if r['set_id']==self.sets.get().split(' • ')[0]),None)

    def _buttons(self):
        idle = not self.task.busy
        authorized = idle and self.snapshot.get('authorized',False)
        candidate,report = self._candidate(),self.loaded_report.get('report',{})
        ready_candidate = bool(candidate and candidate.get('status')=='completed')
        try: valid_threshold = .5<=float(self.threshold.get())<=1
        except ValueError: valid_threshold=False
        evaluate = authorized and ready_candidate and self._set() and valid_threshold and self.ack.get() and not self.training_busy()
        activate = (authorized and ready_candidate and not candidate.get('synthetic') and report.get('status')=='completed'
            and report.get('independent') and not report.get('synthetic') and self.accepted.get()
            and self.criteria.get().strip() and self.rationale.get().strip() and valid_threshold
            and candidate.get('id')==report.get('candidate',{}).get('id')
            and self.snapshot.get('active',{}).get('id')==report.get('baseline',{}).get('id')
            and all(report.get('per_model',{}).get('candidate',{}).get('classification',{}).get('per_class',{}).get(k,{}).get('support',0)>0
                    for k in ('campus_uniform','organizational_shirt','other_clothing'))
            and all(any(r.get('policy_label')==label for r in report.get('predictions',[])) for label in ('compliant','violation')))
        if activate:
            from core.uniform_model_registry import policy_rule,json_hash
            activate = report['candidate'].get('policy_version')==json_hash(policy_rule(float(self.threshold.get())))
        for widget,enabled in ((self.evaluate_button,evaluate),(self.cancel_button,self.evaluating and not self.cancelled.is_set()),
            (self.activate_button,activate),(self.rollback_button,authorized and self.snapshot.get('pointer',{} ) and self.snapshot['pointer'].get('previous') and self.rationale.get().strip()),
            (self.recover_button,authorized and self.snapshot.get('recovery_available',False)),
            (self.import_button,authorized),(self.inventory_button,authorized),(self.refresh_button,idle)):
            widget.configure(state='normal' if enabled else 'disabled')
        for widget in (self.candidates,self.sets,self.reports,self.threshold,self.criteria,self.rationale):
            widget.configure(state='normal' if idle else 'disabled')

    def selection_changed(self,_=None):
        candidate=self._candidate()
        self.candidate_details.configure(text=(f"Weights: {candidate.get('id') or 'Not completed'}\n"
            f"Training dataset manifest: {candidate.get('dataset_version','Unavailable')}\n"
            f"Preprocessing: {candidate.get('preprocessing','Unavailable')}\n"
            f"Status: {candidate['status']} • Synthetic: {candidate.get('synthetic',False)}" if candidate else 'No candidate available. Complete reviewed training prerequisites first.'))
        self.accepted.deselect(); self._buttons()

    def _failure(self,error):
        if not self.task.busy: self.evaluating=False
        self.status.configure(text=str(error)); self._buttons()

    def _run(self,operation,done=None):
        if self.task.busy:return
        def finished(value):
            self.evaluating=False
            if done: done(value)
            else: self.refresh()
            self._buttons()
        self.task.run(operation,finished,self._failure); self._buttons()

    def refresh(self):
        self._run(self.service.inventory,self._loaded)

    def _loaded(self,value):
        self.snapshot=value
        active=value['active']
        self.summary.configure(text=f"Active model: {active['id']}\nClasses: {', '.join(active['labels'])}\n"
            f"Dataset: {active.get('dataset_version','Unavailable')}\nPreprocessing: {active['preprocessing_version']}\n"
            f"{value['provenance']}\nCandidates: {len(value['candidates'])} • Independent sets: {len(value['sets'])}\n"
            + ('Current superadmin session verified.' if value['authorized'] else 'Model operations require a current superadmin session.'))
        for widget,rows,key in ((self.candidates,value['candidates'],'run_id'),(self.sets,value['sets'],'set_id'),(self.reports,value['reports'],'report_id')):
            choices=[f"{r[key]} • {r.get('status',r.get('version','reviewed set'))}" for r in rows] or ['None available']
            old=widget.get();widget.configure(values=choices);widget.set(old if old in choices else choices[0])
        self.status.configure(text='Review versions and prerequisites. Evaluation and activation are separate explicit actions.')
        self.selection_changed()

    def import_set(self):
        path=filedialog.askopenfilename(parent=self,title='Reviewed final-test manifest',filetypes=[('JSON','*.json')])
        if path:self._run(lambda:self.service.import_set(path))

    def import_inventory(self):
        path=filedialog.askopenfilename(parent=self,title='Complete reviewed training inventory (model_id must match)',filetypes=[('JSON','*.json')])
        if path:
            # The file names its model; the service validates its identity and provenance.
            active_id=self.snapshot['active']['id']
            candidate=self._candidate()
            candidate_path=candidate.get('path') if candidate else None
            def operation():
                from core.uniform_model_registry import read_json
                model_id=read_json(path).get('model_id')
                allowed=[active_id]
                if candidate_path:
                    from core.uniform_model_registry import candidate_descriptor
                    allowed.append(candidate_descriptor(candidate_path)['id'])
                if model_id not in allowed:raise ValueError('Inventory must describe the active or selected candidate model')
                return self.service.import_inventory(path,model_id)
            self._run(operation)

    def evaluate(self):
        if self.evaluate_button.cget('state')=='disabled':return
        path,set_id,threshold=self._candidate()['path'],self._set()['set_id'],float(self.threshold.get())
        self.cancelled.clear();self.evaluating=True;self.progress.set(0)
        def progress(done,total,message):self.progress_updates.append((done,total,message))
        self._run(lambda:self.service.evaluate(path,set_id,threshold,cancelled=self.cancelled,progress=progress),self._evaluated)
        self._tick()

    def _tick(self):
        self._poll=None
        if self._closed:return
        if self.progress_updates:
            done,total,message=self.progress_updates.pop();self.progress.set(done/total if total else 0);self.status.configure(text=message)
        if self.task.busy:self._poll=self.after(100,self._tick)

    def _evaluated(self,report_id):
        self.reports.set(report_id+' • completed');self.load_report(self.reports.get())

    def cancel(self):
        self.cancelled.set();self.status.configure(text='Cancellation requested; waiting for the current inference to finish.');self._buttons()

    def load_report(self,value):
        report_id=value.split(' • ')[0]
        if report_id=='None available':return
        self.accepted.deselect()
        def loaded(value):
            self.loaded_report=value;self._text(format_report(value['report']));self._buttons()
        self._run(lambda:self.service.report(report_id),loaded)

    def activate(self):
        if self.activate_button.cget('state')=='disabled':return
        candidate,report=self._candidate(),self.loaded_report['report']
        if not messagebox.askyesno('Activate Candidate',f"Switch the active model to candidate {candidate['run_id']}?\nThe current bundle will be preserved for rollback.",parent=self):return
        review={'accepted':True,'criteria':self.criteria.get(),'rationale':self.rationale.get(),'report_sha256':self.loaded_report['checksum']}
        path,report_id,threshold,token=candidate['path'],report['report_id'],float(self.threshold.get()),self.snapshot['token']
        self._run(lambda:self.service.activate(path,report_id,threshold,review,token))

    def rollback(self):
        if self.rollback_button.cget('state')=='disabled':return
        if not messagebox.askyesno('Roll Back','Restore the preserved compatible previous model bundle?',parent=self):return
        token,reason=self.snapshot['token'],self.rationale.get()
        self._run(lambda:self.service.rollback(token,reason))

    def recover(self):
        self._run(self.service.registry.recover)

    def destroy(self):
        if self._closed:return
        self._closed=True;self.cancelled.set()
        if self._poll is not None:
            self.after_cancel(self._poll);self._poll=None
        # BackgroundTask polls are registered on this window. Cancel scheduling
        # without deleting commands early; Tk destroys its own commands below.
        commands=set(self._tclCommands or ())
        for job in self.tk.splitlist(self.tk.call('after','info')):
            script=self.tk.call('after','info',job)[0]
            if str(script).split()[0] in commands:self.tk.call('after','cancel',job)
        super().destroy()
