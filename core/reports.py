"""Portable CSV downloads and printable HTML reports (no extra dependencies)."""

import csv
from datetime import datetime
from html import escape
from pathlib import Path
import tempfile
import webbrowser

from core.appeal_categories import category_display
from core.academics import display_academics
from core.attendance import display_time
from core.discipline import parse_db_datetime, violation_display_name
from zoneinfo import ZoneInfo


ATTENDANCE_HEADERS = ["Date", "Student ID", "Student Name", "Course",
                      "Year & Section", "First Seen (Asia/Manila)", "Last Seen (Asia/Manila)", "Qualified Sightings"]
VIOLATION_HEADERS = ["Record ID", "Student ID", "Student Name", "Course",
                     "Year & Section", "Violation", "Date & Time", "Status",
                     "Semester", "School Year", "Appeal", "Decision Category",
                     "Administrator Decision Reason", "Decided By", "Decision Time"]


def local_timestamp(value):
    parsed = parse_db_datetime(value)
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S") if parsed else ""


def attendance_values(rows):
    return [[r["attendance_date"], r["student_id"], r["student_name"],
             r.get("course", ""), " ".join(filter(None, (r.get("report_year_level"), r.get("report_section")))),
             display_time(r,"first_seen"), display_time(r,"last_seen"),
             f"{r.get('sighting_count', 0)} new (legacy total unknown)" if r.get("legacy_summary") else r.get("sighting_count", 0)] for r in rows]


def violation_values(rows):
    return [[r["id"], r.get("student_id") or "", r.get("student_name") or "Unknown",
             display_academics(r)["course"], r.get("year_and_section") or "",
             violation_display_name(r.get("violation_code"), r.get("violation_type")),
             local_timestamp(r.get("timestamp")),
             (r.get("status") or "unreviewed").replace("_", " ").title(),
             r.get("semester_name") or "", r.get("school_year") or "",
             (r.get("appeal_status") or "None").title(),
             category_display(r) if r.get("appeal_status") in ("approved","rejected") else "",
             r.get("decision_reason") or "",r.get("decided_by") or "",local_timestamp(r.get("decided_at"))] for r in rows]


def write_csv(path, headers, rows):
    # Prevent user-entered names/IDs from becoming spreadsheet formulas.
    def safe(value):
        value = str(value if value is not None else "")
        return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value
    with open(path, "w", newline="", encoding="utf-8-sig") as output:
        writer = csv.writer(output)
        writer.writerow(headers)
        writer.writerows([safe(v) for v in row] for row in rows)


def report_html(title, headers, rows, description=""):
    def cell(value):
        return escape(str(value if value is not None else ""))
    headings = "".join(f"<th>{cell(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell(v)}</td>" for v in row) + "</tr>"
                   for row in rows)
    if not rows:
        body = f'<tr><td colspan="{len(headers)}">No records match these filters.</td></tr>'
    generated = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    return f'''<!doctype html><html><head><meta charset="utf-8">
<title>{cell(title)}</title><style>
body {{font:14px Arial,sans-serif;color:#172033;margin:28px}}
h1 {{font-size:24px}} table {{width:100%;border-collapse:collapse;font-size:11px}}
th,td {{border:1px solid #cbd5e1;padding:7px;text-align:left;overflow-wrap:anywhere}}
th {{background:#edf2f7}} thead {{display:table-header-group}}
tr {{break-inside:avoid}} button {{padding:10px 18px;margin-bottom:18px}}
@page {{size:landscape;margin:12mm}} @media print {{button {{display:none}} body {{margin:0}}}}
</style></head><body><button onclick="window.print()">Print / Save as PDF</button>
<h1>CBVMS — {cell(title)}</h1><p>{cell(description)}</p>
<p>Generated: {cell(generated)} · Records: {len(rows)} · Times shown in local time</p>
<table><thead><tr>{headings}</tr></thead><tbody>{body}</tbody></table></body></html>'''


def open_print_preview(title, headers, rows, description=""):
    with tempfile.NamedTemporaryFile(suffix=".html", prefix="cbvms_report_",
                                     delete=False, mode="w", encoding="utf-8") as output:
        output.write(report_html(title, headers, rows, description))
        path = Path(output.name)
    if not webbrowser.open(path.as_uri()):
        raise OSError(f"Could not open print preview. Open {path} in a browser.")


