from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import ROOT_DIR

try:
    from .omni_brain import ingest_knowledge, remember, schedule_task
    from .self_evolver import list_proposals
    from .telemetry import build_daily_lessons, log_event, recent_events
except Exception:  # pragma: no cover
    ingest_knowledge = None  # type: ignore
    remember = None  # type: ignore
    schedule_task = None  # type: ignore
    list_proposals = None  # type: ignore
    build_daily_lessons = None  # type: ignore
    recent_events = None  # type: ignore

    def log_event(*args: Any, **kwargs: Any) -> None:  # type: ignore
        return None


SELF_DIR = ROOT_DIR / "data" / "state" / "self_awareness"
LATEST_JSON = SELF_DIR / "latest.json"
LATEST_MD = SELF_DIR / "latest.md"

TEXT_EXTENSIONS = {
    ".bat",
    ".cfg",
    ".css",
    ".env.example",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".kt",
    ".md",
    ".mjs",
    ".ps1",
    ".py",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}

EXCLUDED_DIRS = {
    ".android-sdk",
    ".git",
    ".gradle-cache",
    ".gradle-dist",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "site-packages",
}

EXCLUDED_FILES = {
    ".env",
}

KEY_SELF_FILES = [
    "backend/app/main.py",
    "shared/omni_brain.py",
    "shared/self_evolver.py",
    "shared/self_awareness.py",
    "shared/telemetry.py",
    "shared/search_query_planner.py",
    "frontend/js/shims_omni.js",
    "shims_enterprise/app.py",
    "README.md",
    "START_HERE.md",
    ".env.example",
    "requirements.txt",
    "tests/test_v20_omni_image_voice_realtime.py",
    "tests/test_v21_omni_llm_web_routing.py",
    "tests/test_v22_self_awareness_boot.py",
]


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT_DIR.resolve())).replace("\\", "/")


def _is_safe_text_file(path: Path) -> bool:
    try:
        rel = path.resolve().relative_to(ROOT_DIR.resolve())
    except Exception:
        return False
    parts = set(rel.parts)
    if parts & EXCLUDED_DIRS:
        return False
    if path.name in EXCLUDED_FILES:
        return False
    if path.suffix.lower() in {".sqlite", ".sqlite3", ".db", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".apk", ".exe", ".dll", ".pyc"}:
        return False
    if path.name == ".env.example":
        return True
    return path.suffix.lower() in TEXT_EXTENSIONS and path.stat().st_size <= 2_000_000


def _iter_self_files(max_files: int = 900) -> list[Path]:
    files: list[Path] = []
    for root, dirs, names in os.walk(ROOT_DIR):
        dirs[:] = [
            d
            for d in dirs
            if d not in EXCLUDED_DIRS
            and d != "__pycache__"
            and not d.startswith(".git")
            and not (d == "data" and Path(root).resolve() == ROOT_DIR.resolve())
        ]
        base = Path(root)
        for name in names:
            if len(files) >= max_files:
                return files
            path = base / name
            if not path.is_file():
                continue
            if name in EXCLUDED_FILES:
                continue
            suffix = path.suffix.lower()
            if name == ".env.example":
                pass
            elif suffix not in TEXT_EXTENSIONS:
                continue
            try:
                if path.stat().st_size > 2_000_000:
                    continue
            except Exception:
                continue
            files.append(path)
    return files


def _find_key_self_files() -> list[Path]:
    found: list[Path] = []
    for rel in KEY_SELF_FILES:
        path = ROOT_DIR / rel
        if path.exists() and path.is_file():
            found.append(path)
    return found


def _merge_key_files(files: list[Path]) -> list[Path]:
    seen = {p.resolve() for p in files}
    for path in _find_key_self_files():
        try:
            resolved = path.resolve()
            if resolved not in seen and _is_safe_text_file(path):
                files.append(path)
                seen.add(resolved)
        except Exception:
            continue
    return files


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _file_record(path: Path) -> dict[str, Any]:
    stat = path.stat()
    text = path.read_text(encoding="utf-8", errors="ignore")
    return {
        "path": _rel(path),
        "suffix": path.suffix.lower() or path.name,
        "size_bytes": int(stat.st_size),
        "line_count": text.count("\n") + (1 if text else 0),
        "sha256": _sha256_file(path),
    }


