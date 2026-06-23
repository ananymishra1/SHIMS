"""Seed the App Factory corpus: skills + omni-brain memories."""
from __future__ import annotations

from shared import skills
from shared.omni_brain import remember

SKILL_SPECS = [
    {
        "name": "SHIMS App Factory Overview",
        "summary": "How SHIMS builds a self-contained vertical FastAPI app from a domain brief.",
        "tags": ["app_factory", "vertical_app", "architecture"],
        "body": """A SHIMS vertical app lives under apps/<app_name>/ and is a self-contained FastAPI app mounted into the main backend.

Standard layout:
- apps/<app_name>/app.py          : create_<app>_router(prefix="/<prefix>") factory
- apps/<app_name>/config.py       : paths, default roles, AI model env vars
- apps/<app_name>/database.py     : SQLite get_db(), ensure_schema(), query helpers
- apps/<app_name>/services/       : pure Python domain logic (no FastAPI imports)
- apps/<app_name>/routers/        : optional route modules imported by app.py
- apps/<app_name>/templates/      : Jinja2 base.html + index.html
- apps/<app_name>/static/css|js   : vanilla CSS/JS, mounted at /<app_name>-static
- tests/test_<app_name>.py        : TestClient end-to-end tests

The database is separate: storage/<app_name>.sqlite3 (WAL mode, foreign keys) so the app is portable and does not pollute SHIMS core tables.

Integration steps:
1. Create the app folder and skeleton with shared.app_factory.ensure_app_package(name) and create_app_template_files(name, prefix=..., title=...).
2. Add domain schema in database.py ensure_schema().
3. Add services for CRUD, search, and AI wrappers.
4. Add routers and include them in app.py.
5. Mount static files and include the router in backend/app/main.py.
6. Add a launcher tile in frontend/shims_omni.html.
7. Write tests and run pytest tests/test_<app_name>.py.

Always keep routes returning {"ok": True, ...} or raising HTTPException. Use Pydantic request models where helpful.""",
    },
    {
        "name": "SHIMS App Factory Router Factory",
        "summary": "Boilerplate for creating a mountable FastAPI router for a SHIMS vertical app.",
        "tags": ["app_factory", "router", "fastapi"],
        "body": """Router factory pattern from apps/jk_hospital/app.py:

```python
from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from .database import ensure_schema
from .config import TEMPLATES_DIR

def create_<app>_router() -> APIRouter:
    ensure_schema()                     # idempotent DDL
    router = APIRouter(prefix="/<prefix>")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    @router.get("")
    async def index(request: Request):
        return templates.TemplateResponse(request, "index.html", {"request": request})

    # include domain routers:
    # from .routers import patients
    # router.include_router(patients.router)
    return router
```

In backend/app/main.py:
```python
from apps.<app>.app import create_<app>_router
app.mount("/<app>-static", StaticFiles(directory="apps/<app>/static"), name="<app>-static")
app.include_router(create_<app>_router())
```

Use APIRouter.include_router to split large apps into routers/<domain>.py modules.""",
    },
    {
        "name": "SHIMS App Factory SQLite Layer",
        "summary": "Standard SQLite schema + query helper pattern for vertical apps.",
        "tags": ["app_factory", "sqlite", "database"],
        "body": """Pattern from apps/jk_hospital/database.py:

```python
import sqlite3
from contextlib import contextmanager
from .config import DB_PATH

@contextmanager
def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()

def ensure_schema() -> None:
    with get_db() as con:
        con.execute("CREATE TABLE IF NOT EXISTS patients (...)")
        # add more tables...

def query_one(sql, params=()):
    with get_db() as con:
        return con.execute(sql, params).fetchone()

def query_all(sql, params=()):
    with get_db() as con:
        return con.execute(sql, params).fetchall()

def execute(sql, params=()):
    with get_db() as con:
        con.execute(sql, params)

def insert(sql, params=()):
    with get_db() as con:
        return con.execute(sql, params).lastrowid
```

Keep DDL idempotent with CREATE TABLE IF NOT EXISTS. Separate DB per app: storage/<app>.sqlite3.""",
    },
    {
        "name": "SHIMS App Factory Service Layer",
        "summary": "Domain services are pure Python functions that wrap the SQLite helpers.",
        "tags": ["app_factory", "service_layer", "domain_logic"],
        "body": """Pattern from apps/jk_hospital/services/:

- Do NOT import FastAPI in services/*.py.
- Accept plain dicts / primitive args and return plain dicts.
- Use the database helpers from ..database.
- Keep validation minimal; richer validation goes in routers/Pydantic models.
- AI wrappers go in services/ai.py and call shared.ai.ask_ai with JSON-output prompts.

Example:
```python
from ..database import insert, query_one

def create_patient(data: dict) -> dict:
    pid = insert(
        "INSERT INTO patients(name, phone, created_at) VALUES (?, ?, ?)",
        (data["name"], data.get("phone"), time.time()),
    )
    return dict(query_one("SELECT * FROM patients WHERE id=?", (pid,)))
```

Group services by domain: patients.py, lab.py, rooms.py, ot.py, ivf.py, ai.py, demo.py.""",
    },
    {
        "name": "SHIMS App Factory Templates & Static",
        "summary": "Jinja2 base template + vanilla JS/CSS convention for vertical apps.",
        "tags": ["app_factory", "frontend", "templates", "static"],
        "body": """Templates layout (apps/<app>/templates/):
- base.html: HTML5 shell with {% block title %}, {% block content %}, {% block scripts %}.
- index.html: {% extends "base.html" %} with the SPA or dashboard content.

Static files (apps/<app>/static/):
- css/<app>.css
- js/<app>.js

Mount in backend/app/main.py:
```python
app.mount("/<app>-static", StaticFiles(directory="apps/<app>/static"), name="<app>-static")
```

Use fetch() to talk to the app's own /api/* endpoints. Keep the UI self-contained; add a launcher tile in frontend/shims_omni.html.""",
    },
    {
        "name": "SHIMS App Factory Test Scaffold",
        "summary": "End-to-end TestClient scaffold for vertical apps.",
        "tags": ["app_factory", "tests", "pytest"],
        "body": """Pattern from tests/test_jk_hospital.py:

```python
from fastapi.testclient import TestClient
from backend.app.main import app

def test_status():
    client = TestClient(app)
    res = client.get("/<prefix>")
    assert res.status_code == 200

def test_create_entity():
    client = TestClient(app)
    res = client.post("/<prefix>/api/entities", json={"name": "x"})
    assert res.status_code == 200
    assert res.json()["ok"]
```

Use TestClient against the real app. Test full flows: create, read, update, search. Keep tests idempotent; use demo fixtures when needed.""",
    },
    {
        "name": "SHIMS App Factory Omni Launch",
        "summary": "How to expose a new vertical app through SHIMS Omni.",
        "tags": ["app_factory", "omni", "launcher"],
        "body": """To make a vertical app reachable from SHIMS Omni:

1. In backend/app/main.py import and mount:
```python
from apps.<app>.app import create_<app>_router
app.mount("/<app>-static", StaticFiles(directory=str(ROOT / "apps" / "<app>" / "static")), name="<app>-static")
app.include_router(create_<app>_router())
```

2. In frontend/shims_omni.html add a launcher tile in the modules / left panel:
```html
<div class="nav-row" onclick="window.open('/<prefix>','_blank')" style="cursor:pointer">
  <span>🏫</span>App Name
</div>
```

3. Restart the backend and confirm /health lists the app or the route responds.
4. Optional: add a right-sidebar Units panel for central switching.""",
    },
    {
        "name": "SHIMS App Factory Voice & AI",
        "summary": "Plugging voice entry and AI analysis into a vertical app.",
        "tags": ["app_factory", "voice", "ai"],
        "body": """Voice entry:
- Use faster-whisper via services/voice.py.
- Endpoint: POST /<prefix>/api/voice/transcribe with UploadFile.
- Auto-detect Hindi/English; pass through shared.stt_corrector for medical/school terminology.

AI analysis:
- services/ai.py wraps shared.ai.ask_ai with system prompts that request JSON.
- Provide a helper _extract_json() to parse the response robustly.
- Example endpoints:
  - /<prefix>/api/ai/diagnose  → returns differential + suggested tests
  - /<prefix>/api/ai/suggest   → returns treatment / next steps
  - /<prefix>/api/ai/summarize → returns a patient/student summary

Save durable insights to omni-brain under namespace=<app_name> so SHIMS can recall them later.""",
    },
]

