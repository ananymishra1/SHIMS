"""Hospital AI helpers using local LLM."""
from __future__ import annotations

import json
from typing import Any

from shared.ai import ask_ai
from shared.omni_brain import retrieve_context

from ..database import insert
from ..config import AI_MODEL, AI_PROVIDER


async def differential_diagnosis(complaints: list[str], vitals: dict[str, Any], history: str, age_gender: str) -> dict[str, Any]:
    prompt = (
        "You are a clinical decision-support assistant for junior doctors in an Indian hospital.\n"
        "Given the following patient data, produce a structured differential diagnosis,\n"
        "red flags, and suggested next investigations. Do NOT prescribe definitive treatment;\n"
        "flag anything needing senior review.\n\n"
        f"Patient: {age_gender}\n"
        f"Complaints: {json.dumps(complaints, ensure_ascii=False)}\n"
        f"Vitals: {json.dumps(vitals, ensure_ascii=False)}\n"
        f"History/Allergies: {history or 'none recorded'}\n\n"
        "Return JSON only with keys: differentials (list of objects with name, likelihood, reasoning),\n"
        "red_flags (list), next_investigations (list), senior_review_required (bool), note (string)."
    )
    result = await ask_ai(prompt, system="You are a cautious clinical decision-support AI.", provider=AI_PROVIDER, model=AI_MODEL)
    parsed = _extract_json(result.text)
    return {"ok": result.ok, "provider": result.provider, "model": result.model, "result": parsed, "raw": result.text}


async def treatment_suggestions(diagnosis: str, complaints: list[str], vitals: dict[str, Any], history: str, age_gender: str) -> dict[str, Any]:
    prompt = (
        "You are a hospital treatment-advisory assistant. Suggest general management principles,\n"
        "likely medication classes, and monitoring parameters for the following case.\n"
        "Always include a disclaimer that a senior doctor must review and sign all orders.\n\n"
        f"Patient: {age_gender}\n"
        f"Provisional diagnosis: {diagnosis}\n"
        f"Complaints: {json.dumps(complaints, ensure_ascii=False)}\n"
        f"Vitals: {json.dumps(vitals, ensure_ascii=False)}\n"
        f"History/Allergies: {history or 'none recorded'}\n\n"
        "Return JSON only with keys: management_plan (string), medication_classes (list),\n"
        "monitoring (list), warnings (list), senior_review_required (bool)."
    )
    result = await ask_ai(prompt, system="You are a cautious clinical advisory AI.", provider=AI_PROVIDER, model=AI_MODEL)
    parsed = _extract_json(result.text)
    return {"ok": result.ok, "provider": result.provider, "model": result.model, "result": parsed, "raw": result.text}


async def ivf_insight(couple_summary: str, cycle_summary: str) -> dict[str, Any]:
    prompt = (
        "You are an IVF clinical support assistant. Review the couple summary and current cycle data,\n"
        "then provide: possible causes of infertility, interpretation of the current cycle,\n"
        "and suggestions for the embryology/IVF team. Always flag need for specialist review.\n\n"
        f"Couple: {couple_summary}\n"
        f"Cycle: {cycle_summary}\n\n"
        "Return JSON with keys: probable_causes (list), cycle_interpretation (string),\n"
        "suggestions (list), red_flags (list), specialist_review_required (bool)."
    )
    result = await ask_ai(prompt, system="You are an IVF clinical support AI.", provider=AI_PROVIDER, model=AI_MODEL)
    parsed = _extract_json(result.text)
    return {"ok": result.ok, "provider": result.provider, "model": result.model, "result": parsed, "raw": result.text}


async def junior_doctor_mentor(question: str, case_context: str) -> dict[str, Any]:
    prompt = (
        "A junior doctor at J K Hospital asks:\n" + question + "\n\n"
        "Case context:\n" + (case_context or "none") + "\n\n"
        "Give a concise, educational answer suitable for a junior doctor. Include red flags and when to escalate."
    )
    result = await ask_ai(prompt, system="You are a senior clinician mentoring a junior doctor.", provider=AI_PROVIDER, model=AI_MODEL)
    return {"ok": result.ok, "provider": result.provider, "model": result.model, "answer": result.text}


def save_ai_note(note_type: str, response: str, visit_id: int | None = None, patient_id: int | None = None, prompt_context: str = "") -> int:
    return insert(
        "INSERT INTO ai_notes (visit_id, patient_id, note_type, prompt_context, response) VALUES (?, ?, ?, ?, ?)",
        (visit_id, patient_id, note_type, prompt_context, response),
    )


def _extract_json(text: str) -> Any:
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except Exception:
        return {"parse_error": True, "raw": text}
