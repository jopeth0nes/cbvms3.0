"""Local-authoritative calendar/enrollment operations; never imported by cloud clients."""
import hashlib
import json
import pickle
import time
import uuid
from core.academic_calendar import bounds, instant, resolve_term, enrollment_context

ACADEMICS = ('college_department', 'course', 'report_year_level', 'report_section')


def audit(conn, entity, identity, action, actor, reason, before=None, after=None):
    conn.execute('''INSERT INTO academic_audit(entity,entity_id,action,actor,reason,before_json,after_json,created_at)
        VALUES(?,?,?,?,?,?,?,?)''', (entity,str(identity),action,actor,reason,
            json.dumps(before,sort_keys=True) if before is not None else None,
            json.dumps(after,sort_keys=True) if after is not None else None,instant().isoformat()))


class TermEnrollmentStore:
    def _term_admin(self, conn, actor):
        if self.student_session_ref is not None or not conn.execute(
                "SELECT 1 FROM users WHERE username=? AND role='superadmin'", (actor,)).fetchone():
            raise PermissionError('Only a superadmin may manage calendar and enrollment records.')

    def term_date_impact(self, term_id, start_date, end_date):
        start, end = bounds(start_date,end_date)
        with self.connect() as conn:
            old = conn.execute('SELECT * FROM academic_terms WHERE id=?',(term_id,)).fetchone()
            result = dict(term=dict(old) if old else None, start_date=start_date,end_date=end_date, affected={})
            for table, column in (('violations','timestamp'),('attendance_events','observed_at'),('security_events','observed_at')):
                rows = conn.execute(f'''SELECT id,semester_id,{column} FROM {table} WHERE semester_id=?
                    OR (julianday({column})>=julianday(?) AND julianday({column})<julianday(?))''',
                    (term_id,start,end)).fetchall()
                result['affected'][table] = [dict(r) for r in rows]
            result['token'] = hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest()
            return result

    def save_academic_term(self, *, actor, semester_code, school_year, start_date, end_date,
                           publish=False, term_id=None, reason='', preview_token=None):
        names = {'semester_1':'First Semester','semester_2':'Second Semester','summer':'Summer'}
        if semester_code not in names or not school_year.strip():
            raise ValueError('Choose first semester, second semester or summer and an academic year.')
        start,end = bounds(start_date,end_date)
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            self._term_admin(conn,actor)
            old = conn.execute('SELECT * FROM academic_terms WHERE id=?',(term_id,)).fetchone() if term_id else None
            if term_id and not old:
                raise ValueError('Unknown term.')
            if old and old['publication_state']=='published':
                publish=True
                if (old['start_date'],old['end_date']) != (start_date,end_date):
                    # A second read is safe under our reserved writer lock; reject stale previews.
                    impact = self.term_date_impact(term_id,start_date,end_date)
                    if not reason.strip() or preview_token != impact['token']:
                        raise ValueError('Date corrections require a fresh impact preview and audit reason.')
            values = dict(semester_code=semester_code,semester_name=names[semester_code],school_year=school_year.strip(),
                          start_date=start_date,end_date=end_date,starts_at=start,ends_before=end,
                          publication_state='published' if publish else 'draft')
            if old:
                conn.execute('UPDATE academic_terms SET '+','.join(k+'=?' for k in values)+
                    ',revision=revision+1,updated_at=CURRENT_TIMESTAMP WHERE id=?',(*values.values(),term_id))
            else:
                cur = conn.execute('INSERT INTO academic_terms('+','.join(values)+') VALUES('+','.join('?' for _ in values)+')',tuple(values.values()))
                term_id=cur.lastrowid
            if publish and (not old or old['publication_state']!='published'):
                conn.execute('UPDATE academic_terms SET published_at=?,published_by=? WHERE id=?',(instant().isoformat(),actor,term_id))
            if publish:
                for table,column in (('violations','timestamp'),('attendance_events','observed_at'),('security_events','observed_at')):
                    for record in conn.execute(f'''SELECT id,semester_id,{column} FROM {table} WHERE
                        (semester_id=? AND (julianday({column}) IS NULL OR julianday({column})<julianday(?) OR julianday({column})>=julianday(?)))
                        OR (semester_id IS NOT ? AND julianday({column})>=julianday(?) AND julianday({column})<julianday(?))''',
                        (term_id,start,end,term_id,start,end)):
                        self._term_exception_conn(conn,table,record['id'],'Existing assignment conflicts with published dates; original term retained')
            audit(conn,'term',term_id,'publish' if publish else 'save_draft',actor,reason,dict(old) if old else None,values)
            return term_id

    def enrollment_context(self, student_id, *, occurred_at=None):
        with self.connect() as conn:
            return enrollment_context(conn,student_id,occurred_at)

    def save_enrollment_draft(self, student_id, semester_id, academics, *, actor):
        values = {k:str(academics.get(k,'')).strip() for k in ACADEMICS}
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            self._term_admin(conn,actor)
            old = conn.execute('SELECT * FROM student_term_enrollments WHERE student_id=? AND semester_id=?',(student_id,semester_id)).fetchone()
            if old and old['status']!='draft':
                raise ValueError('Use an audited correction for an existing confirmed enrollment.')
            conn.execute('''INSERT INTO student_term_enrollments(student_id,semester_id,college_department,course,
                report_year_level,report_section,status) VALUES(?,?,?,?,?,?,'draft')
                ON CONFLICT(student_id,semester_id) DO UPDATE SET college_department=excluded.college_department,
                course=excluded.course,report_year_level=excluded.report_year_level,report_section=excluded.report_section''',
                (student_id,semester_id,*values.values()))
            audit(conn,'enrollment',f'{student_id}:{semester_id}','draft',actor,'Demographics only',dict(old) if old else None,values)

    @staticmethod
    def _registration_capture_payload(captures, student_key):
        """Apply the same fresh, three-angle identity checks to both save paths."""
        import numpy as np
        from core.face_capture import FaceCapture, capture_payload
        captures=tuple(captures)
        if len(captures)!=3 or any(not isinstance(c,FaceCapture) or not 0 <= time.monotonic()-c.captured_at <= 600 for c in captures):
            raise ValueError('Three fresh camera captures are required; reopen the camera to retry.')
        if len({str(c.frame_id) for c in captures})!=3:
            raise ValueError('Each capture must come from a distinct camera frame.')
        blob,photo=capture_payload(captures,student_key)
        candidate=np.vstack([c.embedding for c in captures])
        if np.min(candidate @ candidate.T) < .55:
            raise ValueError('Capture identities are inconsistent; retake all angles.')
        return blob,photo,candidate

    @staticmethod
    def _check_capture_identity(conn, student_id, candidate):
        import numpy as np
        from core.recognizer import MATCH_THRESHOLD
        found=False
        # These encodings are trusted local storage, never uploaded pickle data.
        for student in conn.execute('SELECT student_id,encoding FROM students'):
            if student['student_id']==student_id:
                found=True
            if not student['encoding']:
                continue
            stored=np.asarray(pickle.loads(student['encoding']),dtype=np.float32).reshape(-1,candidate.shape[1])
            stored=stored/np.linalg.norm(stored,axis=1,keepdims=True)
            distance=1-float(np.max(candidate @ stored.T))
            if student['student_id']!=student_id and distance<MATCH_THRESHOLD:
                raise ValueError('Face matches another permanent student identity. Resolve the identity before enrolling.')
            if student['student_id']==student_id and distance>=MATCH_THRESHOLD:
                raise ValueError('Fresh capture does not match this permanent student identity.')
        return found

    def register_student_with_capture(self, student, captures, student_key, *, actor):
        """Save an account and recognition anchor without confirming term eligibility."""
        captures=tuple(captures)
        blob,photo,candidate=self._registration_capture_payload(captures,student_key)
        values=dict(student)
        student_id=str(values.get('student_id','')).strip()
        if not student_id or not str(values.get('name','')).strip() or not values.get('account_password'):
            raise ValueError('Student ID, name and account password are required.')
        values.update(student_id=student_id,encoding=blob,photo=photo,registration_pending=False)
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            self._term_admin(conn,actor)
            if conn.execute('SELECT 1 FROM students WHERE student_id=?',(student_id,)).fetchone():
                raise ValueError('Student already registered. Select a published term to confirm enrollment, or edit the existing student.')
            self._check_capture_identity(conn,student_id,candidate)
            pk=self.insert_student(**values,_connection=conn)
            audit(conn,'student',student_id,'register',actor,'Face registration; academic term not assigned',
                  after={'term_assigned':False,'capture_hashes':[hashlib.sha256(c.photo).hexdigest() for c in captures]})
            return pk

    def confirm_term_enrollment(self, student_id, semester_id, academics, captures, student_key, *, actor, new_student=None):
        """Confirm a published term with fresh camera provenance and identity checks."""
        captures=tuple(captures)
        blob,photo,candidate=self._registration_capture_payload(captures,student_key)
        values={k:str(academics.get(k,'')).strip() for k in ACADEMICS}
        from core.academics import academic_values
        normalized=academic_values(values['college_department'],values['course'],values['report_year_level'],values['report_section'])
        values={k:normalized[k] for k in ACADEMICS}
        if not all(values.values()):
            raise ValueError('Complete the term department, course, year and section.')
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            self._term_admin(conn,actor)
            if new_student is not None:
                if new_student.get('student_id')!=student_id:
                    raise ValueError('Permanent identity does not match enrollment.')
                self.insert_student(**new_student,_connection=conn)
            term=conn.execute("SELECT * FROM academic_terms WHERE id=? AND publication_state='published'",(semester_id,)).fetchone()
            if not term or instant(term['ends_before'])<=instant():
                raise ValueError('Select a published current or upcoming term.')
            old=conn.execute('SELECT * FROM student_term_enrollments WHERE student_id=? AND semester_id=?',(student_id,semester_id)).fetchone()
            if old and old['status']=='confirmed':
                raise ValueError('This student already has a verified enrollment for this term.')
            if not self._check_capture_identity(conn,student_id,candidate):
                raise ValueError('Create the permanent student draft first.')
            capture_id=uuid.uuid4().hex
            stamp=instant().isoformat()
            from datetime import timedelta
            capture_times=[(instant(stamp)-timedelta(seconds=time.monotonic()-c.captured_at)).isoformat() for c in captures]
            provenance=json.dumps({'captured_at_utc':capture_times,'time_basis':'monotonic frame age mapped to confirmation UTC','source':'guided_live_camera','frames':[list(c.frame_id) for c in captures],
                'boxes':[list(c.box) for c in captures], 'liveness_verified':False,
                'capture_hashes':[hashlib.sha256(c.photo).hexdigest() for c in captures]})
            # Prevent reuse of any angle from earlier enrollments, not only the main photo.
            hashes={hashlib.sha256(c.photo).hexdigest() for c in captures}
            for row in conn.execute('SELECT provenance FROM enrollment_captures'):
                if hashes.intersection(json.loads(row[0])['capture_hashes']):
                    raise ValueError('A prior capture cannot confirm a new enrollment.')
            conn.execute('''INSERT INTO enrollment_captures(id,student_id,semester_id,encoding,photo,photo_sha256,
                provenance,captured_at,actor) VALUES(?,?,?,?,?,?,?,?,?)''',
                (capture_id,student_id,semester_id,blob,photo,hashlib.sha256(photo).hexdigest(),provenance,min(capture_times),actor))
            conn.execute('''INSERT INTO student_term_enrollments(student_id,semester_id,college_department,course,
                report_year_level,report_section,status,capture_id,confirmed_at,confirmed_by)
                VALUES(?,?,?,?,?,?,'confirmed',?,?,?) ON CONFLICT(student_id,semester_id) DO UPDATE SET
                college_department=excluded.college_department,course=excluded.course,
                report_year_level=excluded.report_year_level,report_section=excluded.report_section,
                status=excluded.status,capture_id=excluded.capture_id,confirmed_at=excluded.confirmed_at,confirmed_by=excluded.confirmed_by''',
                (student_id,semester_id,*values.values(),capture_id,stamp,actor))
            saved=conn.execute('SELECT * FROM student_term_enrollments WHERE student_id=? AND semester_id=?',(student_id,semester_id)).fetchone()
            conn.execute('INSERT INTO enrollment_revisions(enrollment_id,student_id,semester_id,effective_at,snapshot) VALUES(?,?,?,?,?)',
                (saved['id'],student_id,semester_id,stamp,json.dumps(dict(saved))))
            # Preserve the permanent recognition anchor; a future capture cannot revoke current recognition.
            conn.execute('UPDATE students SET encoding=COALESCE(encoding,?),photo=COALESCE(photo,?),registration_pending=0 WHERE student_id=?',(blob,photo,student_id))
            audit(conn,'enrollment',f'{student_id}:{semester_id}','confirm',actor,'Fresh three-angle camera capture',
                  dict(old) if old else None,dict(values,capture_id=capture_id,confirmed_at=stamp))
            return capture_id

    def correct_enrollment(self, enrollment_id, *, actor, reason, academics=None, status=None):
        if not reason.strip() or status not in (None,'withdrawn'):
            raise ValueError('Give a correction reason; confirmation always requires a new capture workflow.')
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            self._term_admin(conn,actor)
            old=conn.execute('SELECT * FROM student_term_enrollments WHERE id=?',(enrollment_id,)).fetchone()
            if not old:
                raise ValueError('Enrollment not found.')
            values={k:str(academics[k]).strip() for k in ACADEMICS if academics and k in academics}
            if values:
                from core.academics import academic_values
                merged=dict(old);merged.update(values)
                normalized=academic_values(*(merged[k] for k in ACADEMICS))
                values={k:normalized[k] for k in ACADEMICS}
            if status:
                values['status']=status
            if not values or any(not v for v in values.values()):
                raise ValueError('Complete correction values are required.')
            conn.execute('UPDATE student_term_enrollments SET '+','.join(k+'=?' for k in values)+' WHERE id=?',(*values.values(),enrollment_id))
            saved=conn.execute('SELECT * FROM student_term_enrollments WHERE id=?',(enrollment_id,)).fetchone()
            conn.execute('INSERT INTO enrollment_revisions(enrollment_id,student_id,semester_id,effective_at,snapshot) VALUES(?,?,?,?,?)',
                (saved['id'],saved['student_id'],saved['semester_id'],instant().isoformat(),json.dumps(dict(saved))))
            audit(conn,'enrollment',enrollment_id,'correction',actor,reason,dict(old),values)

    def record_enrollment_review(self, student_id, *, observed_at, source_id, snapshot, valid_if=None):
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if valid_if and not valid_if():
                return None
            context=enrollment_context(conn,student_id,observed_at)
            if context['eligible']:
                return None
            stamp=instant(observed_at).isoformat()
            term=context['term'].get('id')
            if not conn.execute('INSERT OR IGNORE INTO enrollment_review_receipts VALUES(?,?,?)',(student_id,source_id,stamp)).rowcount:
                return None
            cooldown=conn.execute('SELECT alarm_cooldown FROM enrollment_settings WHERE id=1').fetchone()[0]
            recent=conn.execute('''SELECT last_seen FROM enrollment_reviews WHERE student_id=? AND semester_id IS ?
                AND enrollment_status=? AND source_id=? ORDER BY last_seen DESC LIMIT 1''',
                (student_id,term,context['status'],source_id)).fetchone()
            if recent and abs((instant(stamp)-instant(recent['last_seen'])).total_seconds())<cooldown:
                return None
            last=conn.execute('''SELECT * FROM enrollment_reviews WHERE student_id=? AND semester_id IS ?
                AND enrollment_status=? AND source_id=? AND resolved_at IS NULL''',
                (student_id,term,context['status'],source_id)).fetchone()
            if last:
                if abs((instant(stamp)-instant(last['last_seen'])).total_seconds())<cooldown:
                    return None
                conn.execute('UPDATE enrollment_reviews SET last_seen=MAX(last_seen,?),occurrences=occurrences+1 WHERE id=?',(stamp,last['id']))
                result=last['id']
            else:
                result=conn.execute('''INSERT INTO enrollment_reviews(student_id,semester_id,enrollment_status,
                    observed_at,last_seen,source_id,snapshot,snapshot_sha256) VALUES(?,?,?,?,?,?,?,?)''',
                    (student_id,term,context['status'],stamp,stamp,source_id,snapshot,
                     hashlib.sha256(snapshot).hexdigest() if snapshot else None)).lastrowid
            conn.execute('''INSERT INTO enrollment_review_events(review_id,observed_at,snapshot,snapshot_sha256)
                VALUES(?,?,?,?)''',(result,stamp,snapshot,hashlib.sha256(snapshot).hexdigest() if snapshot else None))
            if valid_if and not valid_if():
                conn.rollback()
                return None
            return result

    def resolve_enrollment_review(self, review_id, *, actor, reason):
        if not reason.strip():
            raise ValueError('A resolution reason is required; dismissal does not enroll the student.')
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            self._term_admin(conn,actor)
            row=conn.execute('SELECT * FROM enrollment_reviews WHERE id=?',(review_id,)).fetchone()
            if not row or row['resolved_at']:
                return False
            conn.execute('UPDATE enrollment_reviews SET resolved_at=?,resolved_by=?,resolution_reason=? WHERE id=?',(instant().isoformat(),actor,reason,review_id))
            audit(conn,'review',review_id,'resolve',actor,reason)
            return True

    def set_enrollment_alarm_cooldown(self, seconds, *, actor):
        if type(seconds) is not int or not 1<=seconds<=86400:
            raise ValueError('Cooldown must be 1–86400 seconds.')
        with self.connect() as conn:
            self._term_admin(conn,actor)
            conn.execute('UPDATE enrollment_settings SET alarm_cooldown=? WHERE id=1',(seconds,))
            audit(conn,'settings',1,'alarm_cooldown',actor,'Enrollment alarm cooldown',after=seconds)

    def reconcile_term_boundaries(self, *, now=None):
        stamp=instant(now)
        with self.connect() as conn:
            term=resolve_term(conn,stamp)
            current=[r[0] for r in conn.execute('SELECT id FROM academic_terms WHERE is_current=1')]
            pending=conn.execute("""SELECT 1 FROM academic_terms t WHERE publication_state='published' AND (
                (julianday(starts_at)<=julianday(?) AND NOT EXISTS(SELECT 1 FROM academic_boundary_receipts r
                    WHERE r.semester_id=t.id AND r.kind='start' AND r.boundary=t.starts_at))
                OR (julianday(ends_before)<=julianday(?) AND NOT EXISTS(SELECT 1 FROM academic_boundary_receipts r
                    WHERE r.semester_id=t.id AND r.kind='end' AND r.boundary=t.ends_before))) LIMIT 1""",
                (stamp.isoformat(),stamp.isoformat())).fetchone()
            suspension=conn.execute("""SELECT 1 FROM student_suspensions s JOIN academic_terms t
                ON t.publication_state='published' AND julianday(t.starts_at)>julianday(s.starts_at)
                AND julianday(t.starts_at)<=julianday(?) AND (s.ends_at IS NULL OR julianday(t.starts_at)<julianday(s.ends_at))
                WHERE s.effective_ended_at IS NULL AND s.lifted_at IS NULL LIMIT 1""",(stamp.isoformat(),)).fetchone()
            if not pending and not suspension and current==([term['id']] if term else []):
                return term
            conn.execute('BEGIN IMMEDIATE')
            term=resolve_term(conn,stamp)
            for configured in conn.execute("SELECT * FROM academic_terms WHERE publication_state='published'"):
                for kind,column in (('start','starts_at'),('end','ends_before')):
                    if instant(configured[column])<=stamp:
                        inserted=conn.execute('INSERT OR IGNORE INTO academic_boundary_receipts VALUES(?,?,?)',
                            (configured['id'],kind,configured[column])).rowcount
                        if inserted:
                            audit(conn,'calendar',configured['id'],'boundary_observed','system','Idempotent calendar recovery',
                                  after={'kind':kind,'occurred_at':configured[column]})
            current=conn.execute('SELECT id FROM academic_terms WHERE is_current=1').fetchone()
            if (current[0] if current else None)!=term.get('id'):
                conn.execute('UPDATE academic_terms SET is_current=0 WHERE is_current=1')
                if term:
                    conn.execute('UPDATE academic_terms SET is_current=1 WHERE id=?',(term['id'],))
                audit(conn,'calendar','campus','automatic_transition','system','Calendar recovery',
                      current[0] if current else None,term.get('id'))
            for row in conn.execute('SELECT * FROM student_suspensions WHERE effective_ended_at IS NULL AND lifted_at IS NULL').fetchall():
                next_term=conn.execute("""SELECT starts_at FROM academic_terms WHERE publication_state='published'
                    AND julianday(starts_at)>julianday(?) AND julianday(starts_at)<=julianday(?) ORDER BY starts_at LIMIT 1""",
                    (row['starts_at'],stamp.isoformat())).fetchone()
                if next_term and (not row['ends_at'] or instant(next_term[0])<instant(row['ends_at'])):
                    conn.execute("UPDATE student_suspensions SET effective_ended_at=?,effective_end_reason='Term rollover' WHERE id=?",(next_term[0],row['id']))
                    audit(conn,'suspension',row['id'],'effective_termination','system','Term rollover',after={'effective_ended_at':next_term[0]})
            return term

    def term_history(self, student_id):
        with self.connect() as conn:
            if self.student_session_ref and self._session_row(conn,self.student_session_ref['token'])['student_id']!=student_id:
                raise PermissionError('Student history belongs to another account.')
            enrollments=[dict(r) for r in conn.execute('''SELECT e.id,e.semester_id,e.college_department,e.course,
                e.report_year_level,e.report_section,e.status,e.confirmed_at,t.semester_name,t.school_year
                FROM student_term_enrollments e JOIN academic_terms t ON t.id=e.semester_id WHERE student_id=?
                ORDER BY t.starts_at DESC''',(student_id,))]
            totals=[dict(r) for r in conn.execute('''SELECT semester_id,COUNT(*) issued,SUM(is_active) valid
                FROM strikes WHERE student_id=? GROUP BY semester_id''',(student_id,))]
            reviews=[dict(r) for r in conn.execute('''SELECT id,semester_id,enrollment_status,observed_at,last_seen,
                resolved_at,resolution_reason FROM enrollment_reviews WHERE student_id=? ORDER BY observed_at DESC''',(student_id,))]
            return dict(enrollments=enrollments,strikes_by_term=totals,lifetime_issued=sum(r['issued'] for r in totals),
                        lifetime_valid=sum(r['valid'] for r in totals),enrollment_reviews=reviews)

    def recognition_capture_encodings(self):
        """Local-only: newest verified capture per identity, alongside the permanent anchor."""
        self._attendance_staff_only()
        with self.connect() as conn:
            return {r['student_id']:r['encoding'] for r in conn.execute('''SELECT c.student_id,c.encoding
                FROM enrollment_captures c WHERE c.rowid=(SELECT c2.rowid FROM enrollment_captures c2
                WHERE c2.student_id=c.student_id ORDER BY c2.captured_at DESC,c2.rowid DESC LIMIT 1)''')}

    def query_term_roster(self, *, semester_id=None, filters=None):
        self._attendance_staff_only()
        filters=filters or {}
        allowed=set(ACADEMICS)|{'school_year','search'}
        if set(filters)-allowed:
            raise ValueError('Unsupported roster filter.')
        join=' AND e.semester_id=?' if semester_id is not None else ''
        args=[semester_id] if semester_id is not None else []
        clauses=[]
        for key,value in filters.items():
            if not value or value=='All':continue
            if key=='search':
                clauses.append('(instr(lower(s.student_id),lower(?))>0 OR instr(lower(s.name),lower(?))>0)');args.extend([value,value])
            else:
                clauses.append(('t.' if key=='school_year' else 'e.')+key+'=?');args.append(value)
        with self.connect() as conn:
            rows=conn.execute('''SELECT s.student_id,s.name,e.id enrollment_id,e.semester_id,
                COALESCE(t.school_year,'') school_year,COALESCE(t.semester_name,'Legacy / Unassigned') semester_name,
                COALESCE(e.college_department,'') college_department,COALESCE(e.course,'') course,
                COALESCE(e.report_year_level,'') report_year_level,COALESCE(e.report_section,'') report_section,
                COALESCE(e.status,'unverified') status,e.confirmed_at,
                CASE WHEN e.capture_id IS NULL THEN 'Missing fresh capture' ELSE 'Capture recorded' END capture
                FROM students s LEFT JOIN student_term_enrollments e ON e.student_id=s.student_id'''+join+
                ' LEFT JOIN academic_terms t ON t.id=e.semester_id'+(' WHERE '+' AND '.join(clauses) if clauses else '')+
                ' ORDER BY s.student_id,t.starts_at',args).fetchall()
            return [dict(r) for r in rows]


    @staticmethod
    def _term_exception_conn(conn,table,record_id,reason='Outside published academic dates'):
        conn.execute('INSERT OR IGNORE INTO term_assignment_reviews(source_table,record_id,reason,created_at) VALUES(?,?,?,?)',
            (table,record_id,reason,instant().isoformat()))

    def resolve_term_assignment_review(self,review_id,*,actor,reason):
        if not reason.strip():raise ValueError('Explain the review decision; existing record ownership is preserved.')
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE');self._term_admin(conn,actor)
            changed=conn.execute('UPDATE term_assignment_reviews SET resolved_at=?,resolved_by=?,resolution_reason=? WHERE id=? AND resolved_at IS NULL',
                (instant().isoformat(),actor,reason,review_id)).rowcount
            if changed:audit(conn,'term_exception',review_id,'resolve',actor,reason)
            return bool(changed)
