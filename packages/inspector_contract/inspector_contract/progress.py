"""Progress lines on the console for every agent: what a run is doing, step by step.

Standard Python logging to stdout, so `docker compose logs -f <agent>` shows it locally and a host runtime collects
it like any other output. A host that configures logging itself keeps its own setup.

Environment:
  INSPECTOR_LOG_LEVEL   INFO (default: progress lines) | DEBUG | WARNING (quiet)
"""

from __future__ import annotations

import logging
import os
import sys

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
NOISY = ("httpx", "httpcore", "botocore", "urllib3", "openai._base_client",  # one line per HTTP request otherwise
         "a2a.client", "strands.telemetry")  # the agent card on every call, metrics set-up


def setup() -> None:
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=(os.environ.get("INSPECTOR_LOG_LEVEL") or "INFO").upper(), format=FORMAT,
                            stream=sys.stdout)
    for name in NOISY:
        logging.getLogger(name).setLevel(max(logging.WARNING, root.level))
