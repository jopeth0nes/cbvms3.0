# AI workflow installation evidence

Validated 2026-10-05. Initial working tree was clean. No pre-existing project AGENTS.md, .agents or .codex directory existed. Existing global plugins/MCPs, model/provider configuration, Python environment and .claude settings were inspected and preserved.

## Result

- Installed `cbvms-ecc@cbvms-local` 0.1.0 and native-only `codex-on-crack@cbvms-local` 2.2.2+cbvms.1 through the official Codex installer.
- Both plugin manifests pass Plugin Creator validation; all 13 skills pass Skill Creator validation. Ten ECC skills total 1,892 words including routing frontmatter (165–237 each). No duplicate skill names.
- Actual `codex plugin list` reports both installed and enabled. `codex plugin marketplace list` resolves this checkout as `cbvms-local`.
- Actual app-server `config/read` under `--strict-config` loads Astra and one concurrent child. `skills/list` finds exactly the 13 requested skills, enabled, with zero loading errors. The same check outside the repository finds no CBVMS settings or skills.
- Upstream setup preview/apply generated exactly one builder; doctor reports `static-ready`, selector chooses only that builder, and solo/delegated planner checks both pass. Undo was previewed successfully without applying it.
- One read-only worker smoke completed. Host thread metadata: `01a10aa7-a8d3-7801-a4d1-0ef8e5eaed74`, path `/root/crack_builder_smoke`, model `gpt-6-luna`, provider `openai`, effort `medium`. No worker edits, descendants, implementation or application tests. The host dispatched exact model/effort using its available schema; a fresh session must load the new named role.
- Smoke routing: spacing stays direct with portal-design; substantial migration framework can use one worker with migration/PostgreSQL guidance; duplicate unknown-sighting investigation stays direct with vision guidance. Regression-first applies once a bug is confirmed.
- Source and installed cache bytes match for every plugin file. Vendored native sources have recorded SHA-256 provenance. JSON/TOML parse, Python/Node syntax and `git diff --check` pass.
- No panel, viewer, external execution adapter, hooks, new MCP server, frontend framework or Python dependency installed. ECC never delegates; Codex on Crack is the only orchestration authority.
- Global `~/.codex/config.toml` is byte-for-byte unchanged, SHA-256 `1dd04708ed4931c81f19cd8d1166a87f52829f3c0fed379f99040d4a7dc28963`. Official plugin installation writes its shared cache and briefly saves enabled flags; the installer restores original config bytes and refuses to overwrite unexpected concurrent changes.
- Application runtime files and data were not modified. No application suite was run for this tooling-only change. No staging, commit, push or PR occurred.

## Limitations / prerequisites

Git 2.50.1, npm 11.9.0, Python venv 3.12.13 and Codex 0.155.0-alpha.16.3 are available. Plugins and multi-agent features are enabled by the installed client. Node is **24.14.0**, below upstream's **24.15.0** requirement; it was not upgraded. Successful static helpers and native worker reachability do not waive this prerequisite or establish full runtime compatibility. No new provider, account or credentials are needed for the verified native worker. Model quality, costs, quota savings and application performance were not measured.

The current conversation's catalog is fixed; restart Codex/start a new conversation to use the installed catalog and named role. Actual discovery is proven through CLI/app-server, not through every IDE plugin UI. Absolute local marketplace and catalog paths need refresh on another checkout/machine.

## Source pins

| Source | Inspected commit |
|---|---|
| affaan-m/ECC (requested everything-claude-code URL redirects here) | `ef648e01899ba3e8dc6371642deaaf64b4477775` |
| worldflowai/everything-claude-code reference fork | `432485ba6b92c14fb357276a98957f348bcff9ee` |
| ethanplusai/codex-on-crack | `9dee6ca9aaa73e0008123eff872871e88b94a12a` |

`plugins/*/UPSTREAM.json` maps adapted ECC concepts and records native source hashes/local changes. MIT licenses are preserved, including vendored smol-toml's license. No upstream installation scripts were run.

## Git report

Only existing file modified: `.gitignore` (+3 lines for ignored local undo receipts).

```text
 .gitignore | 3 +++
 1 file changed, 3 insertions(+)
```

`git diff --stat` excludes the new unstaged files listed below. They remain untracked intentionally.

```text
 M .gitignore
?? .agents/
?? .codex/
?? AGENTS.md
?? docs/CODEX_AI_VALIDATION.md
?? docs/CODEX_AI_WORKFLOW.md
?? plugins/
?? scripts/install_codex_plugins.py
?? scripts/verify_codex_workflow.py
```

## Every new repository file (54)

