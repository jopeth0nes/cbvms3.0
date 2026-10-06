---
name: crack-setup
description: "Inspect or update the single approved CBVMS Codex on Crack worker, preview configuration or undo setup. Do not activate for ordinary implementation or change global providers/credentials."
---

# Repository-scoped worker setup

Helpers live in `../crack/scripts/`. Pass `--codex-home <repo>/.codex` on every helper invocation; this selects setup storage, not the running host's home. Preserve Astra and inherited provider/permissions. Node >=24.15 is required upstream; do not upgrade system runtimes silently.

Use `setup.mjs scan --model-catalog <actual-host-catalog>` for current eligibility. Read `.codex/crack/crack.toml`; keep exactly one builder, no fallback, with an exact supported model/effort. Catalog metadata is not access proof. Do not request credentials when the existing native route suffices.

Preview `setup.mjs plan --roles <draft> --model-catalog <actual-host-catalog>` before `setup.mjs apply` using the same arguments. These helpers generate `.codex/agents/crack_builder.toml`, `.codex/crack/crack.toml` and local backup receipts; they do not edit config.toml. Codex discovers the project agent file automatically; do not also register an inline role, which upstream correctly treats as a conflict. Preserve the single-child limit. Do not use `--policy` to install a second policy document.

Run `doctor.mjs` and use host model/agent discovery. A tiny authorized read-only smoke may establish reachability; never call static readiness runtime verification. A new session must load changed role files. Reuse valid evidence until configuration changes.

Undo first previews with `setup.mjs undo --receipt <receipt>`; add `--apply` only within authorized scope. Preserve receipts and refuse overwriting later edits. Disabling orchestration is separate: disable this plugin and project agents in `.codex/config.toml`.
