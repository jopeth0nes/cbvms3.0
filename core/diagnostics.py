"""Small structured diagnostics; callers supply metadata, never images/embeddings."""
import json
import logging

LOG = logging.getLogger("cbvms.pipeline")


def event(name, **fields):
    LOG.info(json.dumps({"event": name, **fields}, default=str, sort_keys=True))
