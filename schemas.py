import json
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator


class SymptomsFound(BaseModel):
    skin_patch: str
    numbness: str
    hand_foot_weakness: str


class RiskAssessment(BaseModel):
    level: Literal["LOW", "MEDIUM", "HIGH", "EMERGENCY"]
    reason: str


class ActionRequired(BaseModel):
    send_health_worker: bool
    patient_sms_advice: str


class ReviewReport(BaseModel):
    language_used: str
    symptoms_found: SymptomsFound
    risk_assessment: RiskAssessment
    action_required: ActionRequired

    @field_validator("language_used", mode="before")
    @classmethod
    def normalize_language(cls, value: str) -> str:
        return str(value).strip() or "Undetermined"


def strip_json_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned.replace("```json", "", 1)
    elif cleaned.startswith("```"):
        cleaned = cleaned.replace("```", "", 1)
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()


def parse_review_report(raw_text: str) -> ReviewReport:
    cleaned = strip_json_fences(raw_text)
    data = json.loads(cleaned)
    return ReviewReport.model_validate(data)


def rule_based_review(transcript: str) -> ReviewReport:
    lower = transcript.lower()
    emergency = any(
        phrase in lower
        for phrase in (
            "high fever",
            "painful red lumps",
            "painful lumps",
            "swollen red lumps",
            "severe pain",
            "तेज बुखार",
            "लाल गांठ",
        )
    )
    high_risk = any(
        phrase in lower
        for phrase in (
            "numb",
            "no feeling",
            "painless burn",
            "painless injury",
            "slipper slip",
            "chappal slip",
            "सुन्न",
            "बिना दर्द",
        )
    )
    low_risk = any(
        phrase in lower
        for phrase in ("itchy", "since birth", "allergy", "खुजली", "जन्म से")
    )

    if emergency:
        level = "EMERGENCY"
        reason = "Caller reported fever with painful lumps or sudden severe symptoms."
        dispatch = True
    elif high_risk:
        level = "HIGH"
        reason = "Caller reported numbness, painless injury, or nerve weakness signs."
        dispatch = True
    elif low_risk:
        level = "LOW"
        reason = "Caller symptoms match common skin issues with low nerve-disease risk."
        dispatch = False
    else:
        level = "MEDIUM"
        reason = "Symptoms were unclear; a precautionary clinic check is recommended."
        dispatch = True

    return ReviewReport(
        language_used="Undetermined",
        symptoms_found=SymptomsFound(
            skin_patch="See transcript",
            numbness="See transcript",
            hand_foot_weakness="See transcript",
        ),
        risk_assessment=RiskAssessment(level=level, reason=reason),
        action_required=ActionRequired(
            send_health_worker=dispatch,
            patient_sms_advice=(
                "Thank you for contacting the National Health Helpline. "
                "Please visit your nearest government Primary Health Centre for a free check-up."
            ),
        ),
    )


def safe_parse_review(raw_text: str, transcript: str) -> ReviewReport:
    try:
        return parse_review_report(raw_text)
    except (json.JSONDecodeError, ValidationError, TypeError):
        return rule_based_review(transcript)
