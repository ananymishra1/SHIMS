"""FastAPI router factory for Stanford International School."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

from .config import APP_DIR, TEMPLATES_DIR
from .database import ensure_schema
from .routers.auth import router as auth_router_module
from .routers.students import router as students_router_module
from .routers.staff import router as staff_router_module
from .routers.classes import router as classes_router_module
from .routers.attendance import router as attendance_router_module
from .routers.exams import router as exams_router_module
from .routers.results import router as results_router_module
from .routers.fees import router as fees_router_module
from .routers.ai import router as ai_router_module


def create_stanford_school_router() -> APIRouter:
    ensure_schema()
    router = APIRouter(prefix="/school")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    @router.get("")
    async def index(request: Request):
        return templates.TemplateResponse(request, "index.html", {"request": request, "title": "Stanford International School"})

    # TODO: add domain routes in routers/ and include them here
    router.include_router(auth_router_module)
    router.include_router(students_router_module)
    router.include_router(staff_router_module)
    router.include_router(classes_router_module)
    router.include_router(attendance_router_module)
    router.include_router(exams_router_module)
    router.include_router(results_router_module)
    router.include_router(fees_router_module)
    router.include_router(ai_router_module)
    return router
