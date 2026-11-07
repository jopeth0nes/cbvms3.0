"""Native logo review on disposable synthetic images; no camera/model/records."""
import gc
from pathlib import Path
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image
from core.uniform_dataset import UniformDataset
from core.uniform_candidate import CandidateService
from ui.uniform_workspace import UniformWorkspace
from ui.window_lifecycle import WorkspaceWindow


class LogoReviewUITests(unittest.TestCase):
    def setUp(self):
        gc.collect()
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        base=Path(self.temp.name)
        self.dataset=UniformDataset(base/'catalog')
        image=Image.new('RGB',(400,300),'navy');image.paste('white',(100,80,160,120))
        path=base/'fixture.png';image.save(path)
        self.iid=self.dataset.import_paths([path],'organizational_shirt',capture_group='fixture-session')[0]
        self.root=WorkspaceWindow();self.addCleanup(self.root.destroy);self.errors=[]
        self.root.geometry('1100x850')
        self.root.report_callback_exception=lambda *args:self.errors.append(args)
        self.workspace=UniformWorkspace(self.root,capture=Mock(),dataset=self.dataset,
            candidate_service=CandidateService(self.dataset,candidates_root=base/'candidates'))
        self.workspace.pack(fill='both',expand=True);self.wait(self.workspace.task)
        self.workspace.open_review(self.dataset.snapshot()['records'][0])
        self.review=self.workspace._review;self.wait(self.review.task)

    def tearDown(self):
        self.root.destroy();gc.collect();self.assertEqual(self.errors,[])

    def pump(self):self.root.after(35,self.root.quit);self.root.mainloop()

    def wait(self,task):
        until=time.monotonic()+6
        while task.busy and time.monotonic()<until:self.pump()
        self.assertFalse(task.busy);self.pump()

    def set_logo(self):
        r=self.review
        r.set_crop([40,40,300,280])
        r.logo_box.insert(0,'100, 80, 160, 120')
        r.visibility.set('visible');r.logo_source.set('photographed_print')
        for check in r.flags.values():check.deselect()

    def test_original_preview_independent_review_and_readiness(self):
        r=self.review;self.set_logo();main=threading.get_ident();calls=[]
        original=self.dataset.image
        def image(*a,**k):calls.append(threading.get_ident());return original(*a,**k)
        self.dataset.image=image
        r.preview();self.wait(r.task)
        self.assertNotIn(main,calls)
        self.assertIn('60 × 40 original pixels',r.logo_preview.cget('text'))
        r.logo_confirm.select();r.logo_association.select();r.confirm.select()
        r.save('approved');self.wait(r.task);self.wait(self.workspace.task)
        snap=self.dataset.snapshot()
        self.assertEqual(snap['logo']['counts']['reviewed_visible'],1)
        self.assertEqual(self.dataset.logo_image(self.iid).size,(60,40))
        self.assertIn('Not trained',self.workspace.readiness.cget('text'))
        self.assertIn('1 eligible reviewed visible logos',self.workspace.readiness.cget('text'))
        self.workspace.open_review(snap['records'][0]);self.review=self.workspace._review;self.wait(self.review.task)
        self.assertFalse(self.review.logo_confirm.get())
        self.assertFalse(self.review.logo_association.get())

    def test_logo_drag_inside_torso_and_change_invalidates_confirmation(self):
        r=self.review;r.set_crop([40,40,300,280]);r.draw_mode.set('Logo box')
        r.start_crop(SimpleNamespace(x=100*r.scale,y=80*r.scale))
        r.drag_crop(SimpleNamespace(x=160*r.scale,y=120*r.scale))
        self.assertEqual(r.logo_coords(),[100,80,160,120])
        self.assertEqual(r.crop(),[40,40,300,280])
        r.logo_confirm.select();r.logo_association.select()
        r.set_crop([50,50,310,290])
        self.assertFalse(r.logo_confirm.get());self.assertFalse(r.logo_association.get())
        r.logo_box.delete(0,'end');r.logo_box.insert(0,'0, 0, 20, 20');r.preview()
        self.assertIn('inside',r.message.cget('text'))

    def test_hidden_state_saves_without_logo_box_or_negative_label(self):
        r=self.review;r.visibility.set('back_view');r.logo_source.set('photographed_print')
        r.logo_confirm.select();r.logo_association.select();r.save('pending_review')
        self.wait(r.task);self.wait(self.workspace.task)
        row=self.dataset.snapshot()['records'][0]
        self.assertEqual(row['logo_annotation']['visibility'],'back_view')
        self.assertIsNone(row['logo_annotation']['box'])
        self.assertEqual(self.dataset.snapshot()['logo']['counts']['reviewed_visible'],0)

    def test_close_during_logo_decode_has_no_late_tk_access(self):
        self.set_logo();started=threading.Event();release=threading.Event();done=threading.Event()
        original=self.dataset.image
        def image(*args,**kwargs):
            started.set();release.wait(2)
            try:return original(*args,**kwargs)
            finally:done.set()
        self.dataset.image=image
        self.review.preview();self.assertTrue(started.wait(1));self.review.destroy();release.set()
        self.assertTrue(done.wait(2))
        for _ in range(4):self.pump()


if __name__=='__main__':unittest.main()
