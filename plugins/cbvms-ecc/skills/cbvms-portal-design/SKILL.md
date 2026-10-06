---
name: cbvms-portal-design
description: "Design or refine CBVMS portal navigation, forms, tables, dashboards or responsive/accessibility behavior. Specialist/on-demand; skip backend, database, vision and infrastructure work."
---

# Consistent portal design

Inspect the changed surface first. Native portal tokens are the `SP_*` palette in `ui/student_portal.py`, shared elements in `ui/components.py` and scroll behavior in `ui/portal_scroll.py`; the browser uses `web/style.css`, `web/index.html` and `web/app.js`. Preserve the chosen technologies and established patterns.

Match navigation, typography, spacing, cards, forms, tables, charts, quick stats and status badges to adjacent screens. Keep student status and appeal actions understandable; use text as well as color. Preserve loading/empty/error states, form labels, keyboard order, focus visibility, contrast and evidence accessibility.

For web changes check narrow and wide viewports, long names/labels, table overflow and touch targets. For native changes check window resizing, scrolling and focus without blocking Tk's event loop. Use focused visual inspection for styling; invoke workflow E2E only if navigation or meaningful interaction changed. No frontend framework migration or decorative dependencies without an explicit architectural task.
