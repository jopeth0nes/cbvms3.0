"""Small structured diagnostics; callers supply metadata, never images/embeddings."""
import json
import logging
from core.performance import PERF

LOG = logging.getLogger("cbvms.pipeline")


def event(name, **fields):
    # Numeric allowlist only: raw events can contain IDs; performance summaries cannot.
    if name == "stage_complete":
        PERF.observe(fields.get("stage"), fields.get("elapsed"))
    elif name == "frame_rejected":
        PERF.observe("rejected", 0.)
    LOG.info(json.dumps({"event": name, **fields}, default=str, sort_keys=True))
