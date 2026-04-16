"""FastAPI web surface for the orchestrator.

Exposes the orchestrator core over HTTP for the web UI. The actual app
factory lives in `app.py`; this package re-exports `create_app` for
callers.
"""

import os

from agent_interface.web.app import create_app, mount_static_export


def create_app_from_env():
    """Uvicorn-reload entrypoint: pick up `AGI_STATIC_DIR` on each reload."""
    static_dir = os.environ.get("AGI_STATIC_DIR") or None
    return create_app(static_dir=static_dir)


__all__ = ["create_app", "create_app_from_env", "mount_static_export"]
