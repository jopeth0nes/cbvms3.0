from pathlib import Path
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, KeepTogether
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
import fitz

root=Path.cwd()
out=root/'output/pdf/CBVMS_Major_System_Changes_September_October_2026.pdf'
items=[
('Attendance recording and printable violation reports','Added attendance records and printable violation reports.','Sep 18','3855cfd'),
('Student status and contact management','Added enrollment-status controls, pending-registration verification, contact details, status history, and premises-entry records.','Sep 22','6e93de1'),
('Appeal submission and review workflow','Expanded student appeals, supporting-image submissions, administrator alerts, decision history, and links to the original violation.','Sep 25-Oct 1','e1cd6ba'),
('Desktop interface redesign','Refreshed the campus interface, welcome transitions, profile photos, workspace navigation, and startup behavior.','Sep 26','ca7990a'),
('Safer face enrollment and registration','Improved capture validation, face-to-student association, preview responsiveness, and recovery from interrupted enrollment.','Sep 26-Oct 2','741ed9f'),
('Suspension management and analytics','Added and refined suspension administration, lifting and history, student-record views, filters, trends, and analytics.','Sep 26-Oct 7','c9db5c4'),
('YOLOv8 monitoring improvements','Improved person/torso association, uniform-assessment coordination, model safety, and readiness. Existing YOLOv8 models were retained.','Sep 28-Oct 23','b489989'),
('Face recognition and identity-tracking improvements','Strengthened identity consistency, unknown-face handling, stale-result rejection, and recognition-gallery management using existing InsightFace/ArcFace.','Sep 28-Oct 25','7d04ae4'),
('Responsive monitoring and portal loading','Separated background analysis and persistence from UI rendering; bounded frame queues and rejected outdated asynchronous portal responses.','Sep 28-30','dc13c9d'),
('Login and application lifecycle fixes','Reduced login/workspace flicker and improved window transitions, worker cleanup, and application shutdown.','Sep 29-Oct 7','174c39f'),
('Evidence integrity and case ownership','Added stronger original/supporting evidence association, SHA-256 integrity checks, and audit tooling to prevent mismatched case evidence.','Oct 1','6a528ac'),
('Connected student web portal','Added browser access to student profiles, violations, appeals, notifications, and settings; improved discipline email notifications.','Oct 2','8b56418'),
('CSV reporting and roster handling','Added CSV exports and refined reporting workflows; later added validated roster imports and spreadsheet-formula escaping.','Oct 3-5','3ffe355'),
('Standard academic classifications','Introduced a shared seven-college, 25-program catalog, dependent College/Course controls, separate year/section fields, and legacy-value preservation.','Oct 5','9f68e6f'),
('Campus-sighting attendance system','Added immutable sighting events, daily summaries, camera/source details, cooldowns, duplicate protection, pagination, and filtered exports.','Oct 5','9f68e6f'),
('Password setup and session security','Added mandatory first-login password setup, restricted sessions, credential revisions, persisted sessions, and PBKDF2-SHA256 password hashing.','Oct 5','9f68e6f'),
('Structured appeal decision categories','Required decision categories and explanations; recorded them with administrator identity, timestamps, history, notifications, and discipline transitions.','Oct 5','9f68e6f'),
('Unknown-person sightings and detection audio','Added unknown-sighting handling and UI improvements, plus prioritized detection sounds with bounded queues and cooldowns.','Oct 5-19','c9db5c4'),
('Supporting-image forensic review','Added background forensic jobs, metadata/container checks, optional C2PA provenance, and staff analysis history. Learned manipulation detection remains experimental.','Oct 6 / merged Oct 21','244d680'),
('IP/USB camera support and freeze fixes','Expanded IP-camera and Windows USB-camera handling; improved camera switching, reconnect behavior, and freeze diagnostics.','Oct 18-24','13fcdc7'),
('Cloud synchronization protocol v2','Added registered writer/source checks, publication generations, durable acknowledgments, evidence verification, credential-conflict checks, and reconciliation audits.','Oct 20','4292b11'),
('Academic calendars and semester enrollment','Added published calendars, term-specific enrollment/history, and semester-aware attendance, violations, strikes, suspensions, reports, and cloud snapshot schema v3.','Oct 21','4b358ed'),
('Faster model preparation and diagnostics','Started shared model warmup during login, reused model instances, removed redundant frame copies, and added bounded timing and benchmark tools.','Oct 23','b489989'),
('Semester-aware Reports workspace','Added separate directory, enrollment, violation, suspension, and imported-history sections; multi-select filters, pagination, CSV/ZIP exports, and print/PDF output.','Oct 24','c7c6e28'),
('Cloudflare deployment and additive campus import','Deployed the portal with Workers, D1, private R2, and PasswordCrypto; added five MacBook accounts, mapped case IDs, and verified evidence without replacing existing records.','Oct 24','d43ff56'),
('Superadmin Recycle Bin and restoration','Added recycle/restore controls, lifecycle versions, audit records, durable cloud delivery, session revocation, and safeguards against stale reactivation.','Oct 25','338ad83'),
]
navy=HexColor('#153047'); teal=HexColor('#187D85'); gray=HexColor('#536171')
title=ParagraphStyle('title',fontName='Helvetica-Bold',fontSize=22,leading=26,textColor=navy,spaceAfter=7)
sub=ParagraphStyle('sub',fontName='Helvetica',fontSize=10,leading=14,textColor=gray,spaceAfter=17)
entry=ParagraphStyle('entry',fontName='Helvetica',fontSize=9.5,leading=12.5,textColor=navy,spaceAfter=10)
small=ParagraphStyle('small',fontName='Helvetica',fontSize=8,leading=11,textColor=gray)
story=[]
for page in range(2):
 if page:story.append(PageBreak())
 story.append(Paragraph('CBVMS | Major System Changes',title))
 story.append(Paragraph('September 1 - October 27, 2026  /  GitHub-committed additions and improvements',sub))
 for i,(heading,desc,date,commit) in enumerate(items[page*13:(page+1)*13],start=page*13+1):
  story.append(Paragraph(f'<font color="#187D85"><b>{i:02d}</b></font>  <b>{heading}</b> <font color="#536171">({date})</font><br/>{desc} <a href="https://github.com/jopeth0nes/cbvms3.0/commit/{commit}" color="#187D85">[{commit}]</a>',entry))
 if page==1:
  story.append(Spacer(1,3))
  story.append(Paragraph('Scope: main through commit 338ad83. Uncommitted incremental-sync work is excluded. YOLOv8 and InsightFace existed before September; no new major model version or tagged application-version change was found in this period.',small))
def footer(c,d):
 c.setStrokeColor(HexColor('#D9E1E7'));c.line(40,37,A4[0]-40,37)
 c.setFont('Helvetica',8);c.setFillColor(gray)
 c.drawString(40,24,'Source: github.com/jopeth0nes/cbvms3.0 | Commit references are clickable')
 c.drawRightString(A4[0]-40,24,f'{d.page} / 2')
SimpleDocTemplate(str(out),pagesize=A4,rightMargin=40,leftMargin=40,topMargin=34,bottomMargin=48,title='CBVMS Major System Changes | September-October 2026',author='CBVMS',pageCompression=1).build(story,onFirstPage=footer,onLaterPages=footer)
doc=fitz.open(out)
assert len(doc)==2,len(doc)
text=''.join(p.get_text() for p in doc)
for heading,_,_,_ in items:assert heading in text,heading
for i,p in enumerate(doc):p.get_pixmap(matrix=fitz.Matrix(1.4,1.4)).save(root/f'tmp/pdfs/changes-page-{i+1}.png')
print(f'{out}\nPages: {len(doc)}; major changes: {len(items)}; text verified.')
