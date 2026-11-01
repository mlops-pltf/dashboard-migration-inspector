"""Host extensions without editing the agents: modules named in INSPECTOR_PLUGINS (comma-separated) are imported
when an agent starts, and register what they bring, e.g.

    # my_project/warehouse.py, with INSPECTOR_PLUGINS=my_project.warehouse on the data agent
    from data_agent.tools.warehouse.connection import register_backend
    register_backend("redshift_data", fetch)

The same works for package readers (`register_reader`), run-folder writers (`register_writer`) or looking up the
other agents in a registry at start-up (`expert_client.register`, `data_probe.client.register`).
"""

from __future__ import annotations

import importlib
import logging
import os

log = logging.getLogger("inspector.plugins")


def load() -> list[str]:
    names = [n.strip() for n in (os.environ.get("INSPECTOR_PLUGINS") or "").split(",") if n.strip()]
    for name in names:
        importlib.import_module(name)  # a broken plug-in stops the start: better than running without it
        log.info("plug-in loaded: %s", name)
    return names
