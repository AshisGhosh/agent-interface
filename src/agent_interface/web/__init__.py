"""FastAPI web surface for the orchestrator.

Exposes the orchestrator core over HTTP for the web UI. The actual app
factory lives in `app.py`; this package re-exports `create_app` for
callers.
"""

from agent_interface.web.app import create_app

__all__ = ["create_app"]