def _extract_routes(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="ignore")
    routes: list[dict[str, str]] = []
    pattern = re.compile(r"@app\.(get|post|put|delete|patch|websocket)\(\s*['\"]([^'\"]+)['\"]", re.I)
    for method, route in pattern.findall(text):
        routes.append({"method": method.upper(), "path": route, "file": _rel(path)})
    return routes


def _key_file_excerpts(limit_chars: int = 1800) -> list[dict[str, Any]]:
    excerpts: list[dict[str, Any]] = []
    for rel in KEY_SELF_FILES:
        path = ROOT_DIR / rel
        if not path.exists() or not _is_safe_text_file(path):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        excerpts.append(
            {
                "path": rel,
                "sha256": _sha256_file(path),
                "excerpt": text[:limit_chars],
            }
        )
    return excerpts


def _safe_env_status() -> dict[str, Any]:
    keys = [
        "OLLAMA_HOST",
        "SHIMS_OLLAMA_MODEL",
        "SHIMS_IMAGE_BACKEND",
        "SHIMS_ENABLE_DIFFUSERS",
        "SHIMS_DIFFUSERS_ALLOW_SLOW_CPU",
        "STABLE_DIFFUSION_URL",
        "COMFYUI_URL",
        "SHIMS_BRAIN_BACKGROUND_LEARNING",
        "SHIMS_BOOT_SELF_AWARENESS",
        "SHIMS_ALLOW_SELF_EVOLUTION",
    ]
    secretish = ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "TAVILY_API_KEY", "BRAVE_SEARCH_API_KEY", "SERPAPI_API_KEY"]
    out = {k: os.getenv(k, "") for k in keys if os.getenv(k, "")}
    out.update({k: "configured" for k in secretish if os.getenv(k)})
    return out


def _derive_gaps(files: list[dict[str, Any]], routes: list[dict[str, str]], lessons: dict[str, Any]) -> list[str]:
    gaps: list[str] = []
    paths = {f["path"] for f in files}
    if "tests/test_v21_omni_llm_web_routing.py" not in paths:
        gaps.append("Search/chat router has no v21 regression test in manifest.")
    if not os.getenv("OPENAI_API_KEY") and not os.getenv("STABLE_DIFFUSION_URL") and not os.getenv("COMFYUI_URL"):
        gaps.append("No real cloud/WebUI image provider is configured; local visual fallback will be used.")
    if os.getenv("SHIMS_ENABLE_DIFFUSERS", "false").lower() in {"1", "true", "yes", "on"} and os.getenv("SHIMS_DIFFUSERS_ALLOW_SLOW_CPU", "false").lower() not in {"1", "true", "yes", "on"}:
        gaps.append("Diffusers is enabled but CPU SDXL is guarded; use CUDA/WebUI/OpenAI for real image generation.")
    if lessons.get("error_count", 0):
        gaps.append(f"Recent telemetry includes {lessons.get('error_count')} error events; review repeated routes before proposing patches.")
    if not any(r["path"] == "/self/status" for r in routes):
        gaps.append("Self-awareness status endpoint is not exposed yet.")
    return gaps[:12]


