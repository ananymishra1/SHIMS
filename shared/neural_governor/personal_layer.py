"""Personal operating layer — learns and adapts to each user."""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import PersonalProfile

PERSONAL_DB = Path(__file__).resolve().parent.parent.parent / "data" / "state" / "governor_personal.sqlite3"
PERSONAL_DB.parent.mkdir(parents=True, exist_ok=True)


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(str(PERSONAL_DB))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS personal_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE NOT NULL,
            writing_style TEXT DEFAULT 'formal',
            preferred_formats_json TEXT DEFAULT '[]',
            sentence_length TEXT DEFAULT 'medium',
            technical_depth INTEGER DEFAULT 3,
            factory_context_json TEXT DEFAULT '{}',
            rd_habits_json TEXT DEFAULT '[]',
            document_patterns_json TEXT DEFAULT '[]',
            workflow_sequences_json TEXT DEFAULT '[]',
            active_projects_json TEXT DEFAULT '[]',
            communication_tone TEXT DEFAULT 'professional',
            correction_history_json TEXT DEFAULT '[]',
            peak_hours_json TEXT DEFAULT '[]',
            learning_enabled INTEGER DEFAULT 1,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    con.commit()
    return con


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_profile(user_id: int) -> Optional[PersonalProfile]:
    with _connect() as con:
        row = con.execute("SELECT * FROM personal_profiles WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        return None
    return _row_to_profile(row)


def ensure_profile(user_id: int) -> PersonalProfile:
    """Get or create a default profile for a user."""
    profile = get_profile(user_id)
    if profile:
        return profile
    profile = PersonalProfile(user_id=user_id)
    save_profile(profile)
    return profile


def save_profile(profile: PersonalProfile) -> None:
    with _connect() as con:
        con.execute(
            """
            INSERT INTO personal_profiles (
                user_id, writing_style, preferred_formats_json, sentence_length, technical_depth,
                factory_context_json, rd_habits_json, document_patterns_json, workflow_sequences_json,
                active_projects_json, communication_tone, correction_history_json, peak_hours_json,
                learning_enabled, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                writing_style=excluded.writing_style,
                preferred_formats_json=excluded.preferred_formats_json,
                sentence_length=excluded.sentence_length,
                technical_depth=excluded.technical_depth,
                factory_context_json=excluded.factory_context_json,
                rd_habits_json=excluded.rd_habits_json,
                document_patterns_json=excluded.document_patterns_json,
                workflow_sequences_json=excluded.workflow_sequences_json,
                active_projects_json=excluded.active_projects_json,
                communication_tone=excluded.communication_tone,
                correction_history_json=excluded.correction_history_json,
                peak_hours_json=excluded.peak_hours_json,
                learning_enabled=excluded.learning_enabled,
                updated_at=excluded.updated_at
            """,
            (
                profile.user_id,
                profile.writing_style,
                json.dumps(profile.preferred_formats),
                profile.sentence_length,
                profile.technical_depth,
                json.dumps(profile.factory_context),
                json.dumps(profile.rd_habits),
                json.dumps(profile.document_patterns),
                json.dumps(profile.workflow_sequences),
                json.dumps(profile.active_projects),
                profile.communication_tone,
                json.dumps(profile.correction_history),
                json.dumps(profile.peak_hours),
                int(profile.learning_enabled),
                _now(),
            ),
        )
        con.commit()


def format_profile_context(profile: PersonalProfile) -> str:
    """Format profile as context string for prompts."""
    lines = [f"User profile (learned):"]
    lines.append(f"- Writing style: {profile.writing_style}")
    lines.append(f"- Technical depth: {profile.technical_depth}/5")
    lines.append(f"- Tone: {profile.communication_tone}")
    if profile.preferred_formats:
        lines.append(f"- Preferred formats: {', '.join(profile.preferred_formats)}")
    if profile.active_projects:
        lines.append(f"- Active projects: {', '.join(profile.active_projects)}")
    if profile.factory_context:
        dept = profile.factory_context.get("department", "")
        if dept:
            lines.append(f"- Department: {dept}")
        role = profile.factory_context.get("role", "")
        if role:
            lines.append(f"- Role: {role}")
    if profile.rd_habits:
        lines.append(f"- R&D focus areas: {', '.join(profile.rd_habits[:5])}")
    return "\n".join(lines)


def learn_from_interaction(
    user_id: int,
    prompt: str,
    output: str,
    feedback_rating: Optional[int] = None,
    feedback_notes: str = "",
) -> None:
    """Update personal profile based on interaction."""
    profile = ensure_profile(user_id)
    if not profile.learning_enabled:
        return

    # Learn from corrections
    if feedback_rating == -1 and feedback_notes:
        profile.correction_history.append({
            "timestamp": _now(),
            "notes": feedback_notes,
            "prompt_preview": prompt[:100],
        })
        # Trim history
        profile.correction_history = profile.correction_history[-20:]

    # Learn format preferences from prompts
    prompt_lower = prompt.lower()
    if any(w in prompt_lower for w in ["pdf", "document", "report"]):
        if "pdf" not in profile.preferred_formats:
            profile.preferred_formats.append("pdf")
    if any(w in prompt_lower for w in ["docx", "word", "microsoft"]):
        if "docx" not in profile.preferred_formats:
            profile.preferred_formats.append("docx")
    if any(w in prompt_lower for w in ["markdown", "md", "readme"]):
        if "markdown" not in profile.preferred_formats:
            profile.preferred_formats.append("markdown")

    # Learn R&D habits
    if any(w in prompt_lower for w in ["experiment", "reaction", "synthesis", "yield", "impurity"]):
        habit = None
        if "reaction" in prompt_lower:
            habit = "reaction_optimization"
        elif "yield" in prompt_lower:
            habit = "yield_analysis"
        elif "impurity" in prompt_lower:
            habit = "impurity_profiling"
        elif "formulation" in prompt_lower:
            habit = "formulation_development"
        if habit and habit not in profile.rd_habits:
            profile.rd_habits.append(habit)
            profile.rd_habits = profile.rd_habits[-10:]

    # Learn document patterns
    if any(w in prompt_lower for w in ["coa", "certificate of analysis"]):
        if "coa" not in profile.document_patterns:
            profile.document_patterns.append("coa")
    if any(w in prompt_lower for w in ["sop", "standard operating procedure"]):
        if "sop" not in profile.document_patterns:
            profile.document_patterns.append("sop")
    if any(w in prompt_lower for w in ["bmr", "batch manufacturing"]):
        if "bmr" not in profile.document_patterns:
            profile.document_patterns.append("bmr")

    # Learn peak hours
    hour = datetime.now(timezone.utc).hour
    if hour not in profile.peak_hours:
        profile.peak_hours.append(hour)
        profile.peak_hours = profile.peak_hours[-12:]

    save_profile(profile)


def _row_to_profile(row: sqlite3.Row) -> PersonalProfile:
    def load(col: str, default: Any) -> Any:
        try:
            return json.loads(row[col]) if row[col] else default
        except Exception:
            return default

    return PersonalProfile(
        user_id=row["user_id"],
        writing_style=row["writing_style"] or "formal",
        preferred_formats=load("preferred_formats_json", []),
        sentence_length=row["sentence_length"] or "medium",
        technical_depth=row["technical_depth"] or 3,
        factory_context=load("factory_context_json", {}),
        rd_habits=load("rd_habits_json", []),
        document_patterns=load("document_patterns_json", []),
        workflow_sequences=load("workflow_sequences_json", []),
        active_projects=load("active_projects_json", []),
        communication_tone=row["communication_tone"] or "professional",
        correction_history=load("correction_history_json", []),
        peak_hours=load("peak_hours_json", []),
        learning_enabled=bool(row["learning_enabled"]),
        updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None,
    )
