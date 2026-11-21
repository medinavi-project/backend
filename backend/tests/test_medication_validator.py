from app.medicare.response_validator import validate_response

S = "MFDS-" + "a" * 20
W = "MFDS-" + "b" * 20


def context():
    return {
        "products": [
            {
                "documents": [
                    {"source_id": S, "field": "effects", "text": "통증을 완화한다."},
                    {
                        "source_id": W,
                        "field": "precautions",
                        "text": "12세 미만은 복용하지 않는다.",
                    },
                ]
            }
        ],
        "source_index": {S: {}, W: {}},
    }


def test_general_paraphrase_without_repeating_full_warning():
    result = validate_response(f"통증을 줄이는 데 쓰이는 약입니다. [{S}]", context())
    assert result["status"] == "source_linked_summary"
    assert result["medical_expert_review"] == "not_performed"


def test_changed_safety_and_unknown_source_rejected():
    assert (
        validate_response(f"6세 이상 복용 가능합니다. [{W}]", context())["status"]
        == "review_required"
    )
    assert (
        validate_response("통증 설명 [MFDS-" + "c" * 20 + "]", context())["status"]
        == "review_required"
    )
    assert validate_response("출처 없는 설명", context())["status"] == "review_required"


def test_safety_exact_quote_accepted():
    assert (
        validate_response(f"12세 미만은 복용하지 않는다. [{W}]", context())["status"]
        == "source_linked_summary"
    )
