"""Real Python client against the isolated Miniflare fixture, never production."""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from urllib.request import Request, build_opener
from urllib.parse import urlsplit
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.campus_client import CampusClient, CampusCycle, _NoRedirect
from core.incremental_sync import migrate
from database.shared_profiles import save_profile,apply_profile
from database.db_manager import CBVMSDatabase

fixture=sys.argv[1]
url=urlsplit(fixture)
assert url.scheme=='http' and url.hostname in ('127.0.0.1','localhost','::1')
class FixtureOpener:
    def open(self,req,timeout):
        parsed=urlsplit(req.full_url)
        assert parsed.scheme=='https' and parsed.hostname=='fixture.invalid'
        return build_opener(_NoRedirect()).open(Request(fixture.rstrip('/')+parsed.path+('?' + parsed.query if parsed.query else ''),data=req.data,headers=dict(req.header_items()),method=req.method),timeout=timeout)

with tempfile.TemporaryDirectory() as directory:
    cycles=[];databases=[]
    for device,token,session in [('A','a'*40,'c'*40),('B','b'*40,'d'*40)]:
        database=CBVMSDatabase(Path(directory)/(device+'.db'));database.initialize()
        database.insert_student('s1','Restored update','BSIT','3A',b'',b'')
        actor=database.create_staff_session('admin','admin123')
        with database.connect() as c:
            c.execute('BEGIN IMMEDIATE');migrate(c)
            c.execute('INSERT INTO incremental_device(singleton,device_id) VALUES(1,?)',(device,))
            c.execute("INSERT INTO incremental_entity_versions VALUES('student','s1',3)")
        save_profile(database,'s1',{'name':'Edited by '+device},actor_token=actor,device_id=device,expected_version=3)
        client=CampusClient('https://fixture.invalid',lambda t=token,s=session:{'device_token':t,'actor_session':s},opener=FixtureOpener())
        cycles.append(CampusCycle(database.db_path,client,apply_profile));databases.append(database)
    assert cycles[0].run_once()
    assert cycles[1].run_once()
    assert cycles[0].run_once()
    for database,expected in zip(databases,['accepted','conflict']):
        with database.connect() as c:
            assert c.execute('SELECT state FROM incremental_outbox').fetchone()[0]==expected
            assert c.execute('SELECT cursor FROM incremental_cursor').fetchone()[0]==4
            assert c.execute('SELECT count(*) FROM incremental_inbox').fetchone()[0]==4
            assert c.execute('SELECT count(*) FROM incremental_outbox').fetchone()[0]==1
            assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    with databases[1].connect() as c:
        assert c.execute("SELECT name FROM students WHERE student_id='s1'").fetchone()[0]=='Edited by B'
        assert json.loads(c.execute('SELECT detail FROM incremental_conflicts').fetchone()[0])['current']['name']=='Edited by A'
    print('PASS real Python-to-Miniflare: two isolated desktops, actual receipts, conflict values retained, inbound cursor 4, later cycle, no upload loop.')
