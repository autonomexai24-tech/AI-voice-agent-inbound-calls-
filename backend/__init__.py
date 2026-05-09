"""Backend package (introduced in Phase 1).

This package houses the modular backend (db, config, utils) being built
alongside the existing runtime. It does NOT replace the existing root-level
files (agent.py, db.py, notify.py, ui_server.py, calendar_tools.py) in Phase 1.

Phase 1 scope: scaffolding only. The existing runtime continues to import
from root-level modules. Phase 2 will begin wiring callers to this package.
"""
