"""Demo data generator for J K Hospital."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from . import auth, ivf, lab, patients, rooms


FIRST_NAMES_M = ["Rahul", "Amit", "Vikram", "Suresh", "Rajesh", "Anil", "Deepak", "Manish", "Sunil", "Karan"]
FIRST_NAMES_F = ["Priya", "Sunita", "Anjali", "Neha", "Pooja", "Ritu", "Kavita", "Meera", "Sonia", "Divya"]
LAST_NAMES = ["Sharma", "Gupta", "Kumar", "Singh", "Patel", "Verma", "Agarwal", "Reddy", "Mehta", "Shah"]
CITIES = ["Delhi", "Noida", "Ghaziabad", "Faridabad", "Gurgaon", "Meerut", "Agra", "Lucknow", "Jaipur"]
COMPLAINTS = ["fever", "cough", "abdominal pain", "headache", "chest pain", "shortness of breath", "joint pain", "diabetes follow-up", "hypertension review"]
DIAGNOSES = ["Viral fever", "Upper respiratory infection", "Acute gastroenteritis", "Migraine", "Hypertension", "Type 2 Diabetes mellitus", "Osteoarthritis", "Anemia"]
TESTS = ["CBC", "RBS", "HbA1c", "Lipid profile", "Liver function test", "Kidney function test", "Chest X-ray", "ECG"]


def _random_phone():
    return "98" + "".join(random.choices("0123456789", k=8))


def generate_patients(n: int = 50) -> int:
    count = 0
    for i in range(n):
        gender = random.choice(["Male", "Female"])
        name = (random.choice(FIRST_NAMES_M if gender == "Male" else FIRST_NAMES_F) + " " + random.choice(LAST_NAMES))
        age = random.randint(18, 75)
        dob = (datetime.now(timezone.utc) - timedelta(days=age*365 + random.randint(0,365))).strftime("%Y-%m-%d")
        p = patients.create_patient({
            "name": name,
            "phone": _random_phone(),
            "gender": gender,
            "age": age,
            "dob": dob,
            "city": random.choice(CITIES),
            "address": f"{random.randint(1,200)} {random.choice(['Main Rd','Sector 4','Gali 5','MG Road'])}",
            "blood_group": random.choice(["A+", "B+", "O+", "AB+", "A-", "O-"]),
            "allergies": random.choice(["none", "Penicillin", "Sulfa", "NSAIDs", ""]),
        })
        # create a visit
        vtype = random.choices(["opd", "opd", "ipd", "ivf"], weights=[60, 20, 10, 10])[0]
        v = patients.create_visit({
            "patient_id": p["id"],
            "visit_type": vtype,
            "chief_complaint": random.choice(COMPLAINTS),
            "department": random.choice(["General Medicine", "Orthopedics", "Gynecology", "Cardiology"]),
        })
        # vitals
        patients.add_vitals(v["id"], {
            "temperature": round(random.uniform(97.0, 102.0), 1),
            "pulse": random.randint(60, 110),
            "bp_systolic": random.randint(100, 160),
            "bp_diastolic": random.randint(70, 100),
            "spo2": round(random.uniform(94, 99), 1),
            "weight_kg": round(random.uniform(50, 90), 1),
            "height_cm": random.randint(150, 180),
        })
        # complaint
        patients.add_complaint(v["id"], {"complaint": v["chief_complaint"], "duration": random.choice(["1 day", "3 days", "1 week", "2 weeks"])})
        # diagnosis for some
        if random.random() > 0.3:
            patients.add_diagnosis(v["id"], {"diagnosis": random.choice(DIAGNOSES), "type": "provisional"})
        # prescriptions for some
        if random.random() > 0.5:
            patients.add_prescription(v["id"], {"medication": random.choice(["Paracetamol", "Amoxicillin", "Metformin", "Amlodipine"]), "dosage": "1 tab", "frequency": "BD", "duration": "5 days"})
        # lab orders
        if random.random() > 0.4:
            lab.create_order(v["id"], {"test_name": random.choice(TESTS), "category": "routine"})
        # IVF couple for IVF visits
        if vtype == "ivf" and gender == "Female":
            male_p = patients.create_patient({
                "name": random.choice(FIRST_NAMES_M) + " " + random.choice(LAST_NAMES),
                "phone": _random_phone(),
                "gender": "Male",
                "age": random.randint(28, 45),
            })
            couple = ivf.create_couple({
                "female_patient_id": p["id"],
                "male_patient_id": male_p["id"],
                "trying_to_conceive_years": random.randint(2, 8),
                "prior_ivf_cycles": random.randint(0, 2),
            })
            cycle = ivf.create_cycle(couple["id"], {"protocol": random.choice(["antagonist", "agonist", "mild"]), "start_date": datetime.now(timezone.utc).strftime("%Y-%m-%d")})
            ivf.add_scan(cycle["id"], {
                "scan_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "right_follicles": random.randint(3, 8),
                "left_follicles": random.randint(3, 8),
                "largest_follicle_mm": round(random.uniform(16, 22), 1),
                "endometrial_thickness_mm": round(random.uniform(7, 12), 1),
            })
        count += 1
    return count


def estimate_staff(patient_load: int, opd_per_day: int, ipd_beds: int, ivf_cycles_per_month: int) -> dict[str, Any]:
    """Estimate staffing and room needs based on workload."""
    doctors = max(2, round(opd_per_day / 25 + ipd_beds / 15))
    nurses = max(3, round(ipd_beds / 5 + opd_per_day / 40))
    receptionists = max(2, round(opd_per_day / 60))
    lab_techs = max(1, round((opd_per_day + ipd_beds) / 80))
    ot_coordinators = max(1, round(ipd_beds / 30))
    ivf_specialists = max(1, round(ivf_cycles_per_month / 15))
    pharmacist = 1 if patient_load > 50 else 0
    return {
        "estimated_staff": {
            "doctors": doctors,
            "nurses": nurses,
            "receptionists": receptionists,
            "lab_technicians": lab_techs,
            "ot_coordinators": ot_coordinators,
            "ivf_specialists": ivf_specialists,
            "pharmacists": pharmacist,
        },
        "recommended_beds": max(5, round(ipd_beds * 1.2)),
        "recommended_ot_rooms": max(1, round(ipd_beds / 20)),
        "notes": "Estimates assume 8-hour shifts and standard Indian hospital ratios. Adjust for 24x7 coverage and specialist availability.",
    }
