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


def test_bag_line_formats_keep_name_drop_dosing_columns():
    lines = {
        "가상정500mg 1 3 3": "가상정500mg",
        "1. 가상정500mg": "가상정500mg",
        "가상 정 500mg": "가상 정 500mg",
        "가상정5/50밀리그램": "가상정5/50밀리그램",
        "[1]다른약정 1정 3회 3일": "다른약정",
        "가상정500mg(성분명)": "가상정500mg",
    }
    for line, expected in lines.items():
        found = extract_candidates(line)["candidates"]
        assert [r["raw_text"] for r in found] == [expected], line
    assert extract_candidates("가상 정 500mg")["candidates"][0]["search_name"] == (
        "가상정500mg"
    )


def test_identity_words_on_line_are_flagged_not_dropped():
    found = extract_candidates("1. 가상정5mg 처방 1일 3회")["candidates"]
    assert found[0]["raw_text"] == "가상정5mg"
    assert found[0]["identity_words_on_line"] is True


def test_dictionary_noise_and_generic_words_are_not_candidates():
    from app.services.ocr_candidate_adapter import extract_candidates

    text = (
        "해열제 시럽은 라벨에 적힌 대로 드시고 임의로 늘리지 마세요\n"
        "해열제 시럽\n"
        "세프로바이정250밀리그램 1정\n"
    )
    names = [c["raw_text"] for c in extract_candidates(text)["candidates"]]
    assert names == ["세프로바이정250밀리그램"]