- `.agents/plugins/marketplace.json`
- `.codex/agents/crack_builder.toml`
- `.codex/config.toml`
- `.codex/crack/crack.toml`
- `AGENTS.md`
- `docs/CODEX_AI_VALIDATION.md`
- `docs/CODEX_AI_WORKFLOW.md`
- `plugins/cbvms-ecc/.codex-plugin/plugin.json`
- `plugins/cbvms-ecc/LICENSE`
- `plugins/cbvms-ecc/UPSTREAM.json`
- `plugins/cbvms-ecc/skills/cbvms-api-design/SKILL.md`
- `plugins/cbvms-ecc/skills/cbvms-db-migrations/SKILL.md`
- `plugins/cbvms-ecc/skills/cbvms-deploy/SKILL.md`
- `plugins/cbvms-ecc/skills/cbvms-portal-design/SKILL.md`
- `plugins/cbvms-ecc/skills/cbvms-portal-e2e/SKILL.md`
- `plugins/cbvms-ecc/skills/cbvms-postgres/SKILL.md`
- `plugins/cbvms-ecc/skills/cbvms-regression-first/SKILL.md`
- `plugins/cbvms-ecc/skills/cbvms-security-review/SKILL.md`
- `plugins/cbvms-ecc/skills/cbvms-verify/SKILL.md`
- `plugins/cbvms-ecc/skills/cbvms-vision-eval/SKILL.md`
- `plugins/codex-on-crack/.codex-plugin/plugin.json`
- `plugins/codex-on-crack/LICENSE`
- `plugins/codex-on-crack/UPSTREAM.json`
- `plugins/codex-on-crack/package.json`
- `plugins/codex-on-crack/skills/crack-plan/SKILL.md`
- `plugins/codex-on-crack/skills/crack-plan/scripts/plan.mjs`
- `plugins/codex-on-crack/skills/crack-setup/SKILL.md`
- `plugins/codex-on-crack/skills/crack/SKILL.md`
- `plugins/codex-on-crack/skills/crack/references/policy.md`
- `plugins/codex-on-crack/skills/crack/references/worker-contract.md`
- `plugins/codex-on-crack/skills/crack/scripts/doctor.mjs`
- `plugins/codex-on-crack/skills/crack/scripts/lib/catalog.mjs`
- `plugins/codex-on-crack/skills/crack/scripts/lib/changes.mjs`
- `plugins/codex-on-crack/skills/crack/scripts/lib/config.mjs`
- `plugins/codex-on-crack/skills/crack/scripts/lib/io.mjs`
- `plugins/codex-on-crack/skills/crack/scripts/lib/plan.mjs`
- `plugins/codex-on-crack/skills/crack/scripts/lib/readiness.mjs`
- `plugins/codex-on-crack/skills/crack/scripts/lib/roles.mjs`
- `plugins/codex-on-crack/skills/crack/scripts/lib/routing.mjs`
- `plugins/codex-on-crack/skills/crack/scripts/lib/toml.mjs`
- `plugins/codex-on-crack/skills/crack/scripts/lib/vendor/smol-toml/LICENSE`
- `plugins/codex-on-crack/skills/crack/scripts/lib/vendor/smol-toml/date.js`
- `plugins/codex-on-crack/skills/crack/scripts/lib/vendor/smol-toml/error.js`
- `plugins/codex-on-crack/skills/crack/scripts/lib/vendor/smol-toml/extract.js`
- `plugins/codex-on-crack/skills/crack/scripts/lib/vendor/smol-toml/index.js`
- `plugins/codex-on-crack/skills/crack/scripts/lib/vendor/smol-toml/parse.js`
- `plugins/codex-on-crack/skills/crack/scripts/lib/vendor/smol-toml/primitive.js`
- `plugins/codex-on-crack/skills/crack/scripts/lib/vendor/smol-toml/stringify.js`
- `plugins/codex-on-crack/skills/crack/scripts/lib/vendor/smol-toml/struct.js`
- `plugins/codex-on-crack/skills/crack/scripts/lib/vendor/smol-toml/util.js`
- `plugins/codex-on-crack/skills/crack/scripts/select.mjs`
- `plugins/codex-on-crack/skills/crack/scripts/setup.mjs`
- `scripts/install_codex_plugins.py`
- `scripts/verify_codex_workflow.py`

## Local installation artifacts

Ignored undo receipt files (kept locally, not intended for Git):

- `.codex/crack-backups/20261005T060139Z-77faa007/receipt.json`

The official installed copies mirror every file beneath the corresponding plugin source listed above:

- `~/.codex/plugins/cache/cbvms-local/cbvms-ecc/0.1.0/`
- `~/.codex/plugins/cache/cbvms-local/codex-on-crack/2.2.2+cbvms.1/`

Codex's own installation/state bookkeeping may change during official installation/discovery. User configuration does not. Inspected upstream checkouts and transient planning/validation files remain under `/private/tmp/cbvms-*`; they are not runtime dependencies. The fetched official manual uses the OpenAI Docs temporary cache.
