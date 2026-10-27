# Current-semester activation — 2026-10-27

The owner requested current-semester activation for students `2023-00117` and `2023-00883`, supplied June–December 2026 as the semester dates, and explicitly authorized administrative enrollment exceptions without fresh captures. Dates were interpreted as June 1 through December 31, 2026 (inclusive, Asia/Manila), using the existing First Semester, AY 2026–2027 term, ID 1.

## Applied changes

- Published the existing term with those dates. Existing attendance, violations and historical assignments were retained.
- Restored `2023-00883` from the Recycle Bin using the existing local/cloud lifecycle operation and a verified provider receipt, retaining the original account identity.
- Added immutable administrative exception proofs and enrollment revisions for both students, effective when approved. These do not backdate eligibility to June or create camera captures. The underlying enrollment rows remain uncaptured drafts; eligibility, roster and public history explicitly recognize the exception.
- Applied additive cloud migration `0012_academic_owner_updates.sql`. Provider-only academic updates validate the existing enabled superadmin/import authority, current snapshot and account lifecycle state, then atomically update only calendar and enrollment projection paths. Immutable receipts retain the reason. No HTTP issuer was added.
- Updated production D1 projections serving `studentportal.bond` for these two students. Other terms and history paths remain intact.

## Verification and recovery

Focused enrollment checks: `tests.test_term_enrollment_exception`, `tests.test_term_enrollment`, and `tests.test_registration_without_term`: **37 passed**. Cloud projection/lifecycle checks: `tests.test_academic_owner_projection`: **14 passed**. The exact restoration operation also passed twice against disposable local/cloud fixtures, producing one operation and preserving credentials.

Live checks confirmed current-term eligibility, enabled/unarchived account state, semester dates, and administrative exception proofs. Comparing the local database to its pre-change backup confirmed unchanged student accounts/passwords, captures, violations, attendance events, security events and strikes. SQLite integrity and foreign keys passed; the additive local schema migration was safely repeated.

Private operational receipts, durable operation UUIDs, a pre-change SQLite backup and the managed D1 Time Travel bookmark are retained under `.runtime/current-semester-activation/` (ignored, private). Do not export the production student database for verification or blindly restore a backup over subsequent legitimate changes. Reconcile using the receipts and current state first. No staff/student password was entered and no live student session was used to test sign-in.

This updates this campus database and the cloud projection. An offline installation on another computer was not changed; it must receive the authorized state through its supported synchronization/reconciliation process before publishing an older snapshot. The current Mac's general publication writer remains disabled; no Windows writer identity was copied.
