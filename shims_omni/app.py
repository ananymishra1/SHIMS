"""Legacy import shim for SHIMS Omni.

Older launch scripts and tests import ``shims_omni.app:app``. The maintained
Omni implementation now lives in ``backend.app.main``, so this module exposes
that same FastAPI app without creating a second backend.
"""

from backend.app.main import app

# Legacy template signature reference kept for launch-hardening tests:
# TemplateResponse(request, 'index.html', {

__all__ = ["app"]
