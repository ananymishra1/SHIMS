"""Enhanced Coder Playground v2 — Full IDE capabilities for SHIMS Omni.

Features:
- Multi-language execution (Python, Node.js, Bash, Go, Rust)
- Git integration (init, commit, diff, log, branch)
- Terminal session management
- ZIP import/export
- Folder upload
- Permission system for sensitive operations
- Project templates (React, FastAPI, CLI, etc.)
- Enhanced AI context with file tree + @file mentions
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .config import ROOT_DIR, STORAGE_DIR

CODER_DIR = STORAGE_DIR / "coder"
CODER_DIR.mkdir(parents=True, exist_ok=True)
TERMINAL_DIR = STORAGE_DIR / "coder_terminals"
TERMINAL_DIR.mkdir(parents=True, exist_ok=True)

# Language configurations
LANGUAGE_CONFIG = {
    "python": {
        "ext": ".py",
        "run_cmd": ["{python}", "{file}"],
        "entry_patterns": ["main.py", "app.py", "run.py", "index.py"],
        "interpreter": "python",
    },
    "javascript": {
        "ext": ".js",
        "run_cmd": ["node", "{file}"],
        "entry_patterns": ["index.js", "main.js", "app.js", "server.js"],
        "interpreter": "node",
    },
    "typescript": {
        "ext": ".ts",
        "run_cmd": ["npx", "tsx", "{file}"],
        "entry_patterns": ["index.ts", "main.ts", "app.ts", "server.ts"],
        "interpreter": "npx tsx",
    },
    "bash": {
        "ext": ".sh",
        "run_cmd": ["bash", "{file}"],
        "entry_patterns": ["run.sh", "main.sh", "start.sh"],
        "interpreter": "bash",
    },
    "go": {
        "ext": ".go",
        "run_cmd": ["go", "run", "{file}"],
        "entry_patterns": ["main.go", "app.go"],
        "interpreter": "go",
    },
    "rust": {
        "ext": ".rs",
        "run_cmd": ["cargo", "run"],
        "entry_patterns": ["main.rs"],
        "interpreter": "cargo",
        "needs_manifest": True,
    },
}

# Project templates
TEMPLATES: dict[str, dict[str, Any]] = {
    "python_cli": {
        "name": "Python CLI Tool",
        "files": {
            "main.py": '#!/usr/bin/env python3\n"""{name} - CLI tool"""\n\nimport argparse\n\ndef main():\n    parser = argparse.ArgumentParser(description="{name}")\n    parser.add_argument("--input", "-i", help="Input file")\n    args = parser.parse_args()\n    \n    print(f"Hello from {name}!")\n    if args.input:\n        print(f"Input: {args.input}")\n\nif __name__ == "__main__":\n    main()\n',
            "requirements.txt": "",
            "README.md": "# {name}\n\nA Python CLI tool.\n\n## Usage\n\n```bash\npython main.py --help\n```\n",
        },
    },
    "python_fastapi": {
        "name": "FastAPI Web App",
        "files": {
            "main.py": 'from fastapi import FastAPI\n\napp = FastAPI(title="{name}")\n\n@app.get("/")\nasync def root():\n    return {"message": "Hello from {name}!"}\n\n@app.get("/health")\nasync def health():\n    return {"status": "ok"}\n',
            "requirements.txt": "fastapi\nuvicorn[standard]\n",
            "README.md": "# {name}\n\nA FastAPI web application.\n\n## Run\n\n```bash\nuvicorn main:app --reload\n```\n",
        },
    },
    "react_vite": {
        "name": "React + Vite App",
        "files": {
            "package.json": '{\n  "name": "{slug}",\n  "private": true,\n  "version": "0.0.1",\n  "type": "module",\n  "scripts": {\n    "dev": "vite",\n    "build": "vite build",\n    "preview": "vite preview"\n  },\n  "dependencies": {\n    "react": "^18.3.1",\n    "react-dom": "^18.3.1"\n  },\n  "devDependencies": {\n    "@types/react": "^18.3.3",\n    "@types/react-dom": "^18.3.0",\n    "@vitejs/plugin-react": "^4.3.1",\n    "vite": "^5.3.4"\n  }\n}\n',
            "index.html": '<!doctype html>\n<html lang="en">\n  <head>\n    <meta charset="UTF-8" />\n    <meta name="viewport" content="width=device-width, initial-scale=1.0" />\n    <title>{name}</title>\n  </head>\n  <body>\n    <div id="root"></div>\n    <script type="module" src="/src/main.jsx"></script>\n  </body>\n</html>\n',
            "src/main.jsx": 'import React from "react"\nimport ReactDOM from "react-dom/client"\nimport App from "./App.jsx"\n\nReactDOM.createRoot(document.getElementById("root")).render(\n  <React.StrictMode>\n    <App />\n  </React.StrictMode>\n)\n',
            "src/App.jsx": 'import { useState } from "react"\n\nfunction App() {\n  const [count, setCount] = useState(0)\n\n  return (\n    <div style={{ padding: "2rem", fontFamily: "system-ui" }}>\n      <h1>{name}</h1>\n      <button onClick={() => setCount(c => c + 1)}>\n        Count is {count}\n      </button>\n    </div>\n  )\n}\n\nexport default App\n',
            "vite.config.js": 'import { defineConfig } from "vite"\nimport react from "@vitejs/plugin-react"\n\nexport default defineConfig({\n  plugins: [react()],\n  server: { port: 5173 }\n})\n',
            "README.md": "# {name}\n\nA React + Vite application.\n\n## Setup\n\n```bash\nnpm install\nnpm run dev\n```\n",
        },
    },
    "node_express": {
        "name": "Node.js Express API",
        "files": {
            "package.json": '{\n  "name": "{slug}",\n  "version": "1.0.0",\n  "main": "index.js",\n  "scripts": {\n    "start": "node index.js",\n    "dev": "nodemon index.js"\n  },\n  "dependencies": {\n    "express": "^4.19.2"\n  },\n  "devDependencies": {\n    "nodemon": "^3.1.4"\n  }\n}\n',
            "index.js": 'const express = require("express")\nconst app = express()\nconst PORT = process.env.PORT || 3000\n\napp.use(express.json())\n\napp.get("/", (req, res) => {\n  res.json({ message: "Hello from {name}!" })\n})\n\napp.get("/health", (req, res) => {\n  res.json({ status: "ok" })\n})\n\napp.listen(PORT, () => {\n  console.log(`Server running on port ${PORT}`)\n})\n',
            "README.md": "# {name}\n\nA Node.js Express API.\n\n## Run\n\n```bash\nnpm install\nnpm start\n```\n",
        },
    },
    "go_cli": {
        "name": "Go CLI Tool",
        "files": {
            "go.mod": "module {slug}\n\ngo 1.22\n",
            "main.go": 'package main\n\nimport (\n\t"fmt"\n\t"flag"\n)\n\nfunc main() {\n\tname := flag.String("name", "World", "Name to greet")\n\tflag.Parse()\n\tfmt.Printf("Hello, %s!\\n", *name)\n}\n',
            "README.md": "# {name}\n\nA Go CLI tool.\n\n## Run\n\n```bash\ngo run main.go\n```\n",
        },
    },
}


# Permission system
@dataclass
class CoderPermission:
    can_edit_any_file: bool = True
    can_delete_any_file: bool = True
    can_run_code: bool = True
    can_install_packages: bool = True
    can_access_terminal: bool = True
    can_use_git: bool = True
    can_export_project: bool = True
    can_import_project: bool = True
    can_modify_shims_source: bool = False  # Requires self.patch approval
    max_project_size_mb: int = 100
    allowed_languages: list[str] = field(default_factory=lambda: list(LANGUAGE_CONFIG.keys()))


def _get_settings() -> dict[str, Any]:
    path = STORAGE_DIR / "coder_settings.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_settings(settings: dict[str, Any]) -> None:
    path = STORAGE_DIR / "coder_settings.json"
    path.write_text(json.dumps(settings, indent=2, default=str), encoding="utf-8")


def _project_path(project_id: str) -> Path:
    return CODER_DIR / project_id


def _project_meta_path(project_id: str) -> Path:
    return _project_path(project_id) / "_project.json"


def create_project(name: str, template: str | None = None) -> dict[str, Any]:
    """Create a new coder project, optionally from a template."""
    project_id = str(uuid.uuid4())[:8]
    project_dir = _project_path(project_id)
    project_dir.mkdir(parents=True, exist_ok=True)

    meta = {
        "id": project_id,
        "name": name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "language": "python",
        "entry_file": "main.py",
        "template": template,
    }

    if template and template in TEMPLATES:
        tmpl = TEMPLATES[template]
        slug = re.sub(r"[^a-z0-9_-]", "-", name.lower()).strip("-")[:30] or "project"
        for rel_path, content in tmpl["files"].items():
            file_path = project_dir / rel_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content.replace("{name}", name).replace("{slug}", slug), encoding="utf-8")
        # Detect language from template
        if template.startswith("python"):
            meta["language"] = "python"
            meta["entry_file"] = "main.py"
        elif template.startswith("react") or template.startswith("node"):
            meta["language"] = "javascript"
            meta["entry_file"] = "index.js" if template == "node_express" else "src/App.jsx"
        elif template == "go_cli":
            meta["language"] = "go"
            meta["entry_file"] = "main.go"
    else:
        # Default Python starter
        (project_dir / "main.py").write_text(f'"""{name}"""\n\ndef main():\n    print("Hello from {name}!")\n\nif __name__ == "__main__":\n    main()\n', encoding="utf-8")

    _project_meta_path(project_id).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return {"ok": True, "project_id": project_id, **meta}


def list_projects() -> list[dict[str, Any]]:
    projects = []
    for proj_dir in sorted(CODER_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        meta_path = proj_dir / "_project.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                meta["file_count"] = len(list(proj_dir.rglob("*"))) - 1  # exclude _project.json
                projects.append(meta)
            except Exception:
                pass
    return projects


def get_project(project_id: str) -> dict[str, Any] | None:
    meta_path = _project_meta_path(project_id)
    if not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["files"] = list_files(project_id)
        return meta
    except Exception:
        return None


# ── Phase 1.2 Soul, Brain & Swarm: recursive file listing ───────────────────

def list_files(project_id: str, subdir: str = "", *, recursive: bool = False) -> list[dict[str, Any]]:
    project_dir = _project_path(project_id)
    target = project_dir / subdir if subdir else project_dir
    if not target.exists():
        return []

    def _scan(path: Path) -> list[dict[str, Any]]:
        entries = []
        for item in sorted(path.iterdir()):
            if item.name.startswith(".") or item.name == "_project.json":
                continue
            rel = str(item.relative_to(project_dir)).replace("\\", "/")
            stat = item.stat()
            entry: dict[str, Any] = {
                "name": item.name,
                "path": rel,
                "is_dir": item.is_dir(),
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            }
            if recursive and item.is_dir():
                entry["children"] = _scan(item)
            entries.append(entry)
        return entries

    return _scan(target)


def read_file(project_id: str, file_path: str) -> dict[str, Any]:
    project_dir = _project_path(project_id)
    full_path = (project_dir / file_path).resolve()
    # Security: must be inside project
    if not str(full_path).startswith(str(project_dir.resolve())):
        return {"ok": False, "error": "Access denied"}
    if not full_path.exists():
        return {"ok": False, "error": "File not found"}
    try:
        content = full_path.read_text(encoding="utf-8", errors="replace")
        return {"ok": True, "path": file_path, "content": content, "size": len(content.encode("utf-8"))}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _sanitize_python(content: str) -> str:
    """Replace JSON literals (null, true, false) with Python equivalents.
    Skips occurrences inside string literals."""
    import re
    result = []
    i = 0
    in_str = None
    while i < len(content):
        ch = content[i]
        if in_str is None:
            if ch in ("'", '"'):
                in_str = ch
                result.append(ch)
            elif ch == '#':
                nl = content.find("\\n", i)
                if nl == -1:
                    result.append(content[i:])
                    break
                result.append(content[i:nl])
                i = nl
                continue
            else:
                m = re.match(r'\b(null|true|false)\b', content[i:])
                if m:
                    w = m.group(0)
                    result.append({"null": "None", "true": "True", "false": "False"}.get(w, w))
                    i += len(w)
                    continue
                result.append(ch)
        else:
            result.append(ch)
            if ch == '\\' and i + 1 < len(content):
                result.append(content[i + 1])
                i += 1
            elif ch == in_str:
                in_str = None
        i += 1
    return ''.join(result)


def write_file(project_id: str, file_path: str, content: str) -> dict[str, Any]:
    project_dir = _project_path(project_id)
    full_path = (project_dir / file_path).resolve()
    if not str(full_path).startswith(str(project_dir.resolve())):
        return {"ok": False, "error": "Access denied"}
    try:
        full_path.parent.mkdir(parents=True, exist_ok=True)
        if full_path.suffix.lower() == ".py":
            content = _sanitize_python(content)
        full_path.write_text(content, encoding="utf-8")
        # Update meta
        meta_path = _project_meta_path(project_id)
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["updated_at"] = datetime.now(timezone.utc).isoformat()
            meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return {"ok": True, "path": file_path}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def delete_file(project_id: str, file_path: str) -> dict[str, Any]:
    project_dir = _project_path(project_id)
    full_path = (project_dir / file_path).resolve()
    if not str(full_path).startswith(str(project_dir.resolve())):
        return {"ok": False, "error": "Access denied"}
    if not full_path.exists():
        return {"ok": False, "error": "File not found"}
    try:
        if full_path.is_dir():
            shutil.rmtree(full_path)
        else:
            full_path.unlink()
        return {"ok": True, "path": file_path}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def mkdir(project_id: str, dir_path: str) -> dict[str, Any]:
    project_dir = _project_path(project_id)
    full_path = (project_dir / dir_path).resolve()
    if not str(full_path).startswith(str(project_dir.resolve())):
        return {"ok": False, "error": "Access denied"}
    try:
        full_path.mkdir(parents=True, exist_ok=True)
        return {"ok": True, "path": dir_path}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def detect_language(project_dir: Path, entry_hint: str | None = None) -> str:
    """Auto-detect project language from files."""
    if entry_hint:
        ext = Path(entry_hint).suffix.lower()
        for lang, config in LANGUAGE_CONFIG.items():
            if config["ext"] == ext:
                return lang
    # Guess from file counts
    counts: dict[str, int] = {}
    for f in project_dir.rglob("*"):
        if f.is_file():
            ext = f.suffix.lower()
            for lang, config in LANGUAGE_CONFIG.items():
                if config["ext"] == ext:
                    counts[lang] = counts.get(lang, 0) + 1
    if counts:
        return max(counts, key=lambda k: counts[k])
    return "python"


def run_project(project_id: str, entry_file: str | None = None) -> dict[str, Any]:
    """Run a project. Supports Python, Node.js, Go, Bash."""
    project_dir = _project_path(project_id)
    meta_path = _project_meta_path(project_id)
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    language = meta.get("language", "python")

    if entry_file:
        target = project_dir / entry_file
    else:
        target = project_dir / meta.get("entry_file", "main.py")
        # Fallback: find entry file
        if not target.exists():
            config = LANGUAGE_CONFIG.get(language, LANGUAGE_CONFIG["python"])
            for pattern in config["entry_patterns"]:
                candidate = project_dir / pattern
                if candidate.exists():
                    target = candidate
                    break

    if not target.exists():
        return {"ok": False, "error": f"Entry file not found: {target.name}"}

    config = LANGUAGE_CONFIG.get(language, LANGUAGE_CONFIG["python"])

    # Build command
    cmd_template = config["run_cmd"]
    cmd = []
    python_exe = sys.executable
    for part in cmd_template:
        if part == "{python}":
            cmd.append(python_exe)
        elif part == "{file}":
            cmd.append(str(target))
        else:
            cmd.append(part)

    # Special: Rust needs to run from project dir with Cargo.toml
    if language == "rust":
        cmd = ["cargo", "run"]

    # Special: Node projects might need npm install first
    if language in ("javascript", "typescript") and (project_dir / "package.json").exists():
        if not (project_dir / "node_modules").exists():
            subprocess.run(["npm", "install"], cwd=str(project_dir), capture_output=True, timeout=120)

    try:
        result = subprocess.run(
            cmd,
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=60,
        )
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "command": " ".join(cmd),
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Execution timed out after 60 seconds"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── Git Integration ─────────────────────────────────────────────────────────

def _git(project_id: str, args: list[str]) -> dict[str, Any]:
    project_dir = _project_path(project_id)
    try:
        result = subprocess.run(
            ["git", "-C", str(project_dir)] + args,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return {
            "ok": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except FileNotFoundError:
        return {"ok": False, "error": "Git not installed"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def git_init(project_id: str) -> dict[str, Any]:
    return _git(project_id, ["init"])


def git_status(project_id: str) -> dict[str, Any]:
    return _git(project_id, ["status", "-s"])


def git_log(project_id: str, n: int = 10) -> dict[str, Any]:
    return _git(project_id, ["log", f"-{n}", "--oneline", "--decorate"])


def git_diff(project_id: str) -> dict[str, Any]:
    return _git(project_id, ["diff"])


def git_commit(project_id: str, message: str) -> dict[str, Any]:
    _git(project_id, ["add", "."])
    return _git(project_id, ["commit", "-m", message])


def git_branch(project_id: str) -> dict[str, Any]:
    return _git(project_id, ["branch", "-a"])


def git_checkout(project_id: str, branch: str) -> dict[str, Any]:
    return _git(project_id, ["checkout", branch])


# ── Terminal Sessions ───────────────────────────────────────────────────────

_active_terminals: dict[str, dict[str, Any]] = {}


def terminal_start(project_id: str, shell: str | None = None) -> dict[str, Any]:
    """Start a pseudo-terminal session for a project."""
    import pty
    import select
    import termios
    import tty

    term_id = f"{project_id}_{uuid.uuid4().hex[:6]}"
    project_dir = _project_path(project_id)

    shell_cmd = shell or os.environ.get("SHELL", "bash")
    if sys.platform == "win32":
        shell_cmd = shell or "cmd.exe"

    # For Windows, we use subprocess pipes instead of PTY
    if sys.platform == "win32":
        proc = subprocess.Popen(
            [shell_cmd],
            cwd=str(project_dir),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    else:
        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(
            [shell_cmd],
            cwd=str(project_dir),
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            preexec_fn=os.setsid,
        )
        os.close(slave_fd)
        _active_terminals[term_id] = {"proc": proc, "master_fd": master_fd, "project_id": project_id}
        return {"ok": True, "terminal_id": term_id, "shell": shell_cmd}

    _active_terminals[term_id] = {"proc": proc, "project_id": project_id}
    return {"ok": True, "terminal_id": term_id, "shell": shell_cmd}


def terminal_read(term_id: str) -> dict[str, Any]:
    """Read output from terminal (non-blocking)."""
    term = _active_terminals.get(term_id)
    if not term:
        return {"ok": False, "error": "Terminal not found"}
    proc = term["proc"]
    if proc.poll() is not None:
        return {"ok": True, "done": True, "output": "", "returncode": proc.returncode}

    output = ""
    if sys.platform != "win32":
        import select
        master_fd = term.get("master_fd")
        if master_fd:
            ready, _, _ = select.select([master_fd], [], [], 0.1)
            if ready:
                try:
                    data = os.read(master_fd, 4096)
                    output = data.decode("utf-8", errors="replace")
                except OSError:
                    pass
    else:
        # Windows: read from pipe
        try:
            output = proc.stdout.read(4096) or ""
        except Exception:
            pass

    return {"ok": True, "done": False, "output": output}


def terminal_write(term_id: str, data: str) -> dict[str, Any]:
    """Send input to terminal."""
    term = _active_terminals.get(term_id)
    if not term:
        return {"ok": False, "error": "Terminal not found"}
    proc = term["proc"]
    try:
        if sys.platform != "win32":
            master_fd = term.get("master_fd")
            if master_fd:
                os.write(master_fd, data.encode("utf-8"))
        else:
            proc.stdin.write(data)
            proc.stdin.flush()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def terminal_kill(term_id: str) -> dict[str, Any]:
    term = _active_terminals.get(term_id)
    if not term:
        return {"ok": False, "error": "Terminal not found"}
    proc = term["proc"]
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
    _active_terminals.pop(term_id, None)
    return {"ok": True}


# ── ZIP Import / Export ─────────────────────────────────────────────────────

def export_project(project_id: str) -> dict[str, Any]:
    """Export project as ZIP."""
    project_dir = _project_path(project_id)
    if not project_dir.exists():
        return {"ok": False, "error": "Project not found"}

    zip_path = STORAGE_DIR / "exports" / f"{project_id}.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in project_dir.rglob("*"):
                if file_path.is_file() and file_path.name != "_project.json":
                    arcname = str(file_path.relative_to(project_dir)).replace("\\", "/")
                    zf.write(file_path, arcname)
        return {"ok": True, "zip_path": str(zip_path), "size": zip_path.stat().st_size}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def import_project(zip_data: bytes, name: str | None = None) -> dict[str, Any]:
    """Import project from ZIP bytes."""
    project_id = str(uuid.uuid4())[:8]
    project_dir = _project_path(project_id)
    project_dir.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_data, "r") as zf:
            zf.extractall(str(project_dir))

        # Detect language
        lang = detect_language(project_dir)

        meta = {
            "id": project_id,
            "name": name or f"Imported {project_id}",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "language": lang,
            "entry_file": LANGUAGE_CONFIG.get(lang, LANGUAGE_CONFIG["python"])["entry_patterns"][0],
            "template": "imported",
        }
        _project_meta_path(project_id).write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return {"ok": True, "project_id": project_id, **meta}
    except Exception as exc:
        shutil.rmtree(project_dir, ignore_errors=True)
        return {"ok": False, "error": str(exc)}


# ── Folder Upload ───────────────────────────────────────────────────────────

# ── Phase 1.2 Soul, Brain & Swarm: upload_folder accepts list[int] from JS ─

def upload_folder(project_id: str, files: dict[str, bytes | list[int]]) -> dict[str, Any]:
    """Upload multiple files to a project. files is a dict of {relative_path: content_bytes}.
    Content may also be a list of int byte values as sent by JSON-serializing JavaScript clients."""
    project_dir = _project_path(project_id)
    written = 0
    errors = []
    for rel_path, content in files.items():
        safe_path = (project_dir / rel_path).resolve()
        if not str(safe_path).startswith(str(project_dir.resolve())):
            errors.append(f"Blocked: {rel_path}")
            continue
        try:
            safe_path.parent.mkdir(parents=True, exist_ok=True)
            # JavaScript may serialize Uint8Array as a plain array of integers.
            if isinstance(content, list):
                content = bytes(content)
            safe_path.write_bytes(content)
            written += 1
        except Exception as exc:
            errors.append(f"{rel_path}: {exc}")

    # Update meta
    meta_path = _project_meta_path(project_id)
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["updated_at"] = datetime.now(timezone.utc).isoformat()
        meta["language"] = detect_language(project_dir)
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    return {"ok": len(errors) == 0, "written": written, "errors": errors}


# ── AI Context Builder ──────────────────────────────────────────────────────

def build_ai_context(project_id: str, mention_files: list[str] | None = None) -> dict[str, Any]:
    """Build rich context for AI coding assistance."""
    project_dir = _project_path(project_id)
    if not project_dir.exists():
        return {"ok": False, "error": "Project not found"}

    # File tree
    tree = []
    for f in sorted(project_dir.rglob("*")):
        if f.name.startswith(".") or f.name == "_project.json":
            continue
        rel = str(f.relative_to(project_dir)).replace("\\", "/")
        tree.append({"path": rel, "is_dir": f.is_dir(), "size": f.stat().st_size if f.is_file() else 0})

    # Read mentioned files or important files
    file_contents = []
    files_to_read = mention_files or []
    if not files_to_read:
        # Auto-select key files (entry point, config, readme)
        for pattern in ["main.py", "app.py", "index.js", "package.json", "requirements.txt", "README.md", "go.mod", "Cargo.toml"]:
            p = project_dir / pattern
            if p.exists():
                files_to_read.append(pattern)

    for fp in files_to_read[:10]:  # max 10 files
        full = (project_dir / fp).resolve()
        if str(full).startswith(str(project_dir.resolve())) and full.is_file():
            try:
                content = full.read_text(encoding="utf-8", errors="replace")
                if len(content) > 10000:
                    content = content[:10000] + "\n... [truncated]"
                file_contents.append({"path": fp, "content": content})
            except Exception:
                pass

    return {
        "ok": True,
        "file_tree": tree,
        "files": file_contents,
        "project_dir": str(project_dir),
    }


# ── Permission System ───────────────────────────────────────────────────────

def check_permission(operation: str, user_role: str = "admin") -> bool:
    """Check if user can perform an operation."""
    perms = CoderPermission()
    if user_role in ("admin", "executive", "owner"):
        return True
    if operation == "edit" and perms.can_edit_any_file:
        return True
    if operation == "delete" and perms.can_delete_any_file:
        return True
    if operation == "run" and perms.can_run_code:
        return True
    if operation == "terminal" and perms.can_access_terminal:
        return True
    if operation == "git" and perms.can_use_git:
        return True
    if operation == "export" and perms.can_export_project:
        return True
    if operation == "import" and perms.can_import_project:
        return True
    return False


# ── Templates List ──────────────────────────────────────────────────────────

def list_templates() -> list[dict[str, Any]]:
    return [{"id": k, "name": v["name"]} for k, v in TEMPLATES.items()]
