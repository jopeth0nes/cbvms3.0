# CBVMS Codex workflow

**Astra → optional one Codex on Crack builder → implementation and worker tests → targeted ECC-guided Astra review → acceptance.**

GPT-6 Astra owns scope, architecture, consequential/security decisions and final review. Codex on Crack is the sole delegation authority. ECC supplies ten engineering skills, never agents. Small work stays direct; medium work ordinarily stays direct. Delegate only substantial, self-contained bundles (a portal feature, migration framework, large dashboard/refactor) when that saves effort. Text, spacing, small fixes, simple validation and queries stay with Astra.

Workers own scoped discovery, implementation, debugging and relevant checks. Astra reads the final diff, checks acceptance and reuses trustworthy evidence tied to the tested files/environment. Repeat checks only for invalidated evidence, gaps or material security, migration or camera/AI risk. No parallel reviewers, external leads, nested agents or fallback swarm.

## Installed skills

| Skill | Activation |
|---|---|
| `cbvms-verify` | Meaningful implementation/fix; affected tests and diff |
| `cbvms-security-review` | Identity, permissions, sessions, uploads, secrets or sensitive data |
| `cbvms-regression-first` | Confirmed bug; smallest practical reproduction/regression check |
| `cbvms-api-design` | Changed HTTP endpoint/consumer contract |
| `cbvms-db-migrations` | Schema/data migration, including intentional SQLite conversion |
| `cbvms-postgres` | Actual PostgreSQL schema, query plan, pooling or concurrency |
| `cbvms-deploy` | Hosting/release/recovery work; excludes development tooling |
| `cbvms-portal-e2e` | **Specialist/on-demand:** meaningful native/web student workflow |
| `cbvms-vision-eval` | **Specialist/on-demand:** camera, recognition, uniform or preview performance |
| `cbvms-portal-design` | **Specialist/on-demand:** portal layout, interaction or accessibility |
| `crack-plan` | Decide/define a worthwhile implementation handoff |
| `crack-setup` | Inspect, preview, configure or undo the single builder |
| `crack` | Dispatch the bounded bundle and review its returned evidence |

Skills have narrow automatic routing; specialist means load only for matching work, not on every turn. Each ECC entry is 165–237 words including frontmatter. No generic rules, hooks, MCP servers, panel, viewer, external execution adapter or full ECC package is installed. Existing application dependencies are unchanged.

## Configuration and current prerequisites

- `.agents/plugins/marketplace.json`: the two local plugin sources under `plugins/`.
- `.codex/config.toml`: project enablement, Astra lead and one concurrent child (excluding lead), maximum depth one. Existing global provider, effort and permissions are inherited.
- `.codex/crack/crack.toml`: exactly one `builder`, `gpt-6-luna`, `medium`, native inherited OpenAI provider; no fallback.
- `.codex/agents/crack_builder.toml`: upstream-generated role, automatically discovered by Codex; its own agents are disabled. Do not add an inline duplicate role.
- `.codex/crack-backups/`: ignored local setup receipts; keep them for safe undo.

Final compatibility validation repeated 2026-10-06: **Node 24.21.0**, npm **11.19.0**, Codex CLI **0.160.0**. Codex's shell and helper scripts resolve `/Users/jopethones/Library/Application Support/Herd/config/nvm/versions/node/v24.21.0/bin/node`, satisfying the upstream minimum **24.15.0**. The original installation on 2026-10-05 used Node 24.14.0; its runtime prerequisite is now resolved. Plugin discovery, strict configuration, thirteen skills, source integrity, project isolation and doctor checks pass. No runtime or configuration changes were needed in the completion pass.

The sole worker completed a tiny read-only exercise through the actual configured **`crack_builder`** role on 2026-10-06. Host-recorded child metadata confirms that named role, `gpt-6-luna`, `medium` and provider `openai`. It read only `AGENTS.md` and made no edits or descendants. Doctor's `static-ready` result remains a static check; this separate smoke proves actual worker reachability. No external provider or credentials were added; no pricing or savings claim is made. Start a new conversation after future plugin/role changes to load the updated catalog. Host/UI/managed overrides still apply.

Codex requires its shared installed cache under `~/.codex/plugins/cache/cbvms-local/`. The official installer was used; `scripts/install_codex_plugins.py` preserves the original global configuration bytes after CLI installation. Persistent enablement lives only in this trusted repository. Global plugins/MCPs and the existing `.claude` settings were preserved. The IDE extension's plugin surface may differ; actual discovery was verified through the installed Codex app-server/CLI.

## Verify, disable and maintain

From the repository:

```sh
codex plugin list --marketplace cbvms-local --json
.venv/bin/python scripts/verify_codex_workflow.py
node plugins/codex-on-crack/skills/crack/scripts/doctor.mjs --codex-home "$PWD/.codex"
```

The verifier uses real `config/read` and `skills/list`, strict config parsing, checks thirteen unique enabled skills and project isolation, and makes no model request. Codex may need permission for its own state database/cache. Discovery errors fail the check. It does not test CBVMS application behavior. In a new supported client, inspect the skill picker for `cbvms-ecc:*` and `codex-on-crack:*`.

To disable orchestration, set `[plugins."codex-on-crack@cbvms-local"] enabled = false` and `[agents] enabled = false` in project config. Set `[plugins."cbvms-ecc@cbvms-local"] enabled = false` to disable ECC independently. Restart the conversation. Disabling is separate from uninstalling cached packages or undoing generated roles.

For worker changes use `crack-setup`: always pass `--codex-home "$PWD/.codex"`, preview before apply, and use the real host catalog with `--model-catalog`. Do not change the running process's CODEX_HOME. Undo previews with `setup.mjs undo --codex-home "$PWD/.codex" --receipt <local-receipt>`; add `--apply` for an authorized undo. Inspect edits before restoring; receipts deliberately reject conflicting later changes.

To update either plugin, inspect current upstream and the pinned source mapping in its `UPSTREAM.json`. ECC updates are selective re-adaptation, not a bulk installer. Codex on Crack updates must retain the native dependency closure and local policy; do not restore panel/external-lead features. Preserve MIT notices and refresh integrity hashes for any intentionally changed vendored files.

Use Plugin Creator's `read_marketplace_name.py --marketplace-path "$PWD/.agents/plugins/marketplace.json"` and `update_plugin_cachebuster.py <plugin-directory>` before reinstalling an edited plugin. Then run `.venv/bin/python scripts/install_codex_plugins.py`, the plugin/skill validators and the discovery check. Start a new conversation. Do not hand-edit the marketplace during this update flow.

Local marketplace/catalog paths are absolute as required by the inspected configuration/setup mechanisms. After moving/cloning the checkout, set `marketplaces.cbvms-local.source` to the new absolute root and rerun worker setup with that machine's actual model catalog before installation. Do not copy credentials or claim catalog presence proves access.

Sources: [ECC upstream](https://github.com/affaan-m/ECC), [reference fork](https://github.com/worldflowai/everything-claude-code), [Codex on Crack](https://github.com/ethanplusai/codex-on-crack), [official repo plugin configuration](https://developers.openai.com/plugins/build/plugins#enable-or-disable-a-plugin-for-a-repo), [official subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents). Exact inspected revisions, validation and every added file are recorded in `CODEX_AI_VALIDATION.md` and plugin `UPSTREAM.json` files.
