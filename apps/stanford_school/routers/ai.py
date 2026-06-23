from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from ..database import get_db
from .auth import require_user

router = APIRouter(prefix="/api/ai")


class SuggestRequest(BaseModel):
    grade: Optional[str] = None
    threshold: Optional[float] = 40.0


@router.post("/suggest")
def suggest_intervention(body: SuggestRequest):
    threshold = body.threshold if body.threshold is not None else 40.0
    with get_db() as conn:
        # Find students with low marks in results
        query = """
            SELECT s.id, s.admission_number, s.full_name, s.grade, s.section,
                   AVG(r.marks) as avg_marks,
                   AVG(CASE WHEN e.max_marks > 0 THEN (r.marks * 100.0 / e.max_marks) ELSE 0 END) as avg_pct
            FROM student s
            JOIN result r ON r.student_id = s.id
            JOIN exam e ON e.id = r.exam_id
        """
        params = []
        if body.grade:
            query += " WHERE s.grade = ?"
            params.append(body.grade)
        query += " GROUP BY s.id HAVING avg_pct < ?"
        params.append(threshold)

        cursor = conn.execute(query, params)
        rows = cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        at_risk = [dict(zip(cols, row)) for row in rows]

        # Find students with high absenteeism
        abs_query = """
            SELECT s.id, s.admission_number, s.full_name, s.grade, s.section,
                   COUNT(*) as total_days,
                   SUM(CASE WHEN a.status = 'absent' THEN 1 ELSE 0 END) as absent_days,
                   ROUND(SUM(CASE WHEN a.status = 'absent' THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2) as absence_pct
            FROM student s
            JOIN attendance a ON a.student_id = s.id
        """
        abs_params = []
        if body.grade:
            abs_query += " WHERE s.grade = ?"
            abs_params.append(body.grade)
        abs_query += " GROUP BY s.id HAVING absence_pct > 25"

        cursor2 = conn.execute(abs_query, abs_params)
        rows2 = cursor2.fetchall()
        cols2 = [d[0] for d in cursor2.description]
        high_absent = [dict(zip(cols2, row)) for row in rows2]

        # Find students with outstanding fees
        fee_query = """
            SELECT s.id, s.admission_number, s.full_name, s.grade, s.section,
                   SUM(f.amount) as total_fees,
                   SUM(COALESCE(f.paid, 0)) as total_paid,
                   SUM(f.amount) - SUM(COALESCE(f.paid, 0)) as outstanding
            FROM student s
            JOIN fee f ON f.student_id = s.id
        """
        fee_params = []
        if body.grade:
            fee_query += " WHERE s.grade = ?"
            fee_params.append(body.grade)
        fee_query += " GROUP BY s.id HAVING outstanding > 0"

        cursor3 = conn.execute(fee_query, fee_params)
        rows3 = cursor3.fetchall()
        cols3 = [d[0] for d in cursor3.description]
        fee_outstanding = [dict(zip(cols3, row)) for row in rows3]

        interventions = []

        for student in at_risk:
            interventions.append({
                "student_id": student["id"],
                "admission_number": student["admission_number"],
                "full_name": student["full_name"],
                "grade": student["grade"],
                "section": student["section"],
                "risk_type": "academic",
                "detail": f"Average score {round(student['avg_pct'], 2)}% is below threshold {threshold}%",
                "suggestion": "Schedule remedial classes and parental meeting. Assign a mentor teacher.",
            })

        for student in high_absent:
            interventions.append({
                "student_id": student["id"],
                "admission_number": student["admission_number"],
                "full_name": student["full_name"],
                "grade": student["grade"],
                "section": student["section"],
                "risk_type": "attendance",
                "detail": f"Absence rate {student['absence_pct']}% exceeds 25%",
                "suggestion": "Contact parents immediately. Investigate reasons for absenteeism.",
            })

        for student in fee_outstanding:
            interventions.append({
                "student_id": student["id"],
                "admission_number": student["admission_number"],
                "full_name": student["full_name"],
                "grade": student["grade"],
                "section": student["section"],
                "risk_type": "fee_default",
                "detail": f"Outstanding fee balance: {student['outstanding']}",
                "suggestion": "Send fee reminder and offer installment plan if needed.",
            })

        summary = {
            "total_at_risk": len(interventions),
            "academic_risk": len(at_risk),
            "attendance_risk": len(high_absent),
            "fee_default_risk": len(fee_outstanding),
        }

        return {
            "summary": summary,
            "interventions": interventions,
            "grade_filter": body.grade,
            "academic_threshold_pct": threshold,
        }