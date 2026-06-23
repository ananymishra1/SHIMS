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
            CREATE TABLE IF NOT EXISTS student (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admission_number TEXT NOT NULL,
                full_name TEXT NOT NULL,
                grade TEXT NOT NULL,
                section TEXT NOT NULL,
                parent_phone TEXT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS staff (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id TEXT NOT NULL,
                full_name TEXT NOT NULL,
                role TEXT NOT NULL,
                subject TEXT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS class (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                grade TEXT NOT NULL,
                section TEXT NOT NULL,
                class_teacher_id INTEGER
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                status TEXT NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS exam (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                subject TEXT NOT NULL,
                grade TEXT NOT NULL,
                max_marks REAL NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS result (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                exam_id INTEGER NOT NULL,
                marks REAL NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS fee (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                term TEXT NOT NULL,
                paid REAL
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