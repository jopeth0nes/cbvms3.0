# CBVMS development invariants

- Inspect relevant files first; prefer targeted changes, preserve working modules and existing user edits, and avoid unrelated refactors during fixes.
- Keep expensive camera/ML inference off the CustomTkinter UI thread. Preserve responsive preview, latest-frame backpressure and cooldowns; higher inference frequency needs measured justification.
- Preserve student/admin/superadmin authorization and resource ownership. Protect student and biometric information, photos, embeddings, credentials and sessions.
- Preserve SQLite compatibility except in intentional migration work. Verify with disposable data, never the live database.
- Run relevant verification before declaring completion; report missing evidence honestly.
- GPT-6 Astra owns scope, architecture, consequential/security decisions and final review. Small and ordinarily medium tasks stay direct. Only worthwhile bounded implementation bundles use one Codex on Crack builder; workers never delegate. No parallel reviewers or competing planners.
- When Codex on Crack is active, it owns model delegation and worker orchestration. CBVMS ECC skills must not independently spawn competing agents or duplicate completed worker discovery, testing, or verification. Reuse trustworthy worker evidence and perform additional checks only where risk, uncertainty, or missing evidence warrants it.

Setup, skill routing, limitations and maintenance: `docs/CODEX_AI_WORKFLOW.md`.
