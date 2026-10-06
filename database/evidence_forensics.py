"""Privileged persistence and stale-result invalidation for forensic reports."""
import base64
from contextlib import contextmanager
import hashlib
import json
import sqlite3
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path
import tempfile
import time
from datetime import datetime, timedelta, timezone

LEASE_SECONDS = 180
JOB_TIMEOUT_SECONDS = 120
METADATA_TIMEOUT_SECONDS = 45
ARTIFACT_LIMIT = 512 * 1024
MAX_WORKER_OUTPUT = 2 * 1024 * 1024
MAX_INPUT_BYTES = 10 * 1024 * 1024

def autostart_enabled():
    """Keep test runs deterministic; tests can drain jobs explicitly with run_one."""
    import sys
    if AUTOSTART_OVERRIDE is not None:
        return AUTOSTART_OVERRIDE
    return "unittest" not in sys.modules and "pytest" not in sys.modules

AUTOSTART_OVERRIDE = None

def now_text():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

@contextmanager
def _connect(db):
    """Worker-only connection independent of a student's expiring UI session."""
    conn = sqlite3.connect(db.db_path, timeout=db.timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {int(db.timeout * 1000)}")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def require_admin(conn, username, student_session_ref):
    if student_session_ref is not None:
        raise PermissionError("An authenticated administrator is required.")
    row = conn.execute("SELECT role FROM users WHERE username=?", ((username or "").strip(),)).fetchone()
    if row is None or row[0] not in ("admin", "superadmin"):
        raise PermissionError("An authenticated administrator is required.")

def create_run(db, evidence_id):
    with _connect(db) as conn:
        conn.execute("BEGIN IMMEDIATE")
        evidence = conn.execute("SELECT id,appeal_id,file_sha256 FROM evidence_files WHERE id=?", (evidence_id,)).fetchone()
        if not evidence:
            return None
        active = conn.execute("SELECT id FROM evidence_forensics_runs WHERE evidence_id=? AND status IN ('pending','analyzing')", (evidence_id,)).fetchone()
        if active:
            return int(active[0])
        blob = conn.execute("SELECT file_data FROM evidence_files WHERE id=?",(evidence_id,)).fetchone()[0]
        source_hash = hashlib.sha256(bytes(blob)).hexdigest()
        cur = conn.execute("INSERT INTO evidence_forensics_runs(evidence_id,appeal_id,source_sha256,status,created_at) VALUES(?,?,?,'pending',?)",
                           (evidence_id, evidence["appeal_id"], source_hash, now_text()))
        return int(cur.lastrowid)

def _claim(db, run_id):
    with _connect(db) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _expire_claims(conn)
        active = conn.execute("SELECT id FROM evidence_forensics_runs WHERE status='analyzing'").fetchone()
        if active and int(active[0]) != int(run_id):
            return None
        row = conn.execute("SELECT r.*,e.file_data,e.filename,e.file_sha256 FROM evidence_forensics_runs r JOIN evidence_files e ON e.id=r.evidence_id WHERE r.id=?", (run_id,)).fetchone()
        if not row or row["status"] != "pending":
            return None
        data = bytes(row["file_data"])
        if not data or len(data) > MAX_INPUT_BYTES:
            conn.execute("UPDATE evidence_forensics_runs SET status='error',classification='inconclusive',completed_at=?,error_code='source_size_invalid' WHERE id=? AND status='pending'", (now_text(),run_id))
            return None
        lease = (datetime.now(timezone.utc)+timedelta(seconds=LEASE_SECONDS)).strftime("%Y-%m-%d %H:%M:%S")
        token = uuid.uuid4().hex
        changed = conn.execute("UPDATE evidence_forensics_runs SET status='analyzing',started_at=?,lease_expires_at=?,claim_token=? WHERE id=? AND status='pending'",
                               (now_text(), lease, token, run_id)).rowcount
        if not changed:
            return None
        return data, row["filename"], row["file_sha256"], row["source_sha256"], token

def _expire_claims(conn):
    stamp = now_text()
    conn.execute("UPDATE evidence_forensics_runs SET status='error',classification='inconclusive',completed_at=?,lease_expires_at=NULL,claim_token=NULL,error_code='worker_lease_expired' WHERE status='analyzing' AND lease_expires_at < ?",
                 (stamp, stamp))

def claim_next(db):
    """Atomically claim the oldest queued row, enforcing one DB-wide worker lease."""
    with _connect(db) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _expire_claims(conn)
        # Recover the crash window between committing an upload and scheduling it.
        # A recorded baseline is required; never invent one for legacy evidence.
        conn.execute("""INSERT INTO evidence_forensics_runs
            (evidence_id,appeal_id,source_sha256,status,created_at)
            SELECT e.id,e.appeal_id,e.file_sha256,'pending',? FROM evidence_files e
            WHERE e.file_sha256 IS NOT NULL AND NOT EXISTS
              (SELECT 1 FROM evidence_forensics_runs r WHERE r.evidence_id=e.id)
            ORDER BY e.id LIMIT 64""", (now_text(),))
        if conn.execute("SELECT 1 FROM evidence_forensics_runs WHERE status='analyzing'").fetchone():
            return None
        row = conn.execute("SELECT r.*,e.file_data,e.filename,e.file_sha256 FROM evidence_forensics_runs r JOIN evidence_files e ON e.id=r.evidence_id WHERE r.status='pending' ORDER BY r.id LIMIT 1").fetchone()
        if not row:
            return None
        run_id = int(row["id"])
        data = bytes(row["file_data"])
        if not data or len(data) > MAX_INPUT_BYTES:
            conn.execute("UPDATE evidence_forensics_runs SET status='error',classification='inconclusive',completed_at=?,error_code='source_size_invalid' WHERE id=? AND status='pending'", (now_text(),run_id))
            return None
        token = uuid.uuid4().hex
        lease = (datetime.now(timezone.utc)+timedelta(seconds=LEASE_SECONDS)).strftime("%Y-%m-%d %H:%M:%S")
        changed = conn.execute("UPDATE evidence_forensics_runs SET status='analyzing',started_at=?,lease_expires_at=?,claim_token=? WHERE id=? AND status='pending'",
                               (now_text(),lease,token,run_id)).rowcount
        return (run_id,(data,row["filename"],row["file_sha256"],row["source_sha256"],token)) if changed else None

def _finish(db, run_id, report, error=None, claim_token=None):
    with _connect(db) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT r.evidence_id,r.source_sha256,e.file_data,e.file_sha256 FROM evidence_forensics_runs r JOIN evidence_files e ON e.id=r.evidence_id WHERE r.id=? AND r.status='analyzing' AND r.claim_token=?", (run_id,claim_token)).fetchone()
        if not row:
            return
        actual = hashlib.sha256(bytes(row["file_data"])).hexdigest()
        expected = actual
        if (row["file_sha256"] and row["file_sha256"] != expected) or row["source_sha256"] != expected:
            report = None
            error = "source_hash_mismatch"
        if error or report is None:
            conn.execute("UPDATE evidence_forensics_runs SET status='error',classification='inconclusive',completed_at=?,lease_expires_at=NULL,claim_token=NULL,error_code=? WHERE id=? AND status='analyzing' AND claim_token=?",
                         (now_text(), (error or "analysis_failed")[:64], run_id, claim_token))
            return
        if report.get("sha256") != actual:
            conn.execute("UPDATE evidence_forensics_runs SET status='error',classification='inconclusive',completed_at=?,lease_expires_at=NULL,claim_token=NULL,error_code='analyzer_hash_mismatch' WHERE id=? AND status='analyzing' AND claim_token=?", (now_text(),run_id,claim_token))
            return
        encoded = {}
        for name in ("localization_png", "reliability_png"):
            value = report.get(name)
            try:
                blob = base64.b64decode(value, validate=True) if value else None
            except Exception:
                blob = None
            encoded[name] = _safe_map_png(blob)
        def js(value, fallback):
            try:
                encoded = json.dumps(value if value is not None else fallback, ensure_ascii=True, separators=(",", ":"))
                return encoded if len(encoded) <= 100000 else json.dumps(fallback)
            except (TypeError, ValueError): return json.dumps(fallback)
        conn.execute("""UPDATE evidence_forensics_runs SET status='complete',classification=?,analyzer_version=?,completed_at=?,lease_expires_at=NULL,claim_token=NULL,
          signals_json=?,metadata_json=?,facts_json=?,provenance_json=?,model_json=?,reliability_json=?,risk=NULL,localization_png=?,reliability_png=?,duration_ms=?,error_code=NULL
          WHERE id=? AND status='analyzing' AND claim_token=?""",
          (report.get("classification") if report.get("classification") in ("inconclusive","review_recommended","no_significant_indicators") else "inconclusive", report.get("analyzer_version",""),now_text(),js(report.get("signals"),[]),js(report.get("metadata"),{}),js(report.get("facts"),{}),js(report.get("provenance"),{}),js(report.get("model"),{}),js(report.get("reliability"),{}),encoded["localization_png"],encoded["reliability_png"],report.get("duration_ms"),run_id,claim_token))

def _safe_map_png(blob):
    if not blob or len(blob) > ARTIFACT_LIMIT:
        return None
    try:
        from PIL import Image
        import io
        with Image.open(io.BytesIO(blob)) as image:
            if image.format != "PNG" or image.width > 1024 or image.height > 1024:
                return None
            image.load()
            if image.width <= 0 or image.height <= 0 or image.width * image.height > 1_048_576:
                return None
            result = io.BytesIO()
            image.convert("L").save(result, format="PNG", optimize=True)
            value = result.getvalue()
            return value if len(value) <= ARTIFACT_LIMIT else None
    except Exception:
        return None

def run_one(db, run_id=None, *, claimed=None):
    # The inherited POSIX lock also survives a coordinator crash until its child
    # exits. A lease alone cannot prevent overlap after suspend/clock changes.
    if os.name == 'posix':
        import fcntl
        lock_path = str(Path(db.db_path).resolve()) + '.forensics.lock'
        with open(lock_path, 'a+b') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                if claimed:
                    with _connect(db) as conn:
                        conn.execute("""UPDATE evidence_forensics_runs SET status='pending',
                            started_at=NULL,lease_expires_at=NULL,claim_token=NULL
                            WHERE id=? AND status='analyzing' AND claim_token=?""", (run_id, claimed[-1]))
                return False
            return _run_one_locked(db, run_id, claimed=claimed, pass_fds=(lock.fileno(),))
    return _run_one_locked(db, run_id, claimed=claimed)

def _run_one_locked(db, run_id=None, *, claimed=None, pass_fds=()):
    claimed = claimed if claimed is not None else _claim(db, run_id)
    if not claimed:
        return False
    data, filename, _file_hash, _source_hash, token = claimed
    with _connect(db) as conn:
        if not conn.execute("SELECT 1 FROM evidence_forensics_runs WHERE id=? AND status='analyzing' AND claim_token=?",
                            (run_id, token)).fetchone():
            return False
    request = json.dumps({"data":base64.b64encode(data).decode("ascii"),"filename":filename}).encode("utf-8")
    try:
        worker_python = os.environ.get("CBVMS_FORENSICS_PYTHON") or sys.executable
        repo_root = str(Path(__file__).resolve().parents[1])
        env={"PATH":os.environ.get("PATH",""),"PYTHONPATH":repo_root,"PYTHONIOENCODING":"utf-8",
             "OMP_NUM_THREADS":"1","OPENBLAS_NUM_THREADS":"1","MKL_NUM_THREADS":"1","NUMEXPR_NUM_THREADS":"1"}
        for key in ("CBVMS_FORENSICS_TRUFOR_ROOT","CBVMS_FORENSICS_TRUFOR_WEIGHTS",
                    "CBVMS_FORENSICS_TRUFOR_SHA256","CBVMS_FORENSICS_ENABLE_EXPERIMENTAL",
                    "CBVMS_FORENSICS_LICENSE_ACCEPTED","CBVMS_FORENSICS_C2PA_TRUST_ANCHORS"):
            if os.environ.get(key) is not None:
                env[key]=os.environ[key]
        timeout = JOB_TIMEOUT_SECONDS if (env.get("CBVMS_FORENSICS_ENABLE_EXPERIMENTAL") == "1"
                 and env.get("CBVMS_FORENSICS_LICENSE_ACCEPTED") == "1") else METADATA_TIMEOUT_SECONDS
        with tempfile.TemporaryFile() as output:
            proc = subprocess.run([worker_python,"-m","core.forensics_worker"],input=request,stdout=output,stderr=subprocess.DEVNULL,
                                  timeout=timeout,check=False,cwd=repo_root,env=env,pass_fds=pass_fds)
            output_size=os.fstat(output.fileno()).st_size
            if proc.returncode or output_size > MAX_WORKER_OUTPUT:
                code = "analysis_timeout" if proc.returncode == 3 else "analysis_failed" if proc.returncode else "analysis_output_too_large"
                _finish(db,run_id,None,code,token)
            else:
                output.seek(0)
                _finish(db,run_id,json.loads(output.read(MAX_WORKER_OUTPUT+1).decode("utf-8")),claim_token=token)
    except subprocess.TimeoutExpired:
        try: _finish(db,run_id,None,"analysis_timeout",token)
        except Exception: pass
    except Exception:
        try: _finish(db,run_id,None,"analysis_failed",token)
        except Exception: pass
    return True

def report_view(conn, run, evidence):
    actual = hashlib.sha256(bytes(evidence["file_data"])).hexdigest() if evidence["file_data"] else None
    valid = bool(actual and evidence["file_sha256"] and run["source_sha256"]
                 and actual == evidence["file_sha256"] == run["source_sha256"])
    view = {"id":run["id"],"evidence_id":run["evidence_id"],"appeal_id":run["appeal_id"],
            "status":run["status"],"classification":run["classification"] if valid else "inconclusive",
            "analyzer_version":run["analyzer_version"],"created_at":run["created_at"],
            "completed_at":run["completed_at"],"sha256":actual,"valid":valid,
            "hash_status":"verified" if valid else ("baseline_unavailable" if not evidence["file_sha256"] else "mismatch"),
            "signals":json.loads(run["signals_json"]) if valid else [],
            "metadata":json.loads(run["metadata_json"]) if valid else {},
            "facts":json.loads(run["facts_json"]) if valid else {},
            "provenance":json.loads(run["provenance_json"]) if valid else {},
            "model":json.loads(run["model_json"]) if valid else {},
            "reliability":json.loads(run["reliability_json"]) if valid else {},
            "model_raw_score":json.loads(run["model_json"]).get("raw_score") if valid else None,
            "model_calibrated":False,"risk":None,"duration_ms":run["duration_ms"],
            "error_code":run["error_code"] if valid else ("baseline_unavailable" if not evidence["file_sha256"] else "source_hash_mismatch")}
    if valid:
        view["localization_png"] = run["localization_png"]
        view["reliability_png"] = run["reliability_png"]
    return view

def get_history(db, evidence_id, username):
    if db.student_session_ref is not None:
        raise PermissionError("An authenticated administrator is required.")
    with _connect(db) as conn:
        conn.execute("BEGIN")
        require_admin(conn,username,db.student_session_ref)
        evidence = conn.execute("SELECT * FROM evidence_files WHERE id=?",(evidence_id,)).fetchone()
        if not evidence: return []
        rows = conn.execute("SELECT * FROM evidence_forensics_runs WHERE evidence_id=? ORDER BY id DESC",(evidence_id,)).fetchall()
        views = [report_view(conn,r,evidence) for r in rows]
        active = any(r["status"] in ("pending","analyzing") for r in rows)
        create_initial = not rows and bool(evidence["file_sha256"])
    if create_initial:
        run_id = create_run(db, evidence_id)
        if run_id is not None:
            return get_history(db, evidence_id, username)
    if autostart_enabled() and active:
        ForensicsCoordinator.for_db(db).wake()
    return views

def get_report(db, run_id, username):
    if db.student_session_ref is not None:
        raise PermissionError("An authenticated administrator is required.")
    with _connect(db) as conn:
        conn.execute("BEGIN")
        require_admin(conn,username,db.student_session_ref)
        row = conn.execute("SELECT * FROM evidence_forensics_runs WHERE id=?",(run_id,)).fetchone()
        if not row: return None
        evidence = conn.execute("SELECT * FROM evidence_files WHERE id=?",(row["evidence_id"],)).fetchone()
        return report_view(conn,row,evidence) if evidence else None

def retry(db, evidence_id, username):
    if db.student_session_ref is not None:
        raise PermissionError("An authenticated administrator is required.")
    with _connect(db) as conn:
        conn.execute("BEGIN IMMEDIATE")
        require_admin(conn,username,db.student_session_ref)
        e = conn.execute("SELECT e.id,e.appeal_id,e.file_data FROM evidence_files e JOIN appeals a ON a.id=e.appeal_id WHERE e.id=? AND a.status='pending'",(evidence_id,)).fetchone()
        if not e: return None
        active=conn.execute("SELECT id FROM evidence_forensics_runs WHERE evidence_id=? AND status IN ('pending','analyzing')",(evidence_id,)).fetchone()
        if active: return int(active[0])
        latest = conn.execute("SELECT created_at FROM evidence_forensics_runs WHERE evidence_id=? ORDER BY id DESC LIMIT 1",(evidence_id,)).fetchone()
        if latest:
            try:
                last = datetime.strptime(latest[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                if (datetime.now(timezone.utc)-last).total_seconds() < 10:
                    return None
            except (TypeError, ValueError):
                return None
        source_hash=hashlib.sha256(bytes(e['file_data'])).hexdigest()
        cursor=conn.execute("INSERT INTO evidence_forensics_runs(evidence_id,appeal_id,source_sha256,status,created_at) VALUES(?,?,?,'pending',?)",
                            (evidence_id,e['appeal_id'],source_hash,now_text()))
        return int(cursor.lastrowid)

class ForensicsCoordinator:
    """Polling coordinator; SQLite owns the bounded queue and global claim lease."""
    _instances = {}
    _lock = threading.Lock()
    def __init__(self, db):
        self.db=db
        self.condition=threading.Condition()
        self.thread=threading.Thread(target=self._loop,daemon=True,name="appeal-evidence-forensics")
        self.thread.start()
    @classmethod
    def for_db(cls,db):
        key=str(db.db_path)
        with cls._lock:
            if key not in cls._instances: cls._instances[key]=cls(db)
            return cls._instances[key]
    def enqueue(self,run_id):
        self.wake()
    def wake(self):
        with self.condition:
            self.condition.notify()
    def _loop(self):
        while True:
            try:
                claimed = claim_next(self.db)
                if claimed:
                    run_id, data = claimed
                    if run_one(self.db,run_id,claimed=data):
                        continue
            except Exception:
                pass
            with self.condition:
                self.condition.wait(timeout=1.0)

def schedule(db,evidence_id):
    try:
        run_id=create_run(db,evidence_id)
        if run_id is not None and autostart_enabled(): ForensicsCoordinator.for_db(db).wake()
        return run_id
    except Exception:
        # Analysis is best effort; evidence submission has already committed.
        return None
