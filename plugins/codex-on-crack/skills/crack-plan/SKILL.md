---
name: crack-plan
description: "Assess whether a substantial CBVMS implementation merits the single configured worker and define its bounded assignment. Skip small changes and already settled task splits."
---

# Decide whether delegation pays

Apply repository scope first: small and ordinarily medium tasks stay with Astra. Only a substantial, self-contained bundle warrants one implementation worker; Astra still owns architecture and final acceptance. Reuse existing requirements and approved decisions.

For local capability facts run `node <this-skill>/scripts/plan.mjs --codex-home <repo>/.codex --task implementation --mode solo` or `--mode delegated`. The helper is static: it neither understands task size nor proves provider access. Check the Node >=24.15 prerequisite and actual host route separately; reuse fresh evidence.

Prepare a short brief with allowed paths, non-goals, acceptance, relevant tests and stop conditions. Honor the configured `crack_builder`; no other planner, reviewer, model fallback or external lead. If the route is unavailable, remain direct. Existing authorization for a bounded implementation does not need another plan approval. Hand an approved bundle to `codex-on-crack:crack`.
