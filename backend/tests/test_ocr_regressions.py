from types import SimpleNamespace

from app.services.drug_identity import resolve_exact_scope, select_candidate
from app.services.ocr_candidate_adapter import extract_candidates


def test_noise_and_truncated_strength_regression():
    text = "약제비총액\n총수납금액\n완화시키고\n씨프로바이정250일리그럼\n*부스코판당의정\n가상캅셀"
    result = extract_candidates(text)
    assert [r["raw_text"] for r in result["candidates"]] == [
        "씨프로바이정250일리그럼",
        "부스코판당의정",
        "가상캅셀",
    ]
    for row in result["candidates"]:
        assert (
            text[row["evidence"]["start"] : row["evidence"]["end"]] == row["raw_text"]
        )


def test_complete_exact_query_resolves_broad_truncation():
    class Client:
        permit_list = None

        def pages(self, *args, **kwargs):
            yield SimpleNamespace(
                items=[
                    {
                        "ITEM_SEQ": "123456789",
                        "ITEM_NAME": "가상정(성분)",
                        "CANCEL_NAME": "정상",
                    }
                ],
                page=1,
                page_size=100,
                total_count=1,
            )

    result = resolve_exact_scope(
        Client(), "가상정", {"errors": [], "truncated": True, "candidates": []}
    )
    assert select_candidate("가상정", result)["item_seq"] == "123456789"


def test_unseen_forms_and_strengths_are_preserved():
    names = [
        "테스트서방정10mg",
        "다른약캡슐0.5mg",
        "가상연고",
        "무관크림",
        "별도정25일리그럼",
    ]
    result = extract_candidates("\n".join(names))
    assert [r["raw_text"] for r in result["candidates"]] == names


def test_exact_query_still_rejects_incomplete_search():
    class Client:
        permit_list = None

        def pages(self, *args, **kwargs):
            yield SimpleNamespace(
                items=[
                    {
                        "ITEM_SEQ": "123456789",
                        "ITEM_NAME": "가상정",
                        "CANCEL_NAME": "정상",
                    }
                ],
                page=1,
                page_size=100,
                total_count=250,
            )

    found = {"errors": [], "truncated": True, "candidates": []}
    assert resolve_exact_scope(Client(), "가상정", found) == found
