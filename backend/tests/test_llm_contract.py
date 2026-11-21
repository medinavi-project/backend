from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.schemas.ai import ChatOutput


def asking():
    return {
        "action": "ASK_FOLLOW_UP",
        "symptom_updates": {"onset": "어제"},
        "answered_fields": ["onset"],
        "next_question": "강도는요?",
        "analysis": None,
    }


def completed():
    return {
        "action": "COMPLETE",
        "symptom_updates": {"severity": 0, "additional_symptoms": []},
        "answered_fields": ["severity", "additional_symptoms"],
        "next_question": None,
        "analysis": {
            "assessment_status": "INSUFFICIENT_INFORMATION",
            "symptom_summary": "아직 부족한 정보가 있습니다.",
            "suspected_diseases": [],
            "recommended_department": None,
        },
    }


@pytest.mark.parametrize(
    "field",
    ["action", "symptom_updates", "answered_fields", "next_question", "analysis"],
)
def test_every_top_level_field_is_required(field):
    payload = asking()
    del payload[field]
    with pytest.raises(ValidationError):
        ChatOutput.model_validate(payload)


@pytest.mark.parametrize(
    "patch",
    [
        {"action": "UNKNOWN"},
        {"next_question": None},
        {"next_question": "   "},
        {"analysis": completed()["analysis"]},
        {"answered_fields": ["onset", "onset"]},
        {"answered_fields": ["not_a_field"]},
        {"answered_fields": ["severity"]},
        {"symptom_updates": {"onset": None}},
        {"symptom_updates": {"onset": "어제", "severity": 11}},
        {"symptom_updates": {"onset": "어제", "severity": "2"}},
        {"symptom_updates": {"onset": "어제", "severity": True}},
        {"symptom_updates": {"onset": "어제", "additional_symptoms": None}},
        {"symptom_updates": {"onset": "어제", "invented_field": "x"}},
        {"extra_field": "x"},
    ],
)
def test_invalid_intermediate_contract_is_rejected(patch):
    payload = asking() | deepcopy(patch)
    with pytest.raises(ValidationError):
        ChatOutput.model_validate(payload)


@pytest.mark.parametrize(
    "patch",
    [
        {"next_question": "질문"},
        {"analysis": None},
        {"analysis": {**completed()["analysis"], "follow_up_question": "구버전 필드"}},
        {
            "analysis": {
                **completed()["analysis"],
                "suspected_diseases": [{"disease_name": "", "reason": "근거"}],
            }
        },
        {"analysis": {**completed()["analysis"], "recommended_department": ""}},
    ],
)
def test_invalid_final_contract_is_rejected(patch):
    with pytest.raises(ValidationError):
        ChatOutput.model_validate(completed() | deepcopy(patch))


def test_sparse_updates_preserve_zero_and_explicit_empty_array():
    output = ChatOutput.model_validate(completed())
    assert output.symptom_updates.model_dump(exclude_unset=True) == {
        "severity": 0,
        "additional_symptoms": [],
    }
    assert output.analysis.assessment_status == "INSUFFICIENT_INFORMATION"
    output = ChatOutput.model_validate(
        asking() | {"symptom_updates": {"onset": None}, "answered_fields": []}
    )
    assert output.symptom_updates.onset is None
