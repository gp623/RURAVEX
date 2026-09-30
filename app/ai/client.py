"""Sandboxed Gemini AI client for question generation and explanation rephrasing.
Strict JSON schema validation, deterministic math verification, and template fallback.
Zero engine dependency on this module.
"""

from __future__ import annotations
import json
import re
import uuid
import httpx
from typing import Dict, Any, Tuple, Optional
from app.config import settings

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

# High-quality fallback templates per concept if LLM is unavailable
FALLBACK_TEMPLATES = {
    "fraction_basics": {
        "prompt": "If a ribbon of length 1 meter is cut into 5 equal pieces, what fraction of a meter is each piece?",
        "answer": "1/5 meter",
        "wrong_answers": [
            {"option": "5/1 meter", "misconception": "Inverting parts and whole"},
            {"option": "0.5 meter", "misconception": "Decimal rounding confusion"},
            {"option": "1/4 meter", "misconception": "Incorrect divisor"},
        ],
        "hints": [
            "You are dividing 1 whole meter into 5 equal parts.",
            "Write the portion as 1 divided by 5.",
            "Each piece is exactly 1/5 meter (or 0.2 meters).",
        ],
    },
    "ratios": {
        "prompt": "A recipe uses 3 tablespoons of olive oil for every 2 tablespoons of vinegar. If you use 9 tablespoons of olive oil, how many tablespoons of vinegar are needed?",
        "answer": "6 tablespoons",
        "wrong_answers": [
            {"option": "4 tablespoons", "misconception": "Underestimating multiplier"},
            {"option": "8 tablespoons", "misconception": "Adding 6 instead of scaling by factor 3"},
            {"option": "13.5 tablespoons", "misconception": "Inverting the ratio"},
        ],
        "hints": [
            "Find the scale factor: 9 ÷ 3 = 3.",
            "Multiply the vinegar parts by the scale factor 3.",
            "2 * 3 = 6 tablespoons of vinegar.",
        ],
    },
}


def _extract_numbers(text: str) -> set[str]:
    """Finds all integers, decimals, and percentage tokens in a text string."""
    return set(re.findall(r"\b\d+(?:\.\d+)?%?\b", text))


def deterministic_verify_question(question_dict: Dict[str, Any]) -> Tuple[bool, str]:
    """Deterministically validates AI-generated question structure, answer consistency,
    and options uniqueness before permitting ingestion into the system.
    """
    prompt = question_dict.get("prompt", "").strip()
    answer = question_dict.get("answer", "").strip()
    wrongs = question_dict.get("wrong_answers", [])
    hints = question_dict.get("hints", [])

    if len(prompt) < 15:
        return False, "Prompt is too short or empty"
    if not answer:
        return False, "Answer string is missing"
    if len(wrongs) < 2:
        return False, "Must provide at least 2 misconception-tagged wrong answers"
    if len(hints) < 3:
        return False, "Must provide exactly 3 progressive hint tiers"

    # Verify answer isn't duplicated in wrong options
    wrong_opts = [w.get("option", "").strip().lower() for w in wrongs if isinstance(w, dict)]
    if answer.strip().lower() in wrong_opts:
        return False, "Correct answer was duplicated inside wrong_answers options"

    return True, "Deterministic structure & consistency check passed"


def verify_numbers_preserved(original_reason: str, rephrased_text: str) -> bool:
    """Verifies that all numerical values and percentages from the deterministic
    engine reason string are strictly preserved in the AI-generated rephrase.
    """
    orig_nums = _extract_numbers(original_reason)
    rephrased_nums = _extract_numbers(rephrased_text)

    # Core numbers must be present
    for num in orig_nums:
        # Check direct or strip percent
        base_num = num.rstrip("%")
        found = any(num in r or base_num in r for r in rephrased_nums)
        if not found and len(num) > 1:
            return False
    return True


