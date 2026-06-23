from shared.ai import ask_ai
from ..database import get_db


def suggest_intervention(payload: dict) -> dict:
    grade = payload.get("grade", "")
    section = payload.get("section", "")

    with get_db() as conn:
        students = conn.execute(
            "SELECT * FROM student WHERE grade=? AND section=?",
            (grade, section),
        ).fetchall() if grade and section else conn.execute(
            "SELECT * FROM student"
        ).fetchall()

        student_ids = [s["id"] for s in students]

        attendance_data = []
        results_data = []
        fees_data = []

        for sid in student_ids:
            att = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM attendance WHERE student_id=? GROUP BY status",
                (sid,),
            ).fetchall()
            attendance_data.append({"student_id": sid, "attendance": [dict(a) for a in att]})

            res = conn.execute(
                "SELECT r.marks, e.max_marks, e.subject FROM result r JOIN exam e ON r.exam_id=e.id WHERE r.student_id=?",
                (sid,),
            ).fetchall()
            results_data.append({"student_id": sid, "results": [dict(r) for r in res]})

            fee = conn.execute(
                "SELECT amount, paid, term FROM fee WHERE student_id=?",
                (sid,),
            ).fetchall()
            fees_data.append({"student_id": sid, "fees": [dict(f) for f in fee]})

    students_list = [dict(s) for s in students]

    prompt = f"""
You are an academic advisor for Stanford International School.
Analyze the following student data and identify at-risk students who may need intervention.

Students: {students_list}
Attendance Summary: {attendance_data}
Exam Results: {results_data}
Fee Records: {fees_data}

Filter criteria from request: grade={grade!r}, section={section!r}

Identify students who are at risk due to:
1. High absenteeism (more than 20% absences)
2. Low academic performance (scoring below 50% in any subject)
3. Outstanding fees (paid less than amount due)

Respond with a JSON object in this exact format:
{{
  "at_risk_students": [
    {{
      "student_id": <int>,
      "full_name": "<string>",
      "risk_factors": ["<factor1>", "<factor2>"],
      "recommended_interventions": ["<intervention1>", "<intervention2>"],
      "priority": "high|medium|low"
    }}
  ],
  "summary": "<brief overall summary>",
  "total_at_risk": <int>
}}
"""

    result = ask_ai(prompt, expect_json=True)
    return result