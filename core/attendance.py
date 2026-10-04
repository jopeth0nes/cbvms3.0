"""Campus sightings: immutable qualified facts, explicit Manila dates, safe exports."""
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, date
from zoneinfo import ZoneInfo
import csv
import hashlib
import json

from core.discipline import parse_db_datetime
from core.academics import display_academics

MANILA = ZoneInfo('Asia/Manila')
TITLE = 'Attendance — Campus Sightings'
SNAPSHOT_FIELDS = ('college_department', 'course', 'report_year_level', 'report_section')


def utc_stamp(value):
    instant = parse_db_datetime(value)
    if instant is None:
        raise ValueError('Invalid observation timestamp.')
    return instant.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')


def manila_date(value):
    return parse_db_datetime(value).astimezone(MANILA).date().isoformat()


def local_display(value):
    instant = parse_db_datetime(value)
    return instant.astimezone(MANILA).strftime('%Y-%m-%d %H:%M:%S %Z (UTC+08:00)') if instant else ''


def display_time(row, key):
    if row.get('legacy_summary'):
        return f"{row.get(key, '')} (legacy timezone unverified)"
    return local_display(row.get(key))


@dataclass(frozen=True)
class Sighting:
    student_id: str
    student_name: str
    observed_at: str
    source_id: str
    source_label: str
    session_id: str
    frame_id: str
    monitor_generation: int
    camera_generation: int
    presence_id: str
    student_status: str
    college_department: str
    course: str
    report_year_level: str
    report_section: str
    semester_id: int | None = None
    semester_name: str = ''
    school_year: str = ''
    provenance: str = 'confirmed_live_identity'

    @property
    def key(self):
        return hashlib.sha256(json.dumps([self.student_id, self.session_id, self.frame_id],
            ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()

    def payload(self):
        return asdict(self)


def snapshot_fields(student):
    return display_academics(student)


def validate_filters(filters=None):
    allowed = {'start', 'end', 'semester_id', 'college_department', 'course', 'report_year_level',
               'report_section', 'student_id', 'name', 'source_id', 'search'}
    filters = {k: str(v).strip() for k, v in (filters or {}).items() if v is not None and str(v).strip()}
    if set(filters) - allowed:
        raise ValueError('Unsupported attendance filter.')
    for key in ('start', 'end'):
        if key in filters:
            try:
                if date.fromisoformat(filters[key]).isoformat() != filters[key]:
                    raise ValueError()
            except ValueError:
                raise ValueError('Enter dates as YYYY-MM-DD.') from None
    if filters.get('start') and filters.get('end') and filters['start'] > filters['end']:
        raise ValueError('From date must be on or before To date.')
    if 'semester_id' in filters and not filters['semester_id'].isdigit():
        raise ValueError('Choose a valid semester.')
    return filters


SUMMARY_COLUMNS = ('attendance_date', 'student_id', 'student_name', 'first_seen', 'last_seen',
                   'sighting_count', 'college_department', 'course', 'report_year_level', 'report_section',
                   'semester_name', 'school_year', 'provenance')
EVENT_COLUMNS = ('attendance_date', 'student_id', 'student_name', 'observed_at', 'source_id', 'source_label',
                 'session_id', 'frame_id', 'college_department', 'course', 'report_year_level',
                 'report_section', 'semester_name', 'school_year', 'student_status', 'provenance')


def export_csv(database, path, *, view='summary', filters=None, selected_ids=None):
    """One DB read snapshot; stream every matching row, never just a visible page."""
    filters = validate_filters(filters)
    columns = SUMMARY_COLUMNS if view == 'summary' else EVENT_COLUMNS
    metadata = {'report': TITLE, 'view': view, 'scope': 'selected rows' if selected_ids is not None else 'all matching rows',
                'timezone': 'Asia/Manila (UTC+08:00)', 'filters': filters,
                'generated_at_utc': datetime.now(timezone.utc).isoformat(),
                'meaning': 'Count = accepted cooldown-qualified sightings; not absence, lateness, class attendance, duration or checkout.'}
    def safe(value):
        value = str(value if value is not None else '')
        return "'" + value if value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')) else value
    count = 0
    # Write beside the target and replace only when the complete export succeeds.
    from pathlib import Path
    import tempfile
    import os
    target = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8-sig', newline='',
                dir=target.parent, prefix='.attendance-', delete=False) as output:
            temporary = Path(output.name)
            writer = csv.writer(output)
            writer.writerow(['Report metadata', safe(json.dumps(metadata, ensure_ascii=False, sort_keys=True))])
            writer.writerow([key.replace('_', ' ').title() + (' (Asia/Manila)' if key in ('first_seen','last_seen','observed_at') else '') for key in columns])
            for row in database.iter_attendance(view=view, filters=filters, selected_ids=selected_ids):
                writer.writerow([safe(display_time(row,key) if key in ('first_seen','last_seen','observed_at') else row.get(key)) for key in columns])
                count += 1
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(target)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    return count
