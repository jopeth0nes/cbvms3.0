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
