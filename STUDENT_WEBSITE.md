# Connected student website

From PowerShell in `cbvms3.0`, run:

```powershell
.\.venv\Scripts\python.exe .\web_portal.py
```

Open **http://127.0.0.1:8080** and sign in with an existing student username and password.
The default database is `data/cbvms.db`, the same database used by the desktop app.
No extra packages or separate student registration are needed. Keep the server running
while using the website; Ctrl+C stops it.

The website includes dashboard standing and suspension status, violations and original
detection pictures, picture-backed appeal submission, admin appeal responses,
notifications and read controls, profile name and password changes, persistent dark
mode and compact sidebar preferences, and system reports sent to the desktop admin.
Record pages refresh every 30 seconds; Refresh fetches changes immediately.

Rejecting an appeal in the desktop admin awards one strike through the existing database
workflow. The third uniform strike creates a two-day suspension and queues the configured
SMTP suspension email. Initial detections do not send violation emails.

Sessions last eight hours and are cleared when the server stops. Each API operation uses
the authenticated student's ID; evidence and appeal access cannot be switched by supplying
another student ID. Requests use HTTP-only session cookies and CSRF tokens.

For staff-assisted registration before term dates are available, sign in as superadmin
and open Student Management's registration flow. Leave **Register now, assign term later**
selected, enter the student details, capture all three face angles, and save. The student
account and face data are saved together; the completion message shows the username and
temporary password. The student must change that password at first login. Registration
alone does not confirm academic term enrollment.

Later, publish the actual dates in Settings > Academic Terms. Open the enrollment flow
again with the same student ID, select the published term, and complete its fresh face
capture confirmation. The existing account and permanent face data are preserved.

Desktop and browser appearance preferences are saved per student in the shared database.
An already open desktop session applies changed preferences on its next login.

For an isolated database or another local port:

```powershell
.\.venv\Scripts\python.exe .\web_portal.py --database .\data\cbvms.db --port 8081
```

This is a local website. Public hosting is a separate deployment step requiring an HTTPS
application server and a chosen hosting destination.

Validation:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_web_portal
.\.venv\Scripts\python.exe -m unittest tests.test_student_portal_native_ui.PortalNativeTests.test_dark_mode_updates_canvas_and_survives_navigation
```
