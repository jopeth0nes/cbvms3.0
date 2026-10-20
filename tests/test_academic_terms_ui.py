"""Native desktop smoke test; disposable SQLite only, no camera or synchronization."""
import tempfile
import unittest
from pathlib import Path
import customtkinter as ctk
from database.db_manager import CBVMSDatabase
from ui.academic_terms import AcademicTermsWindow
from tests.term_fixture import verified_term


class AcademicTermsUITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.db=CBVMSDatabase(Path(self.tmp.name)/'ui.db');self.db.initialize(process_deadlines=False)
        self.db.insert_student('FIXTURE','Synthetic','BSIT','1A',None,None)
        self.term=verified_term(self.db,'FIXTURE',2026)
        with self.db.connect() as conn:
            self.actor=conn.execute("SELECT username FROM users WHERE role='superadmin'").fetchone()[0]
        self.root=ctk.CTk();self.root.withdraw();self.addCleanup(self.root.destroy)
        self.window=AcademicTermsWindow(self.root,self.db,self.actor)
        self.root.update()

    def test_term_selection_roster_and_cascading_filters(self):
        self.window.tree.selection_set(str(self.term));self.window.select()
        self.root.update()
        self.assertEqual(self.window.selected,self.term)
        self.assertEqual(len(self.window.roster_filtered),1)
        self.assertEqual(self.window.roster_filtered[0]['status'],'confirmed')
        self.window.roster_search.set('absent');self.window.filter_roster()
        self.assertEqual(self.window.roster_filtered,[])
        self.window.roster_search.set('FIXTURE');self.window.filter_roster()
        self.assertEqual(len(self.window.roster_tree.get_children()),1)

    def test_all_terms_and_exception_tab_exist(self):
        self.window.show_all();self.root.update()
        self.assertEqual(len(self.window.tabs.tabs()),4)
        self.assertEqual(len(self.window.roster_tree.get_children()),1)

if __name__=='__main__':unittest.main()
