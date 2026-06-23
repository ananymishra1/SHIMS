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
        con.execute("""
            CREATE TABLE IF NOT EXISTS task (
                id    INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT    NOT NULL,
                done  INTEGER NOT NULL DEFAULT 0
            )
        """)

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