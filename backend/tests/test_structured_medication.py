import json

from app.medicare.structured_answer import validate_structured


def test_source_linked_summary_and_protected_rejection():
    context = {
        "products": [
            {
                "item_seq": "1",
                "documents": [
                    {"source_id": "effect", "field": "effects", "text": "공식 효능"},
                    {"source_id": "dose", "field": "dosage", "text": "용법"},
                ],
            }
        ],
        "source_index": {"effect": {}, "dose": {}},
    }

    def answer(text, source="effect"):
        return json.dumps(
            {
                "medications": [
                    {
                        "item_seq": "1",
                        "summary": [{"text": text, "source_ids": [source]}],
                    }
                ]
            }
        )

    assert (
        validate_structured(answer("통증을 줄이는 데 쓰입니다."), context)["status"]
        == "structured_source_linked"
    )
    assert (
        validate_structured(answer("하루 3회 복용합니다."), context)["status"]
        == "review_required"
    )
    assert (
        validate_structured(answer("설명", "dose"), context)["status"]
        == "review_required"
    )
    assert (
        validate_structured(answer("설명", "invented"), context)["status"]
        == "review_required"
    )