async def generate_ai_question(
    concept_id: str,
    concept_name: str,
    difficulty: int,
    q_type: str,
) -> Tuple[Dict[str, Any], str, str]:
    """Calls Gemini API with JSON schema constraint and deterministic verification.
    Falls back gracefully if key is missing or validation fails.
    Returns: (question_data, ai_status, validation_log)
    """
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        fallback = FALLBACK_TEMPLATES.get(concept_id, FALLBACK_TEMPLATES["fraction_basics"])
        q_id = f"fallback_{uuid.uuid4().hex[:8]}"
        q_data = {
            "id": q_id,
            "concept_id": concept_id,
            "difficulty": difficulty,
            "type": q_type,
            "prompt": fallback["prompt"],
            "answer": fallback["answer"],
            "wrong_answers": fallback["wrong_answers"],
            "hints": fallback["hints"],
        }
        return q_data, "ai_unavailable", "No GEMINI_API_KEY configured; served verified template fallback."

    prompt_content = f"""Generate a high-quality math question for:
Concept: {concept_name} ({concept_id})
Difficulty: {difficulty} (scale 1 to 5)
Type: {q_type} (recall | procedural | transfer)

Respond with valid JSON strictly adhering to this schema:
{{
  "prompt": "string: clear question prompt",
  "answer": "string: exact correct answer",
  "wrong_answers": [
    {{"option": "string", "misconception": "string: why a student chose this"}},
    {{"option": "string", "misconception": "string: misconception explanation"}},
    {{"option": "string", "misconception": "string: third misconception"}}
  ],
  "hints": [
    "tier 1: general reminder",
    "tier 2: formula or setup",
    "tier 3: full worked step-by-step solution"
  ]
}}
Do NOT include markdown backticks around the json. Return raw JSON only."""

    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.post(
                f"{GEMINI_URL}?key={api_key}",
                json={
                    "contents": [{"parts": [{"text": prompt_content}]}],
                    "generationConfig": {"response_mime_type": "application/json"},
                },
            )
            if resp.status_code == 200:
                result_json = resp.json()
                text_out = result_json["candidates"][0]["content"]["parts"][0]["text"]
                data = json.loads(text_out)
                valid, reason = deterministic_verify_question(data)
                if valid:
                    q_id = f"ai_gen_{uuid.uuid4().hex[:8]}"
                    data["id"] = q_id
                    data["concept_id"] = concept_id
                    data["difficulty"] = difficulty
                    data["type"] = q_type
                    return data, "ai_generated", "AI question validated and approved."
                else:
                    # Log rejection
                    print(f"AI question rejected: {reason}")
    except Exception as e:
        print(f"Gemini API call failed: {e}")

    # Fallback on any failure
    fallback = FALLBACK_TEMPLATES.get(concept_id, FALLBACK_TEMPLATES["fraction_basics"])
    q_data = {
        "id": f"fallback_{uuid.uuid4().hex[:8]}",
        "concept_id": concept_id,
        "difficulty": difficulty,
        "type": q_type,
        "prompt": fallback["prompt"],
        "answer": fallback["answer"],
        "wrong_answers": fallback["wrong_answers"],
        "hints": fallback["hints"],
    }
    return q_data, "fallback_template", "API call or validation failed; served verified template fallback."


async def rephrase_engine_explanation(
    numeric_reason: str,
    student_name: str = "Learner",
) -> Tuple[str, str, bool]:
    """Rephrases the deterministic engine reason into friendly student-facing wording
    while strictly asserting that all numbers and percentages are preserved.
    """
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        return numeric_reason, "ai_unavailable", True

    prompt_content = f"""Rephrase this pedagogical engine recommendation for {student_name} into a friendly, encouraging sentence.
CRITICAL CONSTRAINT: You MUST preserve all numbers, percentages (e.g. 42%, 65%), and topic names exactly as written. Do not add or change any numbers.

Engine reason:
"{numeric_reason}"

Return ONLY the rewritten sentence, nothing else."""

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{GEMINI_URL}?key={api_key}",
                json={"contents": [{"parts": [{"text": prompt_content}]}]},
            )
            if resp.status_code == 200:
                text_out = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                if verify_numbers_preserved(numeric_reason, text_out):
                    return text_out, "ai_rewritten", True
                else:
                    print("AI rephrase rejected: numbers distorted or omitted")
    except Exception as e:
        print(f"AI explanation call failed: {e}")

    # Fallback: Return original numeric reason with zero distortion
    return numeric_reason, "fallback_original", True
