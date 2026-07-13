from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, Any
from .ai import generate_code_from_task

BLOCKED = ['os.system', 'subprocess.', 'socket.', 'shutil.rmtree', 'requests.', 'httpx.', 'urllib.', 'eval(', 'exec(']


def is_safe_python(code: str) -> Dict[str, Any]:
    low = code.lower()
    hits = [b for b in BLOCKED if b in low]
    return {'ok': not hits, 'blocked': hits}


async def generate_and_test(task: str, code: str = '', tests: str = '') -> Dict[str, Any]:
    code = code or await generate_code_from_task(task)
    safe = is_safe_python(code)
    if not safe['ok']:
        return {'ok': False, 'code': code, 'error': 'Blocked unsafe code pattern', 'blocked': safe['blocked']}
    with tempfile.TemporaryDirectory(prefix='shims_code_') as tmp:
        root = Path(tmp)
        solution = root / 'solution.py'
        solution.write_text(code, encoding='utf-8')
        compile_res = subprocess.run([sys.executable, '-m', 'py_compile', str(solution)], capture_output=True, text=True, timeout=10)
        result = {'ok': compile_res.returncode == 0, 'stage': 'compile', 'code': code, 'stdout': compile_res.stdout, 'stderr': compile_res.stderr}
        if compile_res.returncode != 0:
            return result
        run_res = subprocess.run([sys.executable, str(solution)], cwd=str(root), capture_output=True, text=True, timeout=10)
        result.update({'stage': 'run', 'ok': run_res.returncode == 0, 'run_stdout': run_res.stdout, 'run_stderr': run_res.stderr})
        if tests:
            test_file = root / 'test_solution.py'
            test_file.write_text(tests, encoding='utf-8')
            test_res = subprocess.run([sys.executable, str(test_file)], cwd=str(root), capture_output=True, text=True, timeout=10)
            result.update({'tests_ok': test_res.returncode == 0, 'test_stdout': test_res.stdout, 'test_stderr': test_res.stderr})
            result['ok'] = result['ok'] and result['tests_ok']
        return result
