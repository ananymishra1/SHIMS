from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from .settings import BASE_DIR, settings

BLOCKED_EXT = {'.env', '.db', '.sqlite', '.sqlite3', '.pem', '.key', '.crt', '.pfx'}
ALLOWED_EXT = {'.py', '.html', '.css', '.js', '.md', '.txt', '.json', '.yml', '.yaml'}


def _normalize(rel: str) -> Path:
    rel = rel.replace('\\', '/').lstrip('/')
    p = Path(rel)
    if '..' in p.parts or p.is_absolute():
        raise ValueError('Unsafe path')
    return p


def is_allowed(rel: str) -> bool:
    try:
        p = _normalize(rel)
    except ValueError:
        return False
    s = p.as_posix()
    if p.suffix.lower() in BLOCKED_EXT or p.suffix.lower() not in ALLOWED_EXT:
        return False
    return any(s == a or s.startswith(a.rstrip('/') + '/') for a in settings.allowed_paths)


def backup_path(path: Path, backup_root: Path) -> None:
    if path.exists():
        rel = path.relative_to(BASE_DIR)
        dest = backup_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)


def validate_code() -> Dict[str, Any]:
    res = subprocess.run([sys.executable, '-m', 'compileall', 'shims_core', 'apps', 'tests'], cwd=str(BASE_DIR), capture_output=True, text=True, timeout=30)
    return {'ok': res.returncode == 0, 'stdout': res.stdout[-4000:], 'stderr': res.stderr[-4000:]}


def propose(goal: str) -> Dict[str, Any]:
    ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    path = f'docs/evolution_proposals/{ts}_proposal.md'
    content = f"""# SHIMS Evolution Proposal\n\nGoal: {goal}\n\nThis proposal was staged by the guarded self-evolution engine.\n\nRecommended workflow:\n1. Convert this goal into small file changes.\n2. Apply only inside approved paths.\n3. Run validation.\n4. Keep human review before production use.\n"""
    return {'goal': goal, 'files': [{'path': path, 'content': content}]}


def apply_changes(files: List[Dict[str, str]], apply: bool = False) -> Dict[str, Any]:
    errors = [f.get('path', '') for f in files if not is_allowed(f.get('path', ''))]
    if errors:
        return {'ok': False, 'error': 'Blocked unsafe or disallowed paths', 'paths': errors}
    stage_root = BASE_DIR / 'data' / 'evolution_staging'
    stage_root.mkdir(parents=True, exist_ok=True)
    if not apply or not settings.self_evolution_enabled:
        staged = []
        for f in files:
            dest = stage_root / _normalize(f['path'])
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(f.get('content', ''), encoding='utf-8')
            staged.append(str(dest))
        return {'ok': True, 'mode': 'staged', 'files': staged}
    stamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    backup_root = BASE_DIR / 'data' / 'evolution_backups' / stamp
    backup_root.mkdir(parents=True, exist_ok=True)
    changed = []
    try:
        for f in files:
            target = BASE_DIR / _normalize(f['path'])
            backup_path(target, backup_root)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f.get('content', ''), encoding='utf-8')
            changed.append(str(target))
        validation = validate_code()
        if not validation['ok']:
            raise RuntimeError(validation['stderr'] or validation['stdout'])
        return {'ok': True, 'mode': 'applied', 'backup': str(backup_root), 'changed': changed, 'validation': validation}
    except Exception as exc:
        for f in files:
            target = BASE_DIR / _normalize(f['path'])
            backup = backup_root / _normalize(f['path'])
            if backup.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup, target)
        return {'ok': False, 'mode': 'rolled_back', 'backup': str(backup_root), 'error': str(exc)}
