from types import SimpleNamespace

from app.medicare_final.mfds_api import MFDSError
from app.services.drug_identity import corroborated_candidate, identity_note

# Cues as they appear (OCR-garbled) on the real sample bag.
BAG = "세프로바이정250밀리그램\n{항생제}\n세균에 의한 각종 김영증을\n(위산과다중약)"
CIPRO = {
    "item_seq": "111111111",
    "official_name": "씨프로바이정250밀리그램(시프로플록사신염산염수화물)",
    "permit_status": "취하",
}
OTHER = {
    "item_seq": "222222222",
    "official_name": "크로세프캡슐250밀리그램(세파클러수화물)",
    "permit_status": "정상",
}


class Client:
    def __init__(self, effects):
        self.effects, self.calls = effects, 0

    def permit_detail(self, item_seq, **kwargs):
        self.calls += 1
        return SimpleNamespace(items=[{"EE_DOC_DATA": self.effects[item_seq]}])


def found(*cands):
    return {"errors": [], "truncated": True, "candidates": list(cands)}


def test_typo_and_inactive_confirmed_when_bag_matches():
    client = Client({"111111111": "요로감염 등 세균 감염증"})
    pick, basis = corroborated_candidate(
        client, "세프로바이정250밀리그램", found(CIPRO, OTHER), BAG
    )
    assert pick is CIPRO and basis == "SIMILAR_NAME"
    note = identity_note("세프로바이정250밀리그램", pick, basis)
    assert "봉투의 효능 표시와 일치" in note and "'취하'" in note


def test_rejected_when_effects_do_not_match_bag():
    client = Client({"111111111": "고혈압"})
    assert not corroborated_candidate(
        client, "세프로바이정250밀리그램", found(CIPRO), BAG
    )


def test_rejected_without_clear_name_winner():
    close = {**OTHER, "official_name": "씨프로바이정500밀리그램(시프로플록사신)"}
    client = Client({})
    # Strength conflict on the closest name: no lookup, no match.
    assert not corroborated_candidate(
        client, "씨프로바이정250밀리그램", found(close), BAG
    )
    # Two names equally close: ambiguous.
    twin = {**CIPRO, "item_seq": "333333333"}
    assert not corroborated_candidate(
        client, "세프로바이정250밀리그램", found(CIPRO, twin), BAG
    )
    assert client.calls == 0


def test_exact_inactive_name_needs_bag_cue():
    ranid = {
        "item_seq": "444444444",
        "official_name": "라니드정(라니티딘염산염)",
        "permit_status": "유효기간만료",
    }
    client = Client({"444444444": "위ㆍ십이지장궤양, 위산과다"})
    assert corroborated_candidate(client, "라니드정", found(ranid), BAG) == (
        ranid,
        "EXACT_NAME",
    )
    assert not corroborated_candidate(client, "라니드정", found(ranid), "약제비 영수증")
    assert identity_note("라니드정", ranid, "EXACT_NAME").startswith(
        "현재 허가 상태가 '유효기간만료'"
    )


def test_api_error_is_not_a_match():
    class Failing:
        def permit_detail(self, *a, **k):
            raise MFDSError("network", "x")

    assert not corroborated_candidate(
        Failing(), "세프로바이정250밀리그램", found(CIPRO), BAG
    )
