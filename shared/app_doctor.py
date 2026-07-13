"""App Doctor — self-diagnose and repair common SHIMS vertical-app bugs.

Run from an agent tool or the REST endpoints:

    diagnose_app("todo_demo")  -> report
    repair_app("todo_demo")    -> safe fixes applied

Checks currently cover:
- Static file mount path vs. references in templates/JS/CSS.
- Missing auth router when roles exist in config.
- Auth router not wired into the app's main router.
- pytest result summary.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from .app_factory import derive_paths
from .config import ROOT_DIR

MAIN_PY = ROOT_DIR / "backend" / "app" / "main.py"


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def get_static_mount_path(app_name: str) -> str | None:
    """Return the URL path at which the app's static directory is mounted."""
    text = _read_text(MAIN_PY)
    pattern = re.compile(
        rf'app\.mount\(\s*"([^"]+)"\s*,\s*StaticFiles\(\s*directory\s*=\s*str\(\s*ROOT\s*/\s*"apps"\s*/\s*"{re.escape(app_name)}"\s*/\s*"static"\s*\)\s*\)',
        re.MULTILINE,
    )
    m = pattern.search(text)
    if m:
        return m.group(1)
    # Fallback: any mount whose StaticFiles directory points to this app.
    pattern2 = re.compile(
        rf'StaticFiles\(\s*directory\s*=\s*str\(\s*ROOT\s*/\s*"apps"\s*/\s*"{re.escape(app_name)}"\s*/\s*"static"\s*\)\s*\)',
        re.MULTILINE,
    )
    for m2 in pattern2.finditer(text):
        # Look backwards on the same line for app.mount("/..."
        line = text[:m2.end()].splitlines()[-1]
        mm = re.search(r'app\.mount\(\s*"([^"]+)"', line)
        if mm:
            return mm.group(1)
    return None


def _scan_files_for_static_refs(app_dir: Path, mount_path: str) -> list[dict[str, Any]]:
    """Find references to static URLs that do not match the real mount path."""
    issues: list[dict[str, Any]] = []
    expected = mount_path.rstrip("/") + "/"
    # Any reference that looks like /<something>-static/ but is not expected.
    ref_pattern = re.compile(r'["\']([a-zA-Z0-9_./-]*?-static/[a-zA-Z0-9_./-]+)["\']')
    for ext in ("*.html", "*.js", "*.css"):
        for path in app_dir.rglob(ext):
            text = _read_text(path)
            for m in ref_pattern.finditer(text):
                ref = m.group(1)
                if not ref.startswith(expected):
                    issues.append({
                        "type": "static_path_mismatch",
                        "file": str(path.relative_to(ROOT_DIR)).replace("\\", "/"),
                        "reference": ref,
                        "expected_prefix": expected,
                        "line": text[:m.start()].count("\n") + 1,
                    })
    return issues


def _has_auth_router(app_dir: Path) -> bool:
    return (app_dir / "routers" / "auth.py").exists()


def _auth_router_wired(app_dir: Path, app_py_text: str) -> bool:
    return "auth_router_module" in app_py_text or "routers/auth" in app_py_text


def _roles_defined(config_text: str) -> bool:
    return bool(re.search(r"DEFAULT_ROLES\s*=?\s*\[", config_text))


