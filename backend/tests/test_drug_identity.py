from app.medicare_final.drug_search import normalize
from app.services.drug_identity import ranked_suggestions, select_candidate


def candidate(name, seq="1"):
    return {"official_name": name, "item_seq": seq, "permit_status": "정상"}


def test_parenthetical_ingredient_and_units():
    item = candidate("시연정500밀리그람(가상성분)")
    assert select_candidate("시연정 500mg", {"candidates": [item]}) == item
    assert normalize("10마이크로그램") == "10mcg"


def test_strength_ambiguity_and_typo_not_auto_confirmed():
    item = candidate("시연정500mg")
    assert select_candidate("시연정50mg", {"candidates": [item]}) is None
    assert select_candidate("시언정500mg", {"candidates": [item]}) is None
    assert (
        select_candidate(
            "시연정500mg", {"candidates": [item, candidate("시연정500mg", "2")]}
        )
        is None
    )
    assert (
        select_candidate("시연정500mg", {"candidates": [item], "truncated": True})
        is None
    )
    wrong = candidate("시언정50mg")
    assert ranked_suggestions("시언정500mg", [wrong, item])[0] == item


def test_similarity_prefix_fallback_and_conflict_metadata():
    from app.services.drug_identity import candidate_details, search_with_similarity

    calls = []
    item = candidate("시연약품정500mg")

    def search(client, query, **kwargs):
        calls.append(query)
        return {
            "errors": [],
            "truncated": False,
            "candidates": [] if query == "시연약품졍500mg" else [item],
        }

    result = search_with_similarity(None, "시연약품졍500mg", search)
    assert result["candidates"] == [item]
    assert len(calls) <= 3
    details = candidate_details("시연약품졍500mg", item)
    assert details["similarity_score"] >= 70
    assert details["match_reason"] == "SIMILAR_NAME_REQUIRES_CONFIRMATION"
    assert (
        candidate_details("시연약품정50mg", item)["match_reason"] == "STRENGTH_CONFLICT"
    )
