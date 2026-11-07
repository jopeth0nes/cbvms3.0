"""Native Tk checks with fake service, synthetic metrics and no app/camera/DB."""
from copy import deepcopy
import gc
import threading
import time
import unittest
from unittest.mock import patch
import customtkinter as ctk
from ui.uniform_models_panel import UniformModelsWindow,format_report
from ui.window_lifecycle import WorkspaceWindow
from core.uniform_model_registry import CLASSES,PREPROCESS,json_hash,policy_rule


class FakeService:
    def __init__(self):
        self.calls=[];self.entered=threading.Event();self.finished=threading.Event()
        self.snapshot={'active':{'id':'baseline-fixture','labels':['correct_uniform','wrong_uniform'],
            'dataset_version':'unknown','preprocessing_version':'legacy-fixture'},'token':'token-fixture',
            'pointer':None,'candidates':[],'sets':[],'reports':[],'authorized':True,
            'provenance':'Independent evaluation blocked: missing reviewed provenance.'}
        self.report_value={'report_id':'report-fixture','status':'completed','independent':True,'synthetic':False,
            'baseline':{'id':'baseline-fixture'},'candidate':{'id':'weights-fixture','policy_version':json_hash(policy_rule(.8))},
            'per_model':{'candidate':{'classification':{'per_class':{c:{'support':1,'precision':None,'recall':None} for c in CLASSES}}}},
            'predictions':[{'policy_label':'compliant'},{'policy_label':'violation'}],
            'limitations':['Synthetic fixture only; no real metrics.']}
    def inventory(self):self.calls.append(('inventory',threading.get_ident()));return deepcopy(self.snapshot)
    def report(self,r):return {'report':deepcopy(self.report_value),'checksum':'checksum-fixture'}
    def evaluate(self,path,set_id,threshold,*,cancelled,progress):
        self.calls.append(('evaluate',threading.get_ident()));self.entered.set()
        progress(1,3,'Fixture progress 1/3')
        cancelled.wait(3)
        self.report_value['status']='cancelled';self.finished.set();return 'report-fixture'
    def activate(self,*args):self.calls.append(('activate',args))
    def rollback(self,*args):self.calls.append(('rollback',args))


class ModelsWindowTests(unittest.TestCase):
    def setUp(self):
        gc.collect()  # Dispose the previous test interpreter's fonts on Tk's thread.
        self.root=WorkspaceWindow();self.root.geometry('1050x900');self.errors=[]
        self.addCleanup(self.root.destroy)
        self.root.report_callback_exception=lambda *a:self.errors.append(a)
        self.service=FakeService();self.view=UniformModelsWindow(self.root,self.service);self.wait()
    def tearDown(self):
        if self.view.winfo_exists():self.view.destroy()
        self.root.destroy();self.assertEqual(self.errors,[])
    def pump(self):self.root.after(35,self.root.quit);self.root.mainloop()
    def wait(self):
        until=time.monotonic()+5
        while self.view.task.busy and time.monotonic()<until:self.pump()
        self.assertFalse(self.view.task.busy);self.pump()
    def populate(self):
        self.service.snapshot['candidates']=[{'run_id':'candidate-fixture','id':'weights-fixture','path':'/fixture/candidate',
            'status':'completed','synthetic':False,'dataset_version':'dataset-fixture','preprocessing':PREPROCESS}]
        self.service.snapshot['sets']=[{'set_id':'set-fixture','version':'held-out-fixture'}]
        self.service.snapshot['reports']=[{'report_id':'report-fixture','status':'completed'}]
        self.view.refresh();self.wait()
        self.view.threshold.insert(0,'.8');self.view.ack.select();self.view._buttons()
    def test_no_candidate_metrics_or_deployment_enabled_without_prerequisites(self):
        self.assertIn('missing reviewed provenance',self.view.summary.cget('text'))
        self.assertEqual(self.view.evaluate_button.cget('state'),'disabled')
        self.assertEqual(self.view.activate_button.cget('state'),'disabled')
        self.assertEqual(self.view.rollback_button.cget('state'),'disabled')
        self.assertIn('No metrics',self.view.metrics.get('1.0','end'))
        self.assertNotEqual(self.service.calls[0][1],threading.get_ident())
    def test_evaluation_progress_cancellation_and_recovered_report(self):
        self.populate();self.assertEqual(self.view.evaluate_button.cget('state'),'normal')
        self.view.evaluate()
        until=time.monotonic()+3
        while not self.service.entered.is_set() and time.monotonic()<until:self.pump()
        self.pump();self.view.cancel();self.wait()
        self.assertTrue(self.service.finished.is_set())
        self.assertIn('cancelled',self.view.metrics.get('1.0','end'))
        self.assertEqual(self.view.activate_button.cget('state'),'disabled')
    def test_review_requires_confirmation_and_correct_session_and_candidate(self):
        self.populate();self.view.load_report('report-fixture');self.wait()
        self.view.criteria.insert(0,'Human fixture criteria');self.view.rationale.insert(0,'Human fixture rationale')
        self.view.accepted.select();self.view._buttons()
        self.assertEqual(self.view.activate_button.cget('state'),'normal')
        with patch('ui.uniform_models_panel.messagebox.askyesno',return_value=False):self.view.activate()
        self.assertFalse(any(c[0]=='activate' for c in self.service.calls))
        with patch('ui.uniform_models_panel.messagebox.askyesno',return_value=True):self.view.activate()
        self.wait();self.assertEqual(sum(c[0]=='activate' for c in self.service.calls),1)
        self.view.snapshot['authorized']=False;self.view._buttons()
        self.assertEqual(self.view.activate_button.cget('state'),'disabled')
    def test_close_during_worker_cancels_and_has_no_late_tk_callbacks(self):
        self.populate();self.view.evaluate()
        until=time.monotonic()+3
        while not self.service.entered.is_set() and time.monotonic()<until:self.pump()
        self.view.destroy()
        for _ in range(8):self.pump()
        self.assertTrue(self.service.finished.is_set())
        self.assertTrue(self.view.cancelled.is_set())
    def test_report_nulls_and_legacy_limitations_are_explicit(self):
        text=format_report(self.service.report_value)
        self.assertIn('null = unavailable',text)
        self.assertIn('Legacy binary labels',text)
        self.assertIn('Synthetic fixture only',text)


if __name__=='__main__':unittest.main()
