import json
import unittest
from unittest.mock import patch

from app.medicare_final.medication_pipeline import get_medication_info, parse_summary
from app.medicare_final.rag import build_drug_context
from tests.medicare_final.test_rag import Fake

SEQ = "195700020"
SID = "MFDS-" + "a" * 20
SID2 = "MFDS-" + "b" * 20
DOC = "이 약은 식욕감퇴(식욕부진), 위부팽만감, 소화불량, 과식, 체함, 구역, 구토에 사용합니다."
EFFECTS = {SEQ: {SID: DOC, SID2: "공식 효능 원문입니다."}}


def response(text, source=SID, seq=SEQ):
    return json.dumps(
        {
            "medications": [
                {"item_seq": seq, "summary": [{"text": text, "source_ids": [source]}]}
            ]
        },
        ensure_ascii=False,
    )


class FinalNaturalTests(unittest.TestCase):
    def test_exact_controlled_and_two_documents(self):
        self.assertEqual(
            parse_summary(response(DOC), EFFECTS)["status"], "verified_extractive"
        )
        r = parse_summary(response("이 약은 소화불량에 사용합니다."), EFFECTS)
        self.assertEqual(r["status"], "supported_with_checks")
        self.assertEqual(
            r["summaries"][0]["rule"]["medical_expert_review"], "not_performed"
        )
        value = {
            "medications": [
                {
                    "item_seq": SEQ,
                    "summary": [
                        {"text": DOC, "source_ids": [SID]},
                        {"text": "공식 효능 원문입니다.", "source_ids": [SID2]},
                    ],
                }
            ]
        }
        self.assertEqual(
            parse_summary(json.dumps(value), EFFECTS)["status"], "verified_extractive"
        )

    def test_risky_claims_are_not_approved(self):
        claims = [
            "이 약은 암을 완치합니다.",
            "소화불량 질환을 완치합니다.",
            "이 약은 위염을 치료합니다.",
            "금기인 사람도 복용할 수 있습니다.",
            "이 약은 1일 3회 복용합니다.",
            "이 약은 소화불량에 5mg을 사용합니다.",
            "소화불량과 우울증에 사용합니다.",
            "이 약은 위장 질환을 치료합니다.",
            "이 약은 소화불량에 사용하지 않습니다.",
        ]
        for text in claims:
            with self.subTest(text=text):
                self.assertNotIn(
                    parse_summary(response(text), EFFECTS)["status"],
                    ("verified_extractive", "supported_with_checks"),
                )
        for value in (
            response(DOC, "MFDS-" + "f" * 20),
            response(DOC, SID, "123456789"),
            "{",
        ):
            self.assertEqual(parse_summary(value, EFFECTS)["status"], "rejected")

    def test_rule_rejects_different_source_context(self):
        altered = {SEQ: {SID: DOC + " 단, 특정 환자는 제외합니다."}}
        self.assertEqual(
            parse_summary(response("이 약은 소화불량에 사용합니다."), altered)[
                "status"
            ],
            "review_required",
        )

    # Backend policy (aigent.md): verified sentences stay visible per item even when
    # the overall context is partial. Upstream hid all of them.
    def test_partial_status_keeps_verified_output(self):
        ctx = build_drug_context(["123456789"], "효능", client=Fake())
        sid = ctx["products"][0]["effects"][0]["source_id"]
        text = ctx["products"][0]["effects"][0]["text"]

        class Model:
            def generate(self, *args, **kwargs):
                return {"answer": response(text, sid, "123456789")}

        ctx["status"] = "incomplete"
        with patch(
            "app.medicare_final.medication_pipeline.build_drug_context",
            return_value=ctx,
        ):
            r = get_medication_info(
                ["123456789"], model_client=Model(), allow_generation=True
            )
        self.assertEqual(r["validation_result"]["status"], "verified_extractive")
        self.assertEqual(r["ai_summary"][0]["text"], text)
        self.assertTrue(r["official_drug_info"])
