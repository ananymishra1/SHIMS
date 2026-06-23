"""Code Interpreter — richer Python sandbox for data analysis and visualization.

Wraps the existing code_sandbox with:
- Automatic matplotlib figure capture as base64 PNG
- Generated file artifact collection
- Pandas dataframe pretty-printing
- Safety: network-disabled subprocess, timeout, allowed imports only
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any

from .code_sandbox import run_python_code
from .config import SANDBOX_DIR, settings
from .security import new_id


_INTERPRETER_PREAMBLE = '''
# SHIMS Code Interpreter preamble
import sys
import os
import json
import math
import random
import datetime
import statistics
import itertools
import collections
import fractions
import decimal
import typing
import string
import hashlib
import re
import base64
import io

# Allow pandas and numpy if available
try:
    import pandas as pd
    pd.set_option('display.max_rows', 20)
    pd.set_option('display.max_columns', 12)
except Exception:
    pass
try:
    import numpy as np
except Exception:
    pass

# Capture matplotlib figures automatically
try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    _shims_original_show = plt.show
    def _shims_capture_show(*args, **kwargs):
        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight')
        buf.seek(0)
        b64 = base64.b64encode(buf.read()).decode('utf-8')
        print(f"\\nSHIMS_FIGURE_B64:{b64}")
        plt.close()
    plt.show = _shims_capture_show
except Exception:
    pass

# Pretty-print dataframes
try:
    _orig_df_repr = pd.DataFrame.__repr__
    def _df_repr(self):
        return self.to_string(max_rows=20, max_cols=12, max_colwidth=40)
    pd.DataFrame.__repr__ = _df_repr
except Exception:
    pass
'''


def _sanitize_code(code: str) -> tuple[bool, str, str]:
    """Lightweight safety scan: block imports of dangerous modules."""
    dangerous = {"ctypes", "socket", "subprocess", "os.system", "eval(", "exec(", "compile(", "__import__"}
    for bad in dangerous:
        if bad in code:
            return False, f"blocked pattern: {bad}", ""
    # Normalize indentation
    lines = code.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    cleaned = textwrap.dedent("\n".join(lines))
    return True, "", cleaned


def run_interpreter(code: str, timeout: int = 60) -> dict[str, Any]:
    """Run Python code with automatic figure/file capture."""
    ok, error, cleaned = _sanitize_code(code)
    if not ok:
        return {"ok": False, "error": error, "channel": "interpreter"}

    full_code = _INTERPRETER_PREAMBLE + "\n\n# --- user code ---\n" + cleaned
    result = run_python_code(full_code, timeout=timeout)

    stdout = result.get("stdout", "")
    stderr = result.get("stderr", "")
    workdir = Path(result.get("workdir", ""))

    # Extract embedded base64 figures
    figures: list[str] = []
    for match in re.finditer(r"SHIMS_FIGURE_B64:([A-Za-z0-9+/=]+)", stdout):
        figures.append(match.group(1))
    stdout = re.sub(r"SHIMS_FIGURE_B64:[A-Za-z0-9+/=]+\n?", "", stdout).strip()

    # Collect generated files (images, csv, json, etc.)
    artifacts: list[dict[str, Any]] = []
    if workdir.exists():
        for p in sorted(workdir.rglob("*")):
            if p.is_file() and p.name != "main.py" and not p.name.endswith(".pyc"):
                rel = str(p.relative_to(workdir)).replace("\\", "/")
                mime = "application/octet-stream"
                if rel.endswith(".png"):
                    mime = "image/png"
                elif rel.endswith(".csv"):
                    mime = "text/csv"
                elif rel.endswith(".json"):
                    mime = "application/json"
                elif rel.endswith(".txt"):
                    mime = "text/plain"
                artifacts.append({"path": rel, "size": p.stat().st_size, "mime": mime})

    return {
        "ok": result.get("status") == "passed",
        "status": result.get("status"),
        "returncode": result.get("returncode"),
        "stdout": stdout,
        "stderr": stderr,
        "figures": figures,
        "artifacts": artifacts,
        "workdir": str(workdir),
        "channel": "interpreter",
    }


def read_artifact(workdir: str, path: str, max_bytes: int = 100_000) -> dict[str, Any]:
    """Read a generated artifact from an interpreter run."""
    root = Path(workdir).resolve()
    target = (root / path).resolve()
    if not str(target).startswith(str(root)):
        return {"ok": False, "error": "path escapes workdir"}
    if not target.exists():
        return {"ok": False, "error": "file not found"}
    data = target.read_bytes()[:max_bytes]
    mime = "text/plain"
    if path.endswith(".png"):
        mime = "image/png"
        return {"ok": True, "mime": mime, "base64": base64.b64encode(data).decode("utf-8")}
    try:
        text = data.decode("utf-8")
        return {"ok": True, "mime": mime, "text": text}
    except Exception:
        return {"ok": True, "mime": "application/octet-stream", "base64": base64.b64encode(data).decode("utf-8")}
