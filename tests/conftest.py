import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Use an in-memory/temp SQLite DB for tests so seeding runs fresh every time.
_test_db_fd, _test_db_path = tempfile.mkstemp(suffix='.sqlite3')
os.close(_test_db_fd)
os.environ['SHIMS_DB_PATH'] = _test_db_path

# Ensure PYTEST_CURRENT_TEST is set (pytest usually does this, but be explicit).
os.environ.setdefault('PYTEST_CURRENT_TEST', '1')
