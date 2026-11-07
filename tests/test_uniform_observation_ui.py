"""Native rendering/dedup checks with synthetic rows; no DB/model/camera."""
import gc
import unittest
import customtkinter as ctk
from ui.live_alerts import LiveAlerts,assessment_style
from ui.components import COLOR_ACCENT,COLOR_TEXT_MUTED


class UniformObservationUITests(unittest.TestCase):
    def setUp(self):
        gc.collect()
        self.root=ctk.CTk();self.root.geometry('550x450+60+60');self.errors=[]
        self.root.report_callback_exception=lambda *args:self.errors.append(args)
        self.alerts=LiveAlerts(self.root);self.alerts.pack(fill='both',expand=True)
    def tearDown(self):
        self.root.destroy();gc.collect();self.assertEqual(self.errors,[])
    def test_four_outcomes_update_same_widget_and_never_imply_policy_violation(self):
        card=None
        states=('Unable to assess','Campus Uniform recognized','Organizational Shirt recognized','Wrong Uniform recognized')
        for state in states:
            row={'presence_id':'synthetic-passage','track_id':1,'student_id':'fixture-only',
                 'name':'Synthetic UI fixture','reliable_identity':True,'state':state,
                 'detail':'Observation only · Policy not configured','accepted_categories':()}
            self.alerts.update_assessments([row]);self.root.update_idletasks();self.root.update()
            self.assertEqual(len(self.alerts._cards),1)
            widget=self.alerts._cards['synthetic-passage']
            if card is not None:self.assertIs(card,widget)
            card=widget
            label,color=assessment_style(row)
            self.assertEqual(label,state)
            self.assertEqual(color,COLOR_TEXT_MUTED if state=='Unable to assess' else COLOR_ACCENT)
        self.alerts.destroy();self.root.update_idletasks();self.root.update()


if __name__=='__main__':unittest.main()
