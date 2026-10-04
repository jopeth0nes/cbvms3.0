"""Roster exchange and read-only discipline report snapshots."""
import csv
from core.appeal_categories import category_display
from core.academics import academic_values, display_academics, normalize_year, validate_pair, NEEDS_REVIEW

ROSTER = ('student_id', 'name', 'college_department', 'course', 'section', 'year_level')
DISCIPLINE_BASE = ROSTER + ('record_type', 'record_id', 'category', 'status', 'date', 'ends_at', 'reason')
DECISION_COLUMNS = ('appeal_status','decision_category_code','decision_category_label','decision_category_version',
                    'decision_reason','decided_by','decided_at')
DISCIPLINE = DISCIPLINE_BASE + DECISION_COLUMNS


def student_fields(student):
    values = display_academics(student)
    return dict(student_id=student['student_id'], name=student['name'],
                college_department=values['college_department'], course=values['course'],
                section=values['report_section'], year_level=values['report_year_level'])


def validate_roster(rows):
    validated, seen = [], set()
    for line, row in enumerate(rows, 2):
        try:
            clean = {key: str(row.get(key, '')).strip() for key in ROSTER}
            if not clean['student_id'] or not clean['name']:
                raise ValueError('student_id and name are required.')
            if clean['student_id'] in seen:
                raise ValueError('duplicate student_id.')
            seen.add(clean['student_id'])
            academics = academic_values(clean['college_department'], clean['course'], clean['year_level'], clean['section'])
            clean.update(college_department=academics['college_department'], course=academics['course'],
                         year_level=academics['report_year_level'], section=academics['report_section'])
            validated.append(clean)
        except ValueError as exc:
            raise ValueError(f'Line {line}: {exc}') from exc
    return validated


def read_csv(path, kind):
    columns = ROSTER if kind == 'Roster' else DISCIPLINE_BASE
    from pathlib import Path
    if Path(path).stat().st_size > 20 * 1024 * 1024:
        raise ValueError('CSV exceeds the 20 MB import limit.')
    with open(path, newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError('CSV headers are missing or duplicated.')
        missing = set(columns) - set(reader.fieldnames)
        if missing:
            raise ValueError('Missing CSV columns: ' + ', '.join(sorted(missing)))
        rows, seen = [], set()
        for line, row in enumerate(reader, 2):
            if None in row or any(row.get(key) is None for key in columns):
                raise ValueError(f'Line {line}: column count does not match the header.')
            clean = {key: row[key].strip() for key in columns}
            if kind != 'Roster':
                clean.update({key:(row.get(key) or '').strip() for key in DECISION_COLUMNS})
            for key, value in clean.items():
                if len(value) > 1 and value[0] == "'" and value[1] in "'=+-@\t\r":
                    clean[key] = value[1:]
            if not clean['student_id'] or not clean['name']:
                raise ValueError(f'Line {line}: student_id and name are required.')
            if kind != 'Roster' and (clean['college_department'] or clean['course']) and clean['course'] != NEEDS_REVIEW:
                try:
                    clean['college_department'], clean['course'] = validate_pair(clean['college_department'], clean['course'])
                except ValueError as exc:
                    raise ValueError(f'Line {line}: {exc}') from exc
            if kind != 'Roster' and clean['year_level']:
                try:
                    clean['year_level'] = normalize_year(clean['year_level'])
                except ValueError as exc:
                    raise ValueError(f'Line {line}: {exc}') from exc
            if kind == 'Roster':
                if clean['student_id'] in seen:
                    raise ValueError(f'Line {line}: duplicate student_id.')
                seen.add(clean['student_id'])
            elif clean['record_type'] not in ('Violation', 'Suspension'):
                raise ValueError(f'Line {line}: record_type must be Violation or Suspension.')
            if len(rows) >= 50000:
                raise ValueError('CSV exceeds the 50,000 row import limit.')
            rows.append(clean)
    if not rows:
        raise ValueError('The CSV has no records.')
    return validate_roster(rows) if kind == 'Roster' else rows


def write_csv(path, kind, rows):
    columns = ROSTER if kind == 'Roster' else DISCIPLINE
    with open(path, 'w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            # Spreadsheet programs must treat these values as text, not formulas.
            writer.writerow({key: "'" + str(row.get(key, '')) if str(row.get(key, '')).startswith(("'", '=', '+', '-', '@', '\t', '\r'))
                             else row.get(key, '') for key in columns})


def import_roster(database, rows):
    rows = validate_roster(rows)  # Validate every row before acquiring a write transaction.
    with database.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        for row in rows:
            combined = ' - '.join(v for v in (row['year_level'], row['section']) if v)
            conn.execute('''INSERT INTO students
                (student_id,name,course,year_and_section,college_department,report_section,report_year_level)
                VALUES (?,?,?,?,?,?,?) ON CONFLICT(student_id) DO UPDATE SET
                name=excluded.name, course=excluded.course, year_and_section=excluded.year_and_section,
                college_department=excluded.college_department, report_section=excluded.report_section,
                report_year_level=excluded.report_year_level''',
                (row['student_id'], row['name'], row['course'], combined,
                 row['college_department'], row['section'], row['year_level']))


def live_rows(database, kind):
    from core.discipline import violation_display_name
    from ui.suspensions_panel import suspension_state
    students = {s['student_id']: student_fields(dict(s)) for s in database.get_all_students()}
    if kind == 'Roster':
        return list(students.values())
    rows = []
    with database.connect() as conn:
        for record in conn.execute('''SELECT v.*,a.status AS appeal_status,a.decision_category_code,
                a.decision_category_label,a.decision_category_version,a.admin_notes AS decision_reason,a.decided_by,a.decided_at
                FROM violations v LEFT JOIN appeals a ON a.violation_id=v.id ORDER BY v.timestamp DESC'''):
            r = dict(record)
            base = students.get(r['student_id'], dict.fromkeys(ROSTER, ''))
            rows.append(dict(base, student_id=r['student_id'] or '', name=r['student_name'] or base['name'],
                             record_type='Violation', record_id=str(r['id']),
                             category=violation_display_name(r['violation_code']), status=r['status'],
                             date=r['timestamp'], ends_at='', reason='',
                             **{key: (category_display(r) if r.get('appeal_status') in ('approved','rejected') else '')
                                if key=='decision_category_label' else r.get(key) or '' for key in DECISION_COLUMNS}))
        for record in conn.execute('SELECT * FROM student_suspensions ORDER BY imposed_at DESC'):
            r = dict(record)
            base = students.get(r['student_id'], dict.fromkeys(ROSTER, ''))
            rows.append(dict(base, student_id=r['student_id'], record_type='Suspension',
                             record_id=str(r['id']), category='Suspension', status=suspension_state(r),
                             date=r['starts_at'], ends_at=r['ends_at'] or '', reason=r['reason']))
    return rows
