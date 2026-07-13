"""FastAPI router factory for Todo Demo."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

from .config import APP_DIR, TEMPLATES_DIR
from .database import ensure_schema
from .routers.tasks import router as tasks_router_module


def create_todo_demo_router() -> APIRouter:
    ensure_schema()
    router = APIRouter(prefix="/todo")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    @router.get("")
    async def index(request: Request):
        return templates.TemplateResponse(request, "index.html", {"request": request, "title": "Todo Demo"})

    # TODO: add domain routes in routers/ and include them here
    router.include_router(tasks_router_module)
    return router