def snapshot_report_html(snapshot):
    """Render selected safe snapshot sections with independent table schemas."""
    cell = lambda value: escape(str(value if value is not None else ""))
    generated = snapshot.generated_at.astimezone(ZoneInfo('Asia/Manila')).strftime('%Y-%m-%d %H:%M:%S %Z')
    filters = dict(snapshot.filters)
    names={'academic_years':'Academic years','term_labels':'Terms','term_ids':'Stable term IDs','departments':'Departments','courses':'Courses','year_levels':'Year levels','sections':'Sections','statuses':'Statuses','appeal_statuses':'Appeal statuses','date_from':'From (inclusive, Manila)','date_to':'To (inclusive, Manila)','search':'Student search'}
    def display(value):
        if value is None:return 'All'
        if isinstance(value,(list,tuple)):return ', '.join(map(str,value)) if value else 'None (no matching rows)'
        return str(value) if value else 'None'
    summary = ' · '.join(f'{names.get(k,k.replace("_"," ").title())}: {display(v)}' for k,v in filters.items())
    semantics=('Dates are inclusive Asia/Manila dates: violations by occurrence, enrollment by confirmation, '
               'suspensions by start, and imported discipline by supplied date. Historical enrollment uses the '
               'latest revision effective at generation time. Status applies to enrollment and discipline; '
               'appeal status applies to live and imported discipline.')
    parts = []
    main_columns={
        'Student Directory':('student_id','name','college_department','course','year_level','section'),
        'Semester Enrollment Roster':('student_id','name','semester_id','semester_name','school_year','course','year_level','section','status'),
        'Violations':('record_id','student_id','name','semester_id','semester_name','category','status','appeal_status','date'),
        'Suspensions':('record_id','student_id','name','semester_id','semester_name','status','date','ends_at'),
        'Imported Discipline Snapshot':('record_id','source_record_id','student_id','name','semester_name','school_year','record_type','category','status','date')}
    for section in snapshot.sections:
        columns=main_columns.get(section.name,section.columns)
        heads = ''.join(f'<th>{cell(key.replace("_", " ").title())}</th>' for key in columns)
        body_parts=[]
        for row in section.rows:
            body_parts.append('<tbody class="record"><tr>' + ''.join(f'<td>{cell(row.get(key, ""))}</td>' for key in columns) + '</tr>')
            extra=[(key.replace('_',' ').title(),value) for key,value in row.items() if key not in columns and value not in (None,'')]
            if extra:
                details=' · '.join(f'<b>{cell(key)}:</b> {cell(value)}' for key,value in extra)
                body_parts.append(f'<tr class="detail"><td colspan="{len(columns)}">{details}</td></tr>')
            body_parts.append('</tbody>')
        body=''.join(body_parts)
        if not body:
            body = f'<tbody><tr><td colspan="{len(columns)}">No records match these filters.</td></tr></tbody>'
        parts.append(f'<section><h2>{cell(section.name)}</h2><p>{section.record_count} records · '
                     f'{section.distinct_students} distinct students</p><table><thead><tr>{heads}</tr></thead>'
                     f'{body}</table></section>')
    return f'''<!doctype html><html><head><meta charset="utf-8"><title>CBVMS Reports</title><style>
body{{font:12px Arial,sans-serif;color:#172033;margin:24px}}h1{{font-size:22px}}h2{{font-size:17px;break-after:avoid}}
table{{width:100%;border-collapse:collapse;font-size:10px;table-layout:auto}}th,td{{border:1px solid #cbd5e1;padding:5px;text-align:left;overflow-wrap:anywhere}}
th{{background:#edf2f7}}thead{{display:table-header-group}}tr,.record{{break-inside:avoid}}.detail td{{font-size:9px;background:#f8fafc}}section{{break-before:page}}section:first-of-type{{break-before:auto}}
@page{{size:landscape;margin:12mm}}@media print{{button{{display:none}}body{{margin:0}}}}
</style></head><body><button onclick="window.print()">Print / Save as PDF</button><h1>CBVMS Reports</h1>
<p>{cell(summary or 'No additional filters')} · Sections: {cell(', '.join(snapshot.selected_sections) if snapshot.selected_sections else 'None selected')} · {cell(semantics)}</p><p>Source: Local CBVMS database snapshot; imported rows retain their source labels · Generated {cell(generated)} · Asia/Manila</p>{''.join(parts)}</body></html>'''


def open_snapshot_print_preview(snapshot):
    with tempfile.NamedTemporaryFile(suffix='.html', prefix='cbvms_report_', delete=False, mode='w', encoding='utf-8') as output:
        output.write(snapshot_report_html(snapshot))
        path = Path(output.name)
    if not webbrowser.open(path.as_uri()):
        raise OSError(f'Could not open print preview. Open {path} in a browser.')
