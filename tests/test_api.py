"""API Integration Test Suite for MasteryFlow endpoints."""

import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health():
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "healthy"


def test_auth_login():
    # Student login
    res = client.post("/api/auth/login", json={"user_id": "student_maya"})
    assert res.status_code == 200
    data = res.json()
    assert "access_token" in data
    assert data["user"]["role"] == "student"

    # Teacher login
    res_t = client.post("/api/auth/login", json={"user_id": "teacher_sarah"})
    assert res_t.status_code == 200
    data_t = res_t.json()
    assert data_t["user"]["role"] == "teacher"


def test_concepts_graph():
    res = client.get("/api/concepts")
    assert res.status_code == 200
    data = res.json()
    assert len(data["concepts"]) == 10
    assert len(data["edges"]) >= 9

    # Verify root has 0 prereqs and child has prereqs
    fb = next(c for c in data["concepts"] if c["id"] == "fraction_basics")
    assert len(fb["prerequisites"]) == 0

    ef = next(c for c in data["concepts"] if c["id"] == "equivalent_fractions")
    assert "fraction_basics" in ef["prerequisites"]


def test_student_recommendation():
    # Login as student
    login_res = client.post("/api/auth/login", json={"user_id": "student_maya"})
    token = login_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    res = client.get("/api/students/student_maya/recommendation", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "action" in data
    assert "reason" in data
    assert "counterfactual" in data
    assert "required_condition" in data["counterfactual"]


def test_student_attempt_submission():
    login_res = client.post("/api/auth/login", json={"user_id": "student_maya"})
    token = login_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Get next question
    q_res = client.get("/api/students/student_maya/next-question", headers=headers)
    assert q_res.status_code == 200
    q_data = q_res.json()["question"]
    assert q_data is not None

    # Submit correct attempt
    ans_res = client.post(
        "/api/attempts",
        json={
            "question_id": q_data["id"],
            "concept_id": q_data["concept_id"],
            "answer": q_data["options"][0],
            "confidence": 4,
            "response_ms": 6500,
            "hints_used": 0,
            "attempt_no": 1,
        },
        headers=headers,
    )
    assert ans_res.status_code == 200
    submit_data = ans_res.json()
    assert "new_mastery" in submit_data
    assert "weight_applied" in submit_data
    assert "next_action" in submit_data


def test_teacher_heatmap_and_stuck():
    login_res = client.post("/api/auth/login", json={"user_id": "teacher_sarah"})
    token = login_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Heatmap
    heatmap_res = client.get("/api/teacher/class-heatmap", headers=headers)
    assert heatmap_res.status_code == 200
    heatmap = heatmap_res.json()
    assert len(heatmap) >= 4  # Cohort seeded

    # Stuck learners
    stuck_res = client.get("/api/teacher/stuck", headers=headers)
    assert stuck_res.status_code == 200
    stuck_list = stuck_res.json()
    # Leo was seeded as stuck on ratios
    assert any(item["student_id"] == "student_leo" for item in stuck_list)


def test_teacher_override_requires_note():
    login_res = client.post("/api/auth/login", json={"user_id": "teacher_sarah"})
    token = login_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Reject override with empty/short note
    res_bad = client.post(
        "/api/teacher/overrides",
        json={
            "decision_id": "dec_leo_initial_01",
            "student_id": "student_leo",
            "new_action": "Practice",
            "new_target_concept_id": "fraction_basics",
            "note": "short",
        },
        headers=headers,
    )
    assert res_bad.status_code == 422 or res_bad.status_code == 400

    # Accept valid override
    res_good = client.post(
        "/api/teacher/overrides",
        json={
            "decision_id": "dec_leo_initial_01",
            "student_id": "student_leo",
            "new_action": "Practice",
            "new_target_concept_id": "fraction_basics",
            "note": "Assigned foundational 1-on-1 tutoring review session.",
        },
        headers=headers,
    )
    assert res_good.status_code == 200
    assert res_good.json()["success"] is True


def test_simulation_scenarios():
    for scenario in ["guessing_spam", "long_gap_fail", "easy_right_hard_wrong", "prereq_conflict"]:
        res = client.post(f"/api/simulate/{scenario}")
        assert res.status_code == 200
        data = res.json()
        assert data["verified"] is True
