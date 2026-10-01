# Main, Mateo, and local changes — October 1, 2026

The combined `main` includes the local camera/portal work committed as `dc13c9d`,
GitHub main's lifecycle fix `174c39f` (reconciled in `bb3f18b`), and Mateo's appeal
update `d3389e2`. The local Mateo documentation commit `581e2af` was already an
ancestor of main. The original branch histories are preserved.

## Integration decisions

- Keep the coordinated face/torso pipeline, original-frame evidence, model
  readiness/retry, and cancellation checks in both notifications and database writes.
- Combine `WorkspaceWindow` cleanup/reveal with the portal's background worker,
  native scroll container, pagination, request cancellation, and authenticated
  database/student mapping.
- Require a JPG, JPEG, PNG, or BMP picture of at most 10 MB and an explanation of
  20–1000 characters for new appeals. Validate the picture on the portal worker;
  commit appeal and evidence in one database transaction. Failed evidence leaves
  no partial appeal. The backend checks the deadline after obtaining the write lock.
- Load notification-linked violation metadata and appeal eligibility on the worker.
  Existing buttons expire from cached deadlines without querying SQLite on Tk's
  thread. Background refresh picks up admin decisions and keeps open drafts intact.
- Preserve persistent appeal alerts, separate appeal/violation unread counts,
  navigation to the selected appeal, evidence enlargement, and the deciding
  administrator's username while retaining lazy dashboard panel construction.
- Update older appeal tests to use valid picture fixtures and the actual asynchronous
  portal. Correct an automatic merge that misplaced notification actions in the
  mark-read method.

## Validation

- 337 backend/camera tests passed, covering the combined existing suites and Mateo's
  backend appeal cases.
- One additional notification/appeal integration regression passed in the complete
  10-test portal-worker suite. It checks ownership, metadata without evidence blobs,
  decision refresh, and eligibility updates for an open dialog after navigation.
- 33 native macOS UI tests passed together, including eight existing portal tests,
  eight appeal/alert tests, and 17 monitor/student-management/records/suspension tests.
- Total: **371 distinct passing tests**. Python source parsing/compilation and
  `git diff --check` also passed.

Tests used disposable SQLite databases. This merge verification did not launch the
production app or modify its student records. Appeal advisory network calls were
mocked in UI submission tests; physical camera accuracy and Windows/Linux rendering
were not revalidated during this merge.

Focused checks can be repeated from the repository root:

```sh
.venv/bin/python -m unittest tests.test_portal_requests tests.test_violation_workflow tests.test_suspensions tests.test_appeal_flow_completion.AppealFlowBackendTests -v
```

Native checks require a desktop session:

```sh
.venv/bin/python -m unittest tests.test_student_portal_native_ui tests.test_appeal_flow_completion.AppealFlowWidgetTests tests.test_appeal_alerts tests.test_live_monitor_native_ui tests.test_student_management_ui tests.test_records_ui tests.test_suspensions_ui -v
```

## Running the combined version

1. Close running app instances before updating another installation. Preserve its
   existing database and model files; those machine-local assets are not distributed
   by this merge. Back up the database before the first production launch.
2. Pull `main` on other machines, then launch with the existing Python environment:
   `.venv/bin/python main.py`. No dependency requirements changed in this merge.
3. Normal startup applies the additive, idempotent `appeals.admin_alert_read`
   migration automatically. No manual SQL migration or data reset is needed.
4. Use persisted student accounts. The earlier local changes remove the hardcoded
   student-login fallback; missing accounts should be registered/provisioned through
   the application's account workflow.
5. Check real camera tracking with one and two people, seated/standing clothing,
   occlusion, and camera switching. Check login/logout and the picture-backed appeal
   workflow on each target operating system. Verify the configured external advisory
   service separately if that service is required.

If startup reports a missing column, launch through `main.py` so initialization runs
against the same database used by the portal. If SQLite reports a lock, close other
app instances and retry; do not delete the database. If models are unavailable, use
the model-loading retry and verify that the installation's existing trained weights
are present. These are recovery options, not failures observed in the merge tests.
