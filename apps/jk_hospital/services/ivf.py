"""IVF fertility module."""
from __future__ import annotations

from typing import Any

from ..database import execute, insert, query_all, query_one


def create_couple(data: dict[str, Any]) -> dict[str, Any]:
    cid = insert(
        """INSERT INTO ivf_couples
        (female_patient_id, male_patient_id, married_years, trying_to_conceive_years,
         previous_pregnancies, previous_miscarriages, prior_ivf_cycles, known_causes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (data["female_patient_id"], data.get("male_patient_id"), data.get("married_years"),
         data.get("trying_to_conceive_years"), data.get("previous_pregnancies", 0),
         data.get("previous_miscarriages", 0), data.get("prior_ivf_cycles", 0), data.get("known_causes")),
    )
    return get_couple(cid)


def get_couple(couple_id: int) -> dict[str, Any] | None:
    return query_one("SELECT * FROM ivf_couples WHERE id=?", (couple_id,))


def list_couples(limit: int = 100) -> list[dict[str, Any]]:
    return query_all("SELECT * FROM ivf_couples ORDER BY created_at DESC LIMIT ?", (limit,))


def create_cycle(couple_id: int, data: dict[str, Any]) -> dict[str, Any]:
    # count existing cycles
    count = query_one("SELECT COUNT(*) c FROM ivf_cycles WHERE couple_id=?", (couple_id,))["c"]
    cid = insert(
        "INSERT INTO ivf_cycles (couple_id, cycle_number, protocol, start_date, expected_retrieval_date, expected_transfer_date, status, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (couple_id, count + 1, data.get("protocol"), data.get("start_date"), data.get("expected_retrieval_date"), data.get("expected_transfer_date"), data.get("status", "planned"), data.get("notes")),
    )
    return get_cycle(cid)


def get_cycle(cycle_id: int) -> dict[str, Any] | None:
    return query_one("SELECT * FROM ivf_cycles WHERE id=?", (cycle_id,))


def list_cycles(couple_id: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
    if couple_id:
        return query_all("SELECT * FROM ivf_cycles WHERE couple_id=? ORDER BY cycle_number DESC LIMIT ?", (couple_id, limit))
    return query_all("SELECT * FROM ivf_cycles ORDER BY created_at DESC LIMIT ?", (limit,))


def update_cycle(cycle_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
    allowed = {"protocol", "start_date", "expected_retrieval_date", "expected_transfer_date", "status", "outcome", "notes"}
    fields, params = [], []
    for k, v in data.items():
        if k in allowed:
            fields.append(f"{k} = ?")
            params.append(v)
    if not fields:
        return get_cycle(cycle_id)
    params.append(cycle_id)
    execute(f"UPDATE ivf_cycles SET {', '.join(fields)} WHERE id=?", tuple(params))
    return get_cycle(cycle_id)


def add_stimulation(cycle_id: int, data: dict[str, Any]) -> dict[str, Any]:
    sid = insert(
        "INSERT INTO ivf_stimulations (cycle_id, medication, dose, unit, administered_at, administered_by, notes) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (cycle_id, data["medication"], data.get("dose"), data.get("unit"), data.get("administered_at"), data.get("administered_by"), data.get("notes")),
    )
    return query_one("SELECT * FROM ivf_stimulations WHERE id=?", (sid,))


def list_stimulations(cycle_id: int) -> list[dict[str, Any]]:
    return query_all("SELECT * FROM ivf_stimulations WHERE cycle_id=? ORDER BY administered_at DESC", (cycle_id,))


def add_scan(cycle_id: int, data: dict[str, Any]) -> dict[str, Any]:
    sid = insert(
        "INSERT INTO ivf_follicle_scans (cycle_id, scan_date, right_follicles, left_follicles, largest_follicle_mm, endometrial_thickness_mm, notes) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (cycle_id, data["scan_date"], data.get("right_follicles"), data.get("left_follicles"), data.get("largest_follicle_mm"), data.get("endometrial_thickness_mm"), data.get("notes")),
    )
    return query_one("SELECT * FROM ivf_follicle_scans WHERE id=?", (sid,))


def list_scans(cycle_id: int) -> list[dict[str, Any]]:
    return query_all("SELECT * FROM ivf_follicle_scans WHERE cycle_id=? ORDER BY scan_date DESC", (cycle_id,))


def add_embryo(cycle_id: int, data: dict[str, Any]) -> dict[str, Any]:
    count = query_one("SELECT COUNT(*) c FROM ivf_embryos WHERE cycle_id=?", (cycle_id,))["c"]
    eid = insert(
        "INSERT INTO ivf_embryos (cycle_id, embryo_number, retrieval_date, maturity, fertilization_status, grade, transfer_date, freeze_date, outcome, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (cycle_id, count + 1, data.get("retrieval_date"), data.get("maturity"), data.get("fertilization_status"), data.get("grade"), data.get("transfer_date"), data.get("freeze_date"), data.get("outcome"), data.get("notes")),
    )
    return query_one("SELECT * FROM ivf_embryos WHERE id=?", (eid,))


def list_embryos(cycle_id: int) -> list[dict[str, Any]]:
    return query_all("SELECT * FROM ivf_embryos WHERE cycle_id=? ORDER BY embryo_number", (cycle_id,))


def full_cycle_summary(cycle_id: int) -> dict[str, Any] | None:
    cycle = get_cycle(cycle_id)
    if not cycle:
        return None
    couple = get_couple(cycle["couple_id"])
    return {
        "cycle": cycle,
        "couple": couple,
        "stimulations": list_stimulations(cycle_id),
        "scans": list_scans(cycle_id),
        "embryos": list_embryos(cycle_id),
    }
