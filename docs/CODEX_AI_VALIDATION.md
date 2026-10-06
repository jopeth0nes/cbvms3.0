# AI workflow installation evidence

Original installation validated 2026-10-05; final compatibility validation repeated 2026-10-06. At the start of the original installation, the working tree was clean and no project AGENTS.md, .agents or .codex directory existed. Existing global plugins/MCPs, model/provider configuration, Python environment and .claude settings were inspected and preserved.

## Completion validation — 2026-10-06

- Started on clean `Ecc-Coc` at `97fd43d` (`Ecc Coc`), matching the local `origin/Ecc-Coc` tracking ref. The original installation files are now tracked in that commit; the historical unstaged-file report below describes the original session only.
- Codex's execution shell resolves Node **v24.21.0** at `/Users/jopethones/Library/Application Support/Herd/config/nvm/versions/node/v24.21.0/bin/node`; `command -v node`, `which node`, `node --version` and `process.execPath` agree. npm is **11.19.0**, Codex CLI **0.160.0**. The verifier and Codex on Crack helpers ran with this same PATH and compliant Node runtime. No runtime installation, PATH correction or bundled-runtime change was necessary.
- `codex plugin list --marketplace cbvms-local --json`: ECC **0.1.0** and Codex on Crack **2.2.2+cbvms.1** both installed and enabled.
- `.venv/bin/python scripts/verify_codex_workflow.py`: **passed** strict configuration parsing, Astra lead, one Luna Medium builder with nested agents disabled, all 13 expected skills with zero loading errors, vendored source integrity and isolation from `/private/tmp`. Node reported **v24.21.0**, above **24.15.0**. Initial sandbox execution prevented app-server startup; rerunning with approved access to normal Codex state/cache succeeded without configuration changes.
- `node plugins/codex-on-crack/skills/crack/scripts/doctor.mjs --codex-home "$PWD/.codex"`: **passed**, `ok: true`, `static-ready`, no problems, generated role in sync. Doctor deliberately reports `runtime_verified: false`; live reachability is established separately below. Selector also chose only `crack_builder`.
- One read-only smoke used the actual configured **`crack_builder`** role. Host rollout metadata records child **`01a10ef1-905e-70a1-948d-04acd66f0479`**, path **`/root/crack_builder_smoke`**, depth **1**, role **`crack_builder`**, model **`gpt-6-luna`**, effort **`medium`**, provider **`openai`**. It read `AGENTS.md` and returned the CBVMS name and architecture sentence. No edits, descendants or deployment. This confirms named-role loading and actual worker reachability, beyond catalog/static checks.
- Source/cache bytes and file sets match for both plugins. Global configuration still has SHA-256 `1dd04708ed4931c81f19cd8d1166a87f52829f3c0fed379f99040d4a7dc28963`. Existing orchestration policy remains intact: Astra leads and reviews, Codex on Crack alone delegates substantial bounded work, ECC supplies guidance, and worker evidence is reused with independent checks for material gaps/risks.
- No panel, panel MCP, external execution adapter, ECC agents, parallel workers, fallback swarm, generic skill packs, continuous-learning, strategic-compact or unrelated MCP servers were introduced. No plugin reinstallation or configuration change was needed.
- Completion-pass changes are limited to this document and `docs/CODEX_AI_WORKFLOW.md`; CBVMS application code/data are unchanged. No staging, commit, push or deployment occurred during this pass. No remaining installation blocker.

**ECC + CODEX ON CRACK INSTALLATION: FULLY OPERATIONAL**

## Original installation result — 2026-10-05

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
- Application runtime files and data were not modified. No application suite was run for this tooling-only change. No staging, commit, push or PR occurred during the original installation session; the files were subsequently committed before the completion pass.

## Limitations / prerequisites

The original installation used Git 2.50.1, npm 11.9.0, Python venv 3.12.13, Codex 0.155.0-alpha.16.3 and Node **24.14.0**. That Node version was below upstream's **24.15.0** requirement, so the original static checks did not establish supported runtime compatibility. This prerequisite was resolved by the 2026-10-06 completion validation on **24.21.0**, including actual named-worker reachability. No new provider, account or credentials were needed. Model quality, costs, quota savings and application performance were not measured.

The original conversation required a new session to load the installed catalog and named role; the completion session successfully loaded and dispatched that role. Actual discovery is proven through CLI/app-server and this host, not through every IDE plugin UI. Absolute local marketplace and catalog paths need refresh on another checkout/machine.

## Source pins

| Source | Inspected commit |
|---|---|
| affaan-m/ECC (requested everything-claude-code URL redirects here) | `ef648e01899ba3e8dc6371642deaaf64b4477775` |
| worldflowai/everything-claude-code reference fork | `432485ba6b92c14fb357276a98957f348bcff9ee` |
| ethanplusai/codex-on-crack | `9dee6ca9aaa73e0008123eff872871e88b94a12a` |

`plugins/*/UPSTREAM.json` maps adapted ECC concepts and records native source hashes/local changes. MIT licenses are preserved, including vendored smol-toml's license. No upstream installation scripts were run.

## Historical Git report — original installation session

Only existing file modified: `.gitignore` (+3 lines for ignored local undo receipts).

```text
 .gitignore | 3 +++
 1 file changed, 3 insertions(+)
```

At that time, `git diff --stat` excluded the new unstaged files listed below, which were intentionally left untracked. They were subsequently committed in `97fd43d`; this is not the current working-tree status.

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
