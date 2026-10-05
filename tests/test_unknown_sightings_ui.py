"""Actual Tk workspace driven by simulated unknown encounters in a disposable DB."""
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
from PIL import Image

from tests import test_attendance_ui as fixtures


class UnknownSightingsUITests(unittest.TestCase):
    setUp=fixtures.AttendanceUITests.setUp
    tearDown=fixtures.AttendanceUITests.tearDown
    until=fixtures.AttendanceUITests.until

    def seed(self):
        output=BytesIO();Image.new('RGB',(80,100),'navy').save(output,format='JPEG')
        for i in range(61):
            self.db.log_security_event(f'person:1:{i}',event_key=f'encounter-{i:03}',
                source_id='gate',source_label='Gate',snapshot_jpeg=output.getvalue() if i else None,
                observed_at=datetime(2026,10,4,12,tzinfo=timezone.utc))
        self.panel.view.set('Unknown / Unrecognized');self.panel.page_size.set('25')
        self.panel.clear_filters();self.until(lambda:not self.panel.task.busy)

    def test_unknown_paging_export_snapshot_and_shared_records(self):
        self.seed();panel=self.panel
        self.assertEqual(panel.total,61);self.assertEqual(len(panel.rows),25)
        self.assertIn('61 matching encounters',panel.summary.cget('text'))
        self.assertIn('could not match',panel.description.cget('text'))
        self.assertEqual(panel.rows[0]['status'],'Unknown / Unrecognized')
        first=set(panel.tree.get_children());panel.change_page(1)
        self.until(lambda:not panel.task.busy)
        self.assertFalse(first.intersection(panel.tree.get_children()))
        target=Path(self.tmp.name)/'unknown.csv'
        with patch('ui.attendance_panel.filedialog.asksaveasfilename',return_value=str(target)):
            panel.export(False)
        self.until(lambda:not panel.export_task.busy)
        self.assertEqual(len(target.read_text().splitlines()),63)
        panel.tree.selection_set(panel.tree.get_children()[0])
        main=threading.get_ident();original=self.db.unknown_snapshot
        def read(*args,**kwargs):
            self.assertNotEqual(threading.get_ident(),main)
            return original(*args,**kwargs)
        image_open=Image.open
        def decode(*args,**kwargs):
            self.assertNotEqual(threading.get_ident(),main)
            return image_open(*args,**kwargs)
        with patch.object(self.db,'unknown_snapshot',side_effect=read), patch('ui.attendance_panel.Image.open',side_effect=decode):
            panel.open_snapshot();self.until(lambda:not panel.snapshot_task.busy)
        self.assertIsNotNone(panel.snapshot_window)
        panel.filters['search'].set('encounter-000');panel.refresh()
        self.until(lambda:not panel.task.busy)
        self.assertIsNone(panel.snapshot_window);self.assertEqual(panel.total,1)
        panel.tree.selection_set(panel.tree.get_children()[0]);panel.open_snapshot()
        self.until(lambda:not panel.snapshot_task.busy)
        self.assertIn('unavailable',panel.message.cget('text'))
        panel.filters['search'].set('missing');panel.refresh();self.until(lambda:not panel.task.busy)
        self.assertIn('No matching',panel.message.cget('text'))
        panel.view.set('Daily Summary');panel.clear_filters();self.until(lambda:not panel.task.busy)
        self.assertEqual(panel.total,1)
        records=fixtures.RecordsPanel(self.root,database=self.db,username='superadmin')
        records.grid(row=0,column=0,sticky='nsew')
        records._switch_tab('attendance')
        shared=records._attendance_frame
        shared.view.set('Unknown / Unrecognized');shared.clear_filters()
        self.until(lambda:not shared.task.busy)
        self.assertEqual(shared.total,61)
        records.destroy()

    def test_stale_queries_snapshots_and_errors_do_not_replace_current_view(self):
        self.seed();panel=self.panel
        entered,release=threading.Event(),threading.Event()
        original=self.db.query_unknown_sightings;main=threading.get_ident()
        def delayed(**kwargs):
            self.assertNotEqual(threading.get_ident(),main)
            entered.set();release.wait(3)
            return original(**kwargs)
        with patch.object(self.db,'query_unknown_sightings',side_effect=delayed):
            panel.refresh();self.until(entered.is_set)
            panel.view.set('Sighting Events');panel.refresh()
            beat=[];self.root.after(1,lambda:beat.append(1));self.until(lambda:beat)
            release.set();self.until(lambda:not panel.task.busy and panel.applied is not None)
        self.assertEqual(panel.applied[0],'events');self.assertEqual(panel.total,61)
        panel.view.set('Unknown / Unrecognized');panel.clear_filters();self.until(lambda:not panel.task.busy)
        panel.tree.selection_set(panel.tree.get_children()[0]);entered.clear();release.clear()
        original_snapshot=self.db.unknown_snapshot
        def slow_snapshot(*args,**kwargs):
            entered.set();release.wait(3)
            return original_snapshot(*args,**kwargs)
        with patch.object(self.db,'unknown_snapshot',side_effect=slow_snapshot):
            panel.open_snapshot();self.until(entered.is_set)
            panel.view.set('Daily Summary');panel.refresh()
            release.set();self.until(lambda:not panel.snapshot_task.busy and not panel.task.busy)
        self.assertIsNone(panel.snapshot_window)
        panel.view.set('Unknown / Unrecognized')
        with patch.object(self.db,'query_unknown_sightings',side_effect=RuntimeError('locked fixture')):
            panel.refresh();self.until(lambda:not panel.task.busy)
        self.assertIn('locked fixture',panel.message.cget('text'))
        self.assertEqual(panel.tree.get_children(),())
        panel.refresh();self.until(lambda:not panel.task.busy)
        self.assertEqual(panel.total,61)
        panel.username='student-fixture';panel.refresh();self.until(lambda:not panel.task.busy)
        self.assertIn('admin or superadmin',panel.message.cget('text'))
        self.assertEqual(panel.tree.get_children(),())
        self.assertIsNone(panel.applied)


if __name__=='__main__':unittest.main()
