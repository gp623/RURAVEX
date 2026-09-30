"""Tests for sandboxed AI layer, schema validation, number preservation, and fallback."""

import pytest
from app.ai.client import (
    deterministic_verify_question,
    verify_numbers_preserved,
    FALLBACK_TEMPLATES,
)
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_deterministic_verify_question_valid():
    valid_q = {
        "prompt": "If a ribbon is 2 meters long and cut into 4 equal pieces, what is the length of each piece?",
        "answer": "1/2 meter",
        "wrong_answers": [
            {"option": "2/1 meter", "misconception": "Inverted division"},
            {"option": "1/4 meter", "misconception": "Denominator error"},
        ],
        "hints": [
            "Tier 1: Divide 2 by 4.",
            "Tier 2: 2/4 simplifies to lowest terms.",
            "Tier 3: 2 ÷ 4 = 1/2 meter.",
        ],
    }
    is_valid, log = deterministic_verify_question(valid_q)
    assert is_valid is True
    assert "passed" in log.lower()


def test_deterministic_verify_question_invalid():
    # 1. Missing answer
    bad_q1 = {
        "prompt": "A sample prompt that is long enough but missing an answer?",
        "answer": "",
        "wrong_answers": [{"option": "A", "misconception": "M1"}, {"option": "B", "misconception": "M2"}],
        "hints": ["h1", "h2", "h3"],
    }
    valid1, reason1 = deterministic_verify_question(bad_q1)
    assert valid1 is False

    # 2. Correct answer duplicated in wrong answers
    bad_q2 = {
        "prompt": "A sample prompt asking for 1/2 of something?",
        "answer": "1/2",
        "wrong_answers": [{"option": "1/2", "misconception": "Duplicated"}, {"option": "1/4", "misconception": "Wrong"}],
        "hints": ["h1", "h2", "h3"],
    }
    valid2, reason2 = deterministic_verify_question(bad_q2)
    assert valid2 is False
    assert "duplicated" in reason2.lower()

    # 3. Incomplete hints
    bad_q3 = {
        "prompt": "A sample prompt asking for something?",
        "answer": "3/4",
        "wrong_answers": [{"option": "1/4", "misconception": "M1"}, {"option": "2/4", "misconception": "M2"}],
        "hints": ["Only 1 hint"],
    }
    valid3, reason3 = deterministic_verify_question(bad_q3)
    assert valid3 is False
    assert "3 progressive hint tiers" in reason3


def test_verify_numbers_preserved():
    orig = "Review Fractions before Ratios: prerequisite mastery 42% (±15%), below required threshold of 65%."

    # Valid rephrase preserving 42%, 15%, 65%
    valid_rephrase = "Let's review Fractions before moving on to Ratios! Your current mastery is 42% (±15%), which is just below the 65% goal."
    assert verify_numbers_preserved(orig, valid_rephrase) is True

    # Invalid rephrase distorting numbers (changed 42% to 50%)
    invalid_rephrase = "Let's review Fractions before Ratios! Your current mastery is 50% (±15%), below 65%."
    assert verify_numbers_preserved(orig, invalid_rephrase) is False


def test_ai_generate_question_endpoint():
    # Login as student
    login_res = client.post("/api/auth/login", json={"user_id": "student_maya"})
    token = login_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Request question generation
    res = client.post(
        "/api/ai/generate-question",
        json={"concept_id": "fraction_basics", "difficulty": 2, "type": "procedural"},
        headers=headers,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["verified"] is True
    assert data["question"] is not None
    assert "ai_status" in data
    # Either ai_generated or template fallback depending on API key
    assert data["ai_status"] in ["ai_generated", "fallback_template", "ai_unavailable"]


def test_ai_explain_endpoint():
    login_res = client.post("/api/auth/login", json={"user_id": "student_maya"})
    token = login_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    reason = "Practice fraction_basics: current mastery 42% (±15%) in progress towards 80% proficiency threshold."
    res = client.post(
        "/api/ai/explain",
        json={"numeric_reason": reason, "student_id": "student_maya"},
        headers=headers,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["numbers_preserved"] is True
    assert len(data["friendly_explanation"]) > 10