MEMORY_SEEDS = [
    (
        "app_factory",
        "vertical app checklist",
        "SHIMS vertical app checklist: app.py, config.py, database.py, services/, routers/, templates/, static/, tests/test_<app>.py. Mount static at /<app>-static and router prefix at /<prefix>. Add Omni launcher tile in frontend/shims_omni.html.",
        ["app_factory", "checklist"],
    ),
    (
        "app_factory",
        "how to add a new app",
        "To add a new app in SHIMS: create apps/<app_name>/, write database.py with ensure_schema(), write services/, write routers/, assemble in app.py create_<app>_router(), mount static and include router in backend/app/main.py, add a tile in frontend/shims_omni.html, write tests/test_<app>.py, run pytest.",
        ["app_factory", "howto"],
    ),
    (
        "app_factory",
        "example hospital app",
        "J K Hospital is the canonical example of a SHIMS vertical app. It uses apps/jk_hospital/app.py with prefix /hospital, storage/hospital.sqlite3, services for patients/lab/rooms/ot/ivf/ai/voice, and a launcher tile in frontend/shims_omni.html.",
        ["app_factory", "example", "hospital"],
    ),
    (
        "app_factory",
        "shared.app_factory helpers",
        "shared.app_factory provides ensure_app_package(name), create_app_template_files(name, prefix, title), mount_app_static(app, name), register_app_router(app, router), and derive_paths(name). Use these to scaffold new apps deterministically.",
        ["app_factory", "helpers"],
    ),
    (
        "jk_hospital",
        "hospital app purpose",
        "J K Hospital app manages patients end-to-end: registration, OPD, diagnosis, treatment, lab, IPD beds, OT planning, IVF cycles, voice entry, AI suggestions, reminders.",
        ["hospital", "overview"],
    ),
    (
        "stanford_school",
        "school app brief",
        "Stanford International School app needs students, admissions, attendance, exams, grades, fees, transport, library, parent portal, AI insights on student performance.",
        ["school", "brief"],
    ),
]


def main() -> None:
    for spec in SKILL_SPECS:
        skills.save_skill(
            name=spec["name"],
            summary=spec["summary"],
            body=spec["body"],
            tags=spec["tags"],
            pinned=True,
            weight=1.5,
            source="app_factory_seed",
        )
        print("skill:", spec["name"])

    for namespace, key, value, tags in MEMORY_SEEDS:
        remember(
            namespace=namespace,
            key=key,
            value=value,
            tags=tags,
            pinned=True,
            weight=1.5,
            source="app_factory_seed",
        )
        print("memory:", namespace, key)


if __name__ == "__main__":
    main()
