from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any

from .config import SANDBOX_DIR, settings
from .security import new_id

ALLOWED_EXTENSIONS = {'.py', '.txt', '.md', '.json', '.yaml', '.yml'}


def safe_filename(name: str) -> str:
    candidate = Path(name).name or 'main.py'
    suffix = Path(candidate).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        candidate = Path(candidate).stem + '.py'
    return candidate


def run_python_code(code: str, filename: str = 'main.py', tests: str = '', timeout: int | None = None) -> dict[str, Any]:
    run_id = new_id('run')
    workdir = SANDBOX_DIR / run_id
    workdir.mkdir(parents=True, exist_ok=True)
    main_file = workdir / safe_filename(filename)
    main_file.write_text(code, encoding='utf-8')
    if tests.strip():
        (workdir / 'test_generated.py').write_text(tests, encoding='utf-8')
        command = [sys.executable, '-m', 'pytest', '-q', str(workdir)]
    else:
        command = [sys.executable, str(main_file)]
    env = os.environ.copy()
    env['PYTHONPATH'] = str(workdir)
    try:
        proc = subprocess.run(
            command,
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=timeout if timeout is not None else settings.code_timeout_seconds,
            env=env,
        )
        return {
            'status': 'passed' if proc.returncode == 0 else 'failed',
            'returncode': proc.returncode,
            'stdout': proc.stdout[-6000:],
            'stderr': proc.stderr[-6000:],
            'workdir': str(workdir),
        }
    except subprocess.TimeoutExpired as exc:
        return {'status': 'timeout', 'returncode': -1, 'stdout': exc.stdout or '', 'stderr': exc.stderr or 'Timed out', 'workdir': str(workdir)}


def create_code_project(files: dict[str, str]) -> dict[str, Any]:
    project_id = new_id('project')
    root = SANDBOX_DIR / project_id
    root.mkdir(parents=True, exist_ok=True)
    written = []
    for name, content in files.items():
        safe = safe_filename(name)
        path = root / safe
        path.write_text(content, encoding='utf-8')
        written.append(str(path))
    return {'status': 'created', 'project_dir': str(root), 'files': written}
