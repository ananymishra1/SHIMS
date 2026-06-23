"""J K Hospital FastAPI router factory."""
from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import STATIC_DIR, TEMPLATES_DIR
from .database import ensure_schema
from .services import auth, patients, ai as ai_svc, voice as voice_svc
from .services import lab as lab_svc, rooms as rooms_svc, ot as ot_svc, ivf as ivf_svc
from .services import reminders as reminders_svc, demo as demo_svc


def create_hospital_router() -> APIRouter:
    ensure_schema()
    auth.ensure_default_users()
    rooms_svc.ensure_default_rooms()

    router = APIRouter(prefix="/hospital")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    @router.get("", response_class=HTMLResponse)
    async def hospital_home(request: Request):
        return templates.TemplateResponse(request, "index.html")

    # ---------- Auth ----------
    @router.post("/api/auth/login")
    async def hospital_login(request: Request):
        body = await request.json()
        user = auth.authenticate(body.get("username", "").strip(), body.get("password", ""))
        if not user:
            raise HTTPException(401, "Invalid credentials")
        return JSONResponse({"ok": True, "user": user})

    @router.get("/api/users")
    async def hospital_users():
        return {"ok": True, "users": auth.list_users()}

    # ---------- Patients ----------
    @router.get("/api/patients/search")
    async def patient_search(q: str = "", limit: int = 50):
        return {"ok": True, "patients": patients.search_patients(q, limit)}

    @router.post("/api/patients")
    async def patient_create(request: Request):
        data = await request.json()
        required = ["name", "phone"]
        missing = [r for r in required if not data.get(r)]
        if missing:
            raise HTTPException(400, f"Missing: {', '.join(missing)}")
        patient = patients.create_patient(data)
        return {"ok": True, "patient": patient}

    @router.get("/api/patients/{patient_id}")
    async def patient_get(patient_id: int):
        patient = patients.get_patient(patient_id)
        if not patient:
            raise HTTPException(404, "Patient not found")
        return {"ok": True, "patient": patient}

    @router.put("/api/patients/{patient_id}")
    async def patient_update(patient_id: int, request: Request):
        data = await request.json()
        patient = patients.update_patient(patient_id, data)
        if not patient:
            raise HTTPException(404, "Patient not found")
        return {"ok": True, "patient": patient}

    # ---------- Visits ----------
    @router.post("/api/visits")
    async def visit_create(request: Request):
        data = await request.json()
        if not data.get("patient_id"):
            raise HTTPException(400, "patient_id required")
        visit = patients.create_visit(data)
        return {"ok": True, "visit": visit}

    @router.get("/api/visits")
    async def visit_list(patient_id: int | None = None, status: str | None = None, visit_type: str | None = None, limit: int = 100):
        return {"ok": True, "visits": patients.list_visits(patient_id, status, visit_type, limit)}

    @router.get("/api/visits/{visit_id}")
    async def visit_get(visit_id: int):
        summary = patients.full_visit_summary(visit_id)
        if not summary:
            raise HTTPException(404, "Visit not found")
        return {"ok": True, **summary}

    @router.put("/api/visits/{visit_id}")
    async def visit_update(visit_id: int, request: Request):
        data = await request.json()
        visit = patients.update_visit(visit_id, data)
        if not visit:
            raise HTTPException(404, "Visit not found")
        return {"ok": True, "visit": visit}

    # ---------- Vitals / Complaints / Diagnoses / Prescriptions ----------
    @router.post("/api/visits/{visit_id}/vitals")
    async def vitals_add(visit_id: int, request: Request):
        data = await request.json()
        v = patients.add_vitals(visit_id, data)
        return {"ok": True, "vitals": v}

    @router.post("/api/visits/{visit_id}/complaints")
    async def complaint_add(visit_id: int, request: Request):
        data = await request.json()
        if not data.get("complaint"):
            raise HTTPException(400, "complaint required")
        c = patients.add_complaint(visit_id, data)
        return {"ok": True, "complaint": c}

    @router.post("/api/visits/{visit_id}/diagnoses")
    async def diagnosis_add(visit_id: int, request: Request):
        data = await request.json()
        if not data.get("diagnosis"):
            raise HTTPException(400, "diagnosis required")
        d = patients.add_diagnosis(visit_id, data)
        return {"ok": True, "diagnosis": d}

    @router.post("/api/visits/{visit_id}/prescriptions")
    async def prescription_add(visit_id: int, request: Request):
        data = await request.json()
        if not data.get("medication"):
            raise HTTPException(400, "medication required")
        p = patients.add_prescription(visit_id, data)
        return {"ok": True, "prescription": p}

    # ---------- AI ----------
    @router.post("/api/ai/differential")
    async def ai_differential(request: Request):
        data = await request.json()
        result = await ai_svc.differential_diagnosis(
            data.get("complaints", []),
            data.get("vitals", {}),
            data.get("history", ""),
            data.get("age_gender", ""),
        )
        if result.get("ok") and data.get("visit_id"):
            ai_svc.save_ai_note("differential", str(result.get("result")), visit_id=data.get("visit_id"), prompt_context=str(data))
        return {"ok": result.get("ok", False), **result}

    @router.post("/api/ai/treatment")
    async def ai_treatment(request: Request):
        data = await request.json()
        result = await ai_svc.treatment_suggestions(
            data.get("diagnosis", ""),
            data.get("complaints", []),
            data.get("vitals", {}),
            data.get("history", ""),
            data.get("age_gender", ""),
        )
        if result.get("ok") and data.get("visit_id"):
            ai_svc.save_ai_note("treatment_suggestion", str(result.get("result")), visit_id=data.get("visit_id"), prompt_context=str(data))
        return {"ok": result.get("ok", False), **result}

    @router.post("/api/ai/mentor")
    async def ai_mentor(request: Request):
        data = await request.json()
        result = await ai_svc.junior_doctor_mentor(data.get("question", ""), data.get("context", ""))
        return {"ok": result.get("ok", False), **result}

    # ---------- Voice ----------
    @router.post("/api/voice/transcribe")
    async def voice_transcribe(file: UploadFile = File(...)):
        audio = await file.read()
        result = await voice_svc.transcribe_voice(audio, content_type=file.content_type or "audio/webm")
        return {"ok": result.get("ok", False), **result}

    # ---------- Lab ----------
    @router.post("/api/visits/{visit_id}/lab-orders")
    async def lab_order_add(visit_id: int, request: Request):
        data = await request.json()
        if not data.get("test_name"):
            raise HTTPException(400, "test_name required")
        order = lab_svc.create_order(visit_id, data)
        return {"ok": True, "order": order}

    @router.get("/api/visits/{visit_id}/lab-orders")
    async def lab_order_list(visit_id: int):
        return {"ok": True, "orders": lab_svc.list_orders(visit_id=visit_id)}

    @router.post("/api/lab-orders/{order_id}/results")
    async def lab_result_add(order_id: int, request: Request):
        data = await request.json()
        if not data.get("parameter"):
            raise HTTPException(400, "parameter required")
        result = lab_svc.add_result(order_id, data)
        return {"ok": True, "result": result}

    @router.get("/api/lab-orders/{order_id}/results")
    async def lab_result_list(order_id: int):
        return {"ok": True, "results": lab_svc.get_results(order_id)}

    @router.patch("/api/lab-orders/{order_id}")
    async def lab_order_update(order_id: int, request: Request):
        data = await request.json()
        order = lab_svc.update_order_status(order_id, data.get("status"))
        return {"ok": True, "order": order}

    # ---------- Rooms ----------
    @router.get("/api/rooms")
    async def rooms_list(status: str | None = None):
        return {"ok": True, "rooms": rooms_svc.list_rooms(status)}

    @router.post("/api/visits/{visit_id}/allocate-bed")
    async def allocate_bed(visit_id: int, request: Request):
        data = await request.json()
        if not data.get("bed_id"):
            raise HTTPException(400, "bed_id required")
        try:
            alloc = rooms_svc.allocate_bed(visit_id, data["bed_id"])
            return {"ok": True, "allocation": alloc}
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @router.post("/api/visits/{visit_id}/discharge-bed")
    async def discharge_bed(visit_id: int):
        rooms_svc.discharge_bed(visit_id)
        return {"ok": True}

    # ---------- OT ----------
    @router.get("/api/ot/rooms")
    async def ot_rooms():
        return {"ok": True, "rooms": ot_svc.OT_ROOMS}

    @router.post("/api/visits/{visit_id}/ot-schedule")
    async def ot_schedule_add(visit_id: int, request: Request):
        data = await request.json()
        if not data.get("procedure") or not data.get("scheduled_at"):
            raise HTTPException(400, "procedure and scheduled_at required")
        sched = ot_svc.create_schedule(visit_id, data)
        return {"ok": True, "schedule": sched}

    @router.get("/api/ot-schedules")
    async def ot_schedule_list(status: str | None = None, limit: int = 100):
        return {"ok": True, "schedules": ot_svc.list_schedules(status, limit)}

    @router.patch("/api/ot-schedules/{ot_id}")
    async def ot_schedule_update(ot_id: int, request: Request):
        data = await request.json()
        sched = ot_svc.update_schedule(ot_id, data)
        return {"ok": True, "schedule": sched}

    # ---------- IVF ----------
    @router.post("/api/ivf/couples")
    async def ivf_couple_create(request: Request):
        data = await request.json()
        if not data.get("female_patient_id"):
            raise HTTPException(400, "female_patient_id required")
        couple = ivf_svc.create_couple(data)
        return {"ok": True, "couple": couple}

    @router.get("/api/ivf/couples")
    async def ivf_couple_list(limit: int = 100):
        return {"ok": True, "couples": ivf_svc.list_couples(limit)}

    @router.get("/api/ivf/couples/{couple_id}")
    async def ivf_couple_get(couple_id: int):
        couple = ivf_svc.get_couple(couple_id)
        if not couple:
            raise HTTPException(404, "Couple not found")
        return {"ok": True, "couple": couple, "cycles": ivf_svc.list_cycles(couple_id)}

    @router.post("/api/ivf/couples/{couple_id}/cycles")
    async def ivf_cycle_create(couple_id: int, request: Request):
        data = await request.json()
        cycle = ivf_svc.create_cycle(couple_id, data)
        return {"ok": True, "cycle": cycle}

    @router.get("/api/ivf/cycles/{cycle_id}")
    async def ivf_cycle_get(cycle_id: int):
        summary = ivf_svc.full_cycle_summary(cycle_id)
        if not summary:
            raise HTTPException(404, "Cycle not found")
        return {"ok": True, **summary}

    @router.patch("/api/ivf/cycles/{cycle_id}")
    async def ivf_cycle_update(cycle_id: int, request: Request):
        data = await request.json()
        cycle = ivf_svc.update_cycle(cycle_id, data)
        return {"ok": True, "cycle": cycle}

    @router.post("/api/ivf/cycles/{cycle_id}/stimulations")
    async def ivf_stimulation_add(cycle_id: int, request: Request):
        data = await request.json()
        if not data.get("medication"):
            raise HTTPException(400, "medication required")
        s = ivf_svc.add_stimulation(cycle_id, data)
        return {"ok": True, "stimulation": s}

    @router.post("/api/ivf/cycles/{cycle_id}/scans")
    async def ivf_scan_add(cycle_id: int, request: Request):
        data = await request.json()
        if not data.get("scan_date"):
            raise HTTPException(400, "scan_date required")
        s = ivf_svc.add_scan(cycle_id, data)
        return {"ok": True, "scan": s}

    @router.post("/api/ivf/cycles/{cycle_id}/embryos")
    async def ivf_embryo_add(cycle_id: int, request: Request):
        data = await request.json()
        e = ivf_svc.add_embryo(cycle_id, data)
        return {"ok": True, "embryo": e}

    @router.post("/api/ivf/cycles/{cycle_id}/ai-insight")
    async def ivf_ai_insight(cycle_id: int, request: Request):
        summary = ivf_svc.full_cycle_summary(cycle_id)
        if not summary:
            raise HTTPException(404, "Cycle not found")
        couple = summary["couple"]
        female = patients.get_patient(couple["female_patient_id"]) if couple else {}
        male = patients.get_patient(couple["male_patient_id"]) if couple and couple.get("male_patient_id") else {}
        couple_summary = f"Female: {female.get('name','')} age {female.get('age','')}; Male: {male.get('name','')} age {male.get('age','')}; TTC {couple.get('trying_to_conceive_years','')} yrs; prior IVF {couple.get('prior_ivf_cycles','')}; known causes: {couple.get('known_causes','')}"
        cycle_summary = f"Cycle #{summary['cycle']['cycle_number']} {summary['cycle']['protocol']}; scans: {len(summary['scans'])}; embryos: {len(summary['embryos'])}"
        result = await ai_svc.ivf_insight(couple_summary, cycle_summary)
        if result.get("ok"):
            ai_svc.save_ai_note("ivf_insight", str(result.get("result")), patient_id=couple.get("female_patient_id"), prompt_context=cycle_summary)
        return {"ok": result.get("ok", False), **result}

    # ---------- Reminders ----------
    @router.post("/api/reminders")
    async def reminder_create(request: Request):
        data = await request.json()
        if not data.get("patient_id") or not data.get("reminder_type") or not data.get("scheduled_at"):
            raise HTTPException(400, "patient_id, reminder_type, scheduled_at required")
        r = reminders_svc.create_reminder(data["patient_id"], data["reminder_type"], data["scheduled_at"], data.get("message", ""), data.get("visit_id"))
        return {"ok": True, "reminder": r}

    @router.get("/api/reminders")
    async def reminder_list(status: str | None = None, patient_id: int | None = None):
        return {"ok": True, "reminders": reminders_svc.list_reminders(status, patient_id)}

    @router.get("/api/reminders/for-role/{role}")
    async def reminders_for_role(role: str):
        return {"ok": True, "reminders": reminders_svc.upcoming_for_role(role)}

    # ---------- Demo & estimator ----------
    @router.post("/api/demo/generate")
    async def demo_generate(request: Request):
        data = await request.json()
        n = min(int(data.get("count", 50)), 200)
        count = demo_svc.generate_patients(n)
        return {"ok": True, "generated": count}

    @router.post("/api/estimator/staff")
    async def staff_estimator(request: Request):
        data = await request.json()
        result = demo_svc.estimate_staff(
            int(data.get("patient_load", 0)),
            int(data.get("opd_per_day", 0)),
            int(data.get("ipd_beds", 0)),
            int(data.get("ivf_cycles_per_month", 0)),
        )
        return {"ok": True, "estimate": result}

    # ---------- Status ----------
    @router.get("/api/status")
    async def hospital_status():
        from .database import query_one
        counts = query_one(
            """SELECT
                (SELECT COUNT(*) FROM patients) patients,
                (SELECT COUNT(*) FROM visits WHERE status='active') active_visits,
                (SELECT COUNT(*) FROM visits WHERE visit_type='opd' AND status='active') opd_active,
                (SELECT COUNT(*) FROM visits WHERE visit_type='ipd' AND status='active') ipd_active,
                (SELECT COUNT(*) FROM visits WHERE visit_type='ivf' AND status='active') ivf_active,
                (SELECT COUNT(*) FROM ot_schedules WHERE status IN ('scheduled','in_progress')) ot_pending
            """
        )
        return {"ok": True, "counts": counts}

    return router


def mount_static(app) -> None:
    app.mount("/hospital-static", StaticFiles(directory=str(STATIC_DIR)), name="hospital-static")