def build_self_model(*, app_name: str = "SHIMS", app_version: str = "", max_files: int = 900) -> dict[str, Any]:
    started = time.perf_counter()
    files = [_file_record(p) for p in _merge_key_files(_iter_self_files(max_files=max_files))]
    ext_counts = Counter(f["suffix"] for f in files)
    top_dirs = Counter((f["path"].split("/", 1)[0] if "/" in f["path"] else ".") for f in files)
    backend_routes = _extract_routes(ROOT_DIR / "backend" / "app" / "main.py")
    enterprise_routes = _extract_routes(ROOT_DIR / "shims_enterprise" / "app.py")
    routes = backend_routes + enterprise_routes
    lessons = build_daily_lessons(limit=500) if build_daily_lessons else {}
    proposals = list_proposals(limit=25) if list_proposals else []
    tests = [f for f in files if f["path"].startswith("tests/") and f["path"].endswith(".py")]
    key_files = _key_file_excerpts()
    boot_id = hashlib.sha256(f"{_utc()}:{len(files)}:{time.perf_counter()}".encode("utf-8")).hexdigest()[:16]
    model = {
        "ok": True,
        "boot_id": boot_id,
        "generated_at": _utc(),
        "app": app_name,
        "version": app_version,
        "identity": {
            "claim": "SHIMS has an operational self-model, not human consciousness.",
            "capability": "It can inspect local code/config/tests/telemetry, remember findings, queue evolution notes, and propose guarded patches.",
            "safety_gate": "Live source changes require proposal, sandbox validation, and human approval before apply.",
        },
        "workspace": {
            "root": str(ROOT_DIR),
            "scanned_files": len(files),
            "scanned_bytes": sum(int(f["size_bytes"]) for f in files),
            "extension_counts": dict(ext_counts.most_common(20)),
            "top_directories": dict(top_dirs.most_common(20)),
        },
        "routes": {
            "count": len(routes),
            "backend_count": len(backend_routes),
            "enterprise_count": len(enterprise_routes),
            "sample": routes[:80],
        },
        "tests": {
            "count": len(tests),
            "recent_self_tests": [f["path"] for f in tests if "self" in f["path"].lower() or "omni" in f["path"].lower()][:25],
        },
        "files": {
            "manifest_sample": sorted(files, key=lambda f: f["path"])[:200],
            "key_excerpts": key_files,
        },
        "environment": _safe_env_status(),
        "telemetry_lessons": lessons,
        "evolution": {
            "proposal_count_sample": len(proposals),
            "recent_proposals": proposals[:12],
        },
        "gaps": _derive_gaps(files, routes, lessons),
        "next_actions": [
            "Keep this boot self-model in memory and use it when users ask what SHIMS is, what it can change, or why a feature failed.",
            "For repeated failures, create a proposal with tests instead of mutating production files directly.",
            "Prefer small verified changes: inspect, patch, run tests, restart affected service, then write a note to future SHIMS.",
        ],
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
    }
    return model


def render_self_notes(model: dict[str, Any]) -> str:
    workspace = model.get("workspace") or {}
    routes = model.get("routes") or {}
    tests = model.get("tests") or {}
    identity = model.get("identity") or {}
    lines = [
        "# SHIMS Boot Self-Awareness Note",
        "",
        f"Boot ID: {model.get('boot_id')}",
        f"Generated: {model.get('generated_at')}",
        f"App: {model.get('app')} {model.get('version')}",
        "",
        "## Self-Model",
        identity.get("claim", ""),
        identity.get("capability", ""),
        identity.get("safety_gate", ""),
        "",
        "## Workspace Snapshot",
        f"- Root: {workspace.get('root')}",
        f"- Text/code/config files scanned: {workspace.get('scanned_files')}",
        f"- Scanned bytes: {workspace.get('scanned_bytes')}",
        f"- Routes discovered: {routes.get('count')} (backend {routes.get('backend_count')}, enterprise {routes.get('enterprise_count')})",
        f"- Tests discovered: {tests.get('count')}",
        "",
        "## Current Gaps",
    ]
    gaps = model.get("gaps") or []
    lines.extend([f"- {gap}" for gap in gaps] or ["- No immediate gaps detected in the boot audit."])
    lines.extend(["", "## Notes To Future SHIMS"])
    for item in model.get("next_actions") or []:
        lines.append(f"- {item}")
    lines.extend(["", "## Recent Routes Sample"])
    for item in (routes.get("sample") or [])[:25]:
        lines.append(f"- {item.get('method')} {item.get('path')} ({item.get('file')})")
    return "\n".join(line for line in lines if line is not None).strip() + "\n"


