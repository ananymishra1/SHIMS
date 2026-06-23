"""J K Hospital app configuration."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STORAGE_DIR = ROOT / "storage"
DB_PATH = STORAGE_DIR / "hospital.sqlite3"
STATIC_DIR = Path(__file__).resolve().parent / "static"
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

DEFAULT_ROLES = [
    "receptionist",
    "doctor",
    "nurse",
    "lab_technician",
    "ot_coordinator",
    "pharmacist",
    "ivf_specialist",
    "admin",
]

DEFAULT_USERS = [
    {"username": "reception", "password": "JKH2025!", "role": "receptionist", "full_name": "Reception Desk"},
    {"username": "doctor", "password": "JKH2025!", "role": "doctor", "full_name": "Duty Doctor"},
    {"username": "nurse", "password": "JKH2025!", "role": "nurse", "full_name": "Head Nurse"},
    {"username": "lab", "password": "JKH2025!", "role": "lab_technician", "full_name": "Lab Technician"},
    {"username": "ot", "password": "JKH2025!", "role": "ot_coordinator", "full_name": "OT Coordinator"},
    {"username": "ivf", "password": "JKH2025!", "role": "ivf_specialist", "full_name": "IVF Specialist"},
    {"username": "admin", "password": "JKH2025!", "role": "admin", "full_name": "Hospital Admin"},
]

AI_MODEL = os.getenv("SHIMS_HOSPITAL_AI_MODEL", "qwen2.5-coder:14b")
AI_PROVIDER = os.getenv("SHIMS_HOSPITAL_AI_PROVIDER", "ollama")
VOICE_MODEL = os.getenv("SHIMS_HOSPITAL_VOICE_MODEL", "small")  # faster-whisper model size