def run_app_tests(app_name: str) -> dict[str, Any]:
    test_file = ROOT_DIR / "tests" / f"test_{app_name}.py"
    if not test_file.exists():
        return {"ran": False, "reason": f"No test file: tests/test_{app_name}.py"}
    cmd = [sys.executable, "-m", "pytest", str(test_file), "-q", "--tb=line"]
    try:
        proc = subprocess.run(
            cmd,
            cwd=ROOT_DIR,
            capture_output=True,
            text=True,
            timeout=180,
        )
        return {
            "ran": True,
            "returncode": proc.returncode,
            "passed": proc.returncode == 0,
            "summary": (proc.stdout.splitlines()[-3:] if proc.stdout else []),
            "stderr": proc.stderr[-2000:] if proc.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {"ran": False, "reason": "pytest timed out after 180s"}
    except Exception as exc:
        return {"ran": False, "reason": f"pytest error: {exc}"}


def diagnose_app(app_name: str) -> dict[str, Any]:
    """Return a structured diagnosis report for a SHIMS vertical app."""
    paths = derive_paths(app_name)
    app_dir = paths["app_dir"]
    if not app_dir.exists():
        return {"app_name": app_name, "error": f"App directory not found: {app_dir}"}

    issues: list[dict[str, Any]] = []

    mount_path = get_static_mount_path(app_name)
    if not mount_path:
        issues.append({
            "type": "missing_static_mount",
            "detail": f"backend/app/main.py does not mount apps/{app_name}/static.",
            "fix": "Add an app.mount(...) line for this app's static directory.",
        })
    else:
        issues.extend(_scan_files_for_static_refs(app_dir, mount_path))

    config_text = _read_text(app_dir / "config.py")
    app_py_text = _read_text(app_dir / "app.py")
    if _roles_defined(config_text):
        if not _has_auth_router(app_dir):
            issues.append({
                "type": "missing_auth_router",
                "detail": "DEFAULT_ROLES are defined but routers/auth.py is missing.",
                "fix": "Create routers/auth.py and wire it into app.py.",
            })
        elif not _auth_router_wired(app_dir, app_py_text):
            issues.append({
                "type": "auth_router_not_wired",
                "detail": "routers/auth.py exists but is not included in app.py.",
                "fix": "Include the auth router in create_<app>_router() before domain routers.",
            })

    tests = run_app_tests(app_name)
    if tests["ran"] and not tests["passed"]:
        issues.append({
            "type": "tests_failing",
            "detail": "pytest reported failures.",
            "summary": tests["summary"],
        })

    return {
        "app_name": app_name,
        "app_dir": str(app_dir.relative_to(ROOT_DIR)).replace("\\", "/"),
        "static_mount_path": mount_path,
        "issues": issues,
        "tests": tests,
        "healthy": len(issues) == 0 and tests.get("passed", False),
    }


def _fix_static_refs(app_dir: Path, mount_path: str) -> list[dict[str, Any]]:
    """Replace every wrong -static/ reference with the correct mount path."""
    fixes = []
    expected = mount_path.rstrip("/") + "/"
    ref_pattern = re.compile(r'(["\'])([a-zA-Z0-9_./-]*?-static/[a-zA-Z0-9_./-]+)\1')
    for ext in ("*.html", "*.js", "*.css"):
        for path in app_dir.rglob(ext):
            text = _read_text(path)
            new_text, count = ref_pattern.subn(
                lambda m: m.group(1) + expected + m.group(2).split("-static/", 1)[1] + m.group(1),
                text,
            )
            if count and new_text != text:
                path.write_text(new_text, encoding="utf-8")
                fixes.append({"file": str(path.relative_to(ROOT_DIR)).replace("\\", "/"), "replacements": count})
    return fixes


def _generate_auth_router(app_dir: Path) -> None:
    """Create a minimal but functional auth router for the app."""
    router_file = app_dir / "routers" / "auth.py"
    router_file.parent.mkdir(parents=True, exist_ok=True)
    router_file.write_text(
        '''from fastapi import APIRouter, Form, Header, HTTPException
from ..database import get_db
from ..config import DEFAULT_ROLES
import hashlib, time

router = APIRouter()
_sessions: dict = {}


def _hash(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


@router.post("/auth/token")
def auth_token(username: str = Form(...), password: str = Form(...)):
    user = next((u for u in DEFAULT_ROLES if u["username"] == username), None)
    if not user or user.get("password") != password:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = hashlib.sha256(f"{username}{time.time()}".encode()).hexdigest()
    _sessions[token] = {"username": username, "role": user.get("role", "user")}
    return {"access_token": token, "token_type": "bearer", "role": user.get("role", "user")}


async def require_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization[7:]
    user = _sessions.get(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user
''',
        encoding="utf-8",
    )


def _wire_auth_router(app_dir: Path, app_py_path: Path) -> bool:
    text = _read_text(app_py_path)
    if "auth_router_module" in text or "routers/auth" in text:
        return False
    # Insert import and include near other router imports/includes.
    import_line = "from .routers.auth import router as auth_router_module\n"
    if "from .routers.students import router as students_router_module" in text:
        text = text.replace(
            "from .routers.students import router as students_router_module\n",
            import_line + "from .routers.students import router as students_router_module\n",
        )
    elif "# TODO: add domain routes" in text:
        text = text.replace("# TODO: add domain routes", import_line.strip() + "\n    # TODO: add domain routes")
    else:
        text = import_line + text

    if "router.include_router(auth_router_module)" not in text:
        text = text.replace(
            "router.include_router(students_router_module)",
            "router.include_router(auth_router_module)\n    router.include_router(students_router_module)",
        )
    app_py_path.write_text(text, encoding="utf-8")
    return True


def repair_app(app_name: str) -> dict[str, Any]:
    """Apply safe automatic fixes to a SHIMS vertical app."""
    paths = derive_paths(app_name)
    app_dir = paths["app_dir"]
    if not app_dir.exists():
        return {"app_name": app_name, "error": f"App directory not found: {app_dir}"}

    report = {"fixed": [], "skipped": [], "tests": {}}
    mount_path = get_static_mount_path(app_name)

    if mount_path:
        static_fixes = _fix_static_refs(app_dir, mount_path)
        if static_fixes:
            report["fixed"].append({"type": "static_path_mismatch", "files": static_fixes})
        else:
            report["skipped"].append("static_path_mismatch")
    else:
        report["skipped"].append({"type": "missing_static_mount", "reason": "Cannot auto-fix backend/app/main.py mount"})

    config_text = _read_text(app_dir / "config.py")
    app_py_path = app_dir / "app.py"
    if _roles_defined(config_text):
        if not _has_auth_router(app_dir):
            _generate_auth_router(app_dir)
            report["fixed"].append({"type": "missing_auth_router", "file": "routers/auth.py"})
        elif not _auth_router_wired(app_dir, _read_text(app_py_path)):
            if _wire_auth_router(app_dir, app_py_path):
                report["fixed"].append({"type": "auth_router_not_wired", "file": "app.py"})
        else:
            report["skipped"].append("auth_router")
    else:
        report["skipped"].append("auth_router (no roles defined)")

    report["tests"] = run_app_tests(app_name)
    return report
