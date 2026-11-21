import json
from types import SimpleNamespace

from pydantic import SecretStr

from app.schemas.medications import IdentifiedMedicationDTO, MedicationScanResult
from app.services import precaution_rewriter as rw
from app.services.medication_presentation import present_scan

EFFECT = "이 약은 위ㆍ십이지장궤양, 위염, 장염, 방광염, 월경곤란에 사용합니다."
FULL = "이 약은 십이지장궤양, 위염, 장염, 방광염, 월경곤란(생리통)에 쓰는 약입니다."
# Shaped like the real permit text: a ban section, then cautions.
CAUTION = """사용상의주의사항

1. 다음 환자에는 투여하지 말 것

1) 녹내장 환자

2) 중증 간장애 환자

2. 다음 환자에는 신중히 투여할 것

1) 고령자

2) 65세 이상 신장애 환자"""
# e약은요 style prose ban, separate from the numbered list.
EASY = "임부 및 수유부는 이 약을 복용하지 마십시오.고령자는 상의하십시오."


def test_purpose_guard():
    assert rw.purpose_ok(FULL, EFFECT)
    assert not rw.purpose_ok("하루 3회 복용하면 위염에 좋습니다.", EFFECT)
    assert not rw.purpose_ok("위염을 완치하는 약입니다.", EFFECT)
    assert not rw.purpose_ok("위염 증상의 80%를 줄여 줍니다.", EFFECT)
    # Real HCX output that dropped most conditions must fall back to the source.
    assert not rw.purpose_ok(
        "이 약은 소화 기관 문제를 해결하는데 도움을 줍니다.", EFFECT
    )
    # A cure word is allowed when the official source itself uses it.
    assert rw.purpose_ok("위염 치료에 쓰는 약입니다.", "위염 치료")


def test_contraindications_are_split_out_verbatim():
    items, rest = rw.split_contraindications(CAUTION + "\n\n" + EASY)
    assert items == [
        "녹내장 환자",
        "중증 간장애 환자",
        "임부 및 수유부는 이 약을 복용하지 마십시오",
    ]
    assert "고령자" in rest and "상의하십시오" in rest
    assert "녹내장" not in rest and "수유부" not in rest


def test_precaution_guard_rejects_reclassified_ban():
    rest = rw.split_contraindications(CAUTION)[1]
    # Real HCX failure: a caution group ("고령자") turned into a ban.
    assert not rw.precautions_ok("고령자는 복용 금지입니다.", rest)
    assert not rw.precautions_ok("고령자는 먹으면 안 됩니다.", rest)
    assert not rw.precautions_ok("70세 이상은 상의하세요.", rest)
    assert rw.precautions_ok(
        "고령자나 65세 이상 신장애가 있으면 먼저 상의하세요.", rest
    )


def result():
    return MedicationScanResult(
        recognized_text="x",
        status="COMPLETED",
        identified_medications=[
            IdentifiedMedicationDTO(
                item_seq="1",
                item_name="가상정",
                recognized_names=["가상정"],
                summary=[{"text": EFFECT, "status": "official_excerpt"}],
                summary_status="OFFICIAL_EXCERPT",
                precautions=[{"official_text": CAUTION}],
            ),
            IdentifiedMedicationDTO(
                item_seq="2",
                item_name="다른정",
                recognized_names=["다른정"],
                summary=[{"text": "위염에 사용합니다.", "status": "official_excerpt"}],
                summary_status="OFFICIAL_EXCERPT",
                precautions=[{"official_text": CAUTION}],
            ),
        ],
    )


def fake_client(answers):
    class Client:
        session = SimpleNamespace(close=lambda: None)

        def __init__(self, **kwargs):
            pass

        def generate(self, messages, **kwargs):
            key = (
                "purpose" if messages[0]["content"] == rw.PURPOSE_PROMPT else "caution"
            )
            return {"answer": json.dumps(answers[key], ensure_ascii=False)}

    return Client


def test_rewrite_is_per_item_and_falls_back(monkeypatch):
    monkeypatch.setattr(
        rw,
        "ClovaClient",
        fake_client(
            {
                "purpose": {
                    "medications": [
                        {"item_seq": "1", "text": FULL},
                        # Fails the guard: dosing added. Item 2 keeps official text.
                        {"item_seq": "2", "text": "위염에 하루 3회 복용합니다."},
                    ]
                },
                "caution": {
                    "medications": [
                        {"item_seq": "1", "text": "고령자는 먼저 상의하세요."},
                        # Fails the guard: caution turned into a ban.
                        {"item_seq": "2", "text": "고령자는 복용 금지입니다."},
                    ]
                },
            }
        ),
    )
    settings = SimpleNamespace(
        CLOVA_API_KEY=SecretStr("k"),
        CLOVA_MODEL="HCX-005",
        AI_REQUEST_TIMEOUT_SECONDS=5,
    )
    cards = present_scan(rw.rewrite_guidance(result(), settings)).medications
    assert cards[0].description.purpose == FULL
    assert cards[0].description.precautions == (
        "이런 분은 복용하면 안 됩니다\n- 녹내장 환자\n- 중증 간장애 환자"
        "\n\n그 밖의 주의사항\n고령자는 먼저 상의하세요."
    )
    assert cards[1].description.purpose == "위염에 사용합니다."
    # Rejected rewrite: the full official text, bans included, is shown.
    assert cards[1].description.precautions == CAUTION


def test_missing_final_brace_is_repaired():
    class Client:
        def generate(self, *args, **kwargs):
            # Observed from HCX: final "}" missing although finish_reason=stop.
            return {
                "answer": json.dumps(
                    {"medications": [{"item_seq": "1", "text": FULL}]}
                )[:-1],
                "finish_reason": "stop",
            }

    out = rw._rewrite(Client(), rw.PURPOSE_PROMPT, {"1": EFFECT}, rw.purpose_ok)
    assert out == {"1": FULL}