def persist_self_model(model: dict[str, Any], notes: str, *, persist_brain: bool = True) -> dict[str, Any]:
    SELF_DIR.mkdir(parents=True, exist_ok=True)
    boot_id = str(model.get("boot_id") or hashlib.sha256(notes.encode("utf-8")).hexdigest()[:16])
    json_path = SELF_DIR / f"boot_{boot_id}.json"
    md_path = SELF_DIR / f"boot_{boot_id}.md"
    json_path.write_text(json.dumps(model, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    md_path.write_text(notes, encoding="utf-8")
    LATEST_JSON.write_text(json.dumps(model, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    LATEST_MD.write_text(notes, encoding="utf-8")
    brain_result: dict[str, Any] = {}
    queued: list[dict[str, Any]] = []
    if persist_brain:
        if remember:
            brain_result["memory"] = remember(
                "system",
                "self_model_latest",
                json.dumps(
                    {
                        "boot_id": boot_id,
                        "generated_at": model.get("generated_at"),
                        "workspace": model.get("workspace"),
                        "routes": {k: model.get("routes", {}).get(k) for k in ("count", "backend_count", "enterprise_count")},
                        "tests": model.get("tests"),
                        "gaps": model.get("gaps"),
                        "identity": model.get("identity"),
                    },
                    ensure_ascii=False,
                    default=str,
                ),
                tags=["self-awareness", "boot", "system"],
                pinned=True,
                weight=2.1,
                source="boot_self_audit",
            )
        if ingest_knowledge:
            brain_result["knowledge"] = ingest_knowledge(
                f"SHIMS boot self-awareness {boot_id}",
                notes,
                source_type="self_awareness",
                source_uri=str(md_path),
                tags=["self-awareness", "boot", "evolution"],
                importance=1.9,
            )
        if schedule_task:
            queued.append(
                schedule_task(
                    "self_awareness_review",
                    f"Review boot self-model {boot_id} and convert real gaps into small tested proposals",
                    {"boot_id": boot_id, "notes_path": str(md_path), "gaps": model.get("gaps") or []},
                    priority=2,
                )
            )
            queued.append(
                schedule_task(
                    "evolution_note",
                    f"Future SHIMS note from boot {boot_id}",
                    {"boot_id": boot_id, "next_actions": model.get("next_actions") or []},
                    priority=4,
                )
            )
    log_event("self_awareness.boot", route="self:boot", provider="local", model="self-awareness", ok=True, latency_ms=float(model.get("latency_ms") or 0), message=f"boot_id={boot_id}", metadata={"model": {k: model.get(k) for k in ("boot_id", "workspace", "routes", "tests", "gaps")}, "queued": queued})
    return {"ok": True, "boot_id": boot_id, "json_path": str(json_path), "notes_path": str(md_path), "latest_json": str(LATEST_JSON), "latest_notes": str(LATEST_MD), "brain": brain_result, "queued_tasks": queued}


def run_boot_self_audit(*, app_name: str = "SHIMS", app_version: str = "", max_files: int = 900, persist_brain: bool = True) -> dict[str, Any]:
    model = build_self_model(app_name=app_name, app_version=app_version, max_files=max_files)
    notes = render_self_notes(model)
    persisted = persist_self_model(model, notes, persist_brain=persist_brain)
    return {"ok": True, "model": model, "notes": notes, **persisted}


def latest_self_model() -> dict[str, Any]:
    if not LATEST_JSON.exists():
        return {"ok": False, "message": "No boot self-awareness model has been written yet."}
    try:
        data = json.loads(LATEST_JSON.read_text(encoding="utf-8"))
        data["ok"] = True
        data["latest_json"] = str(LATEST_JSON)
        data["latest_notes"] = str(LATEST_MD)
        return data
    except Exception as exc:
        return {"ok": False, "message": f"Could not read latest self model: {exc}"}


def latest_self_notes() -> str:
    if not LATEST_MD.exists():
        return ""
    return LATEST_MD.read_text(encoding="utf-8", errors="ignore")


def self_prompt_addendum(max_chars: int = 2400) -> str:
    model = latest_self_model()
    if not model.get("ok"):
        return ""
    workspace = model.get("workspace") or {}
    routes = model.get("routes") or {}
    gaps = model.get("gaps") or []
    text = (
        "SHIMS operational self-model from latest boot audit:\n"
        f"- Boot ID: {model.get('boot_id')} generated {model.get('generated_at')}\n"
        "- Important limitation: this is operational self-awareness, not human consciousness.\n"
        f"- Scanned files: {workspace.get('scanned_files')} code/config/doc files, routes: {routes.get('count')}, tests: {(model.get('tests') or {}).get('count')}.\n"
        "- It may inspect itself and propose patches, but live changes require sandbox validation and human approval.\n"
    )
    if gaps:
        text += "- Current self-noted gaps: " + "; ".join(str(g) for g in gaps[:5]) + "\n"
    return text[:max_chars]
