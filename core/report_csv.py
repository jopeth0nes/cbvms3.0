"""Roster exchange and read-only discipline report snapshots."""
import csv
import re

ROSTER = ('student_id', 'name', 'college_department', 'course', 'section', 'year_level')
DISCIPLINE = ROSTER + ('record_type', 'record_id', 'category', 'status', 'date', 'ends_at', 'reason')


def student_fields(student):
    from ui.suspensions_panel import saved_year_level
    raw = student.get('year_and_section') or ''
    match = re.fullmatch(r'\s*[1-4](?:st|nd|rd|th)?\s*(?:YEAR|YR)?\s*[-/,]?\s*([A-Za-z]{1,3}\d*)\s*', raw, re.I)
    return dict(student_id=student['student_id'], name=student['name'],
                college_department=student.get('college_department') or '',
                course=student.get('course') or '',
                section=student.get('report_section') or (match[1].upper() if match else ''),
                year_level=student.get('report_year_level') or saved_year_level(raw) or '')


def read_csv(path, kind):
    columns = ROSTER if kind == 'Roster' else DISCIPLINE
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
            if not clean['student_id'] or not clean['name']:
                raise ValueError(f'Line {line}: student_id and name are required.')
            if clean['year_level']:
                from ui.suspensions_panel import saved_year_level
                year = saved_year_level(clean['year_level'])
                if not year:
                    raise ValueError(f'Line {line}: year_level must be 1st–4th Year.')
                clean['year_level'] = year
            if kind == 'Roster':
                if clean['student_id'] in seen:
                    raise ValueError(f'Line {line}: duplicate student_id.')
                seen.add(clean['student_id'])
            elif clean['record_type'] not in ('Violation', 'Suspension'):
                raise ValueError(f'Line {line}: record_type must be Violation or Suspension.')
            rows.append(clean)
    if not rows:
        raise ValueError('The CSV has no records.')
    return rows


def write_csv(path, kind, rows):
    columns = ROSTER if kind == 'Roster' else DISCIPLINE
    with open(path, 'w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            # Spreadsheet programs must treat these values as text, not formulas.
            writer.writerow({key: "'" + str(row.get(key, '')) if str(row.get(key, '')).startswith(('=', '+', '-', '@', '\t', '\r'))
                             else row.get(key, '') for key in columns})


def import_roster(database, rows):
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
        for record in conn.execute('SELECT * FROM violations ORDER BY timestamp DESC'):
            r = dict(record)
            base = students.get(r['student_id'], dict.fromkeys(ROSTER, ''))
            rows.append(dict(base, student_id=r['student_id'] or '', name=r['student_name'] or base['name'],
                             record_type='Violation', record_id=str(r['id']),
                             category=violation_display_name(r['violation_code']), status=r['status'],
                             date=r['timestamp'], ends_at='', reason=''))
        for record in conn.execute('SELECT * FROM student_suspensions ORDER BY imposed_at DESC'):
            r = dict(record)
            base = students.get(r['student_id'], dict.fromkeys(ROSTER, ''))
            rows.append(dict(base, student_id=r['student_id'], record_type='Suspension',
                             record_id=str(r['id']), category='Suspension', status=suspension_state(r),
                             date=r['starts_at'], ends_at=r['ends_at'] or '', reason=r['reason']))
    return rows
