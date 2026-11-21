from unittest.mock import MagicMock

import pytest

from app.core.config import Settings
from app.services.medication_scan_service import MedicationScanService


@pytest.mark.parametrize("ready", [True, False])
def test_final_module_contract(monkeypatch, ready):
    from app.medicare_final import clova_client, medication_pipeline, mfds_api

    client = MagicMock()
    model = MagicMock()
    monkeypatch.setattr(mfds_api, "MFDSClient", lambda **k: client)
    monkeypatch.setattr(clova_client, "ClovaClient", lambda **k: model)
    result = {
        "status": "ready" if ready else "partial",
        "official_drug_info": [{"item_seq": "123456789"}],
        "ai_summary": [
            {
                "item_seq": "123456789",
                "text": "검증된 설명",
                "status": "supported_with_checks",
            }
        ],
        "validation_result": {"status": "supported_with_checks"},
        "dur_result": {"pairs": [{"status": "unknown"}]},
    }
    get = MagicMock(return_value=result)
    monkeypatch.setattr(medication_pipeline, "get_medication_info", get)

    class Search:
        def search(self, *a):
            return {
                "errors": [],
                "truncated": False,
                "candidates": [
                    {
                        "item_seq": "123456789",
                        "official_name": "시연약정5mg",
                        "permit_status": "정상",
                    }
                ],
            }

    out = MedicationScanService(
        Settings(_env_file=None, MFDS_API_KEY="test", CLOVA_API_KEY="test"), Search()
    )._analyze("시연약정5mg")
    assert out.status == ("COMPLETED" if ready else "PARTIAL")
    assert bool(out.ai_summary) is True
    model.generate.assert_not_called()
    get.assert_called_once()
    client.session.close.assert_called_once()


def test_embedded_text_not_sent_for_search(monkeypatch):
    from app.medicare_final import mfds_api

    monkeypatch.setattr(mfds_api, "MFDSClient", lambda **k: MagicMock())
    search = MagicMock()
    out = MedicationScanService(
        Settings(_env_file=None, MFDS_API_KEY="test"), search
    )._analyze("성명: 가상약정 5mg")
    search.search.assert_not_called()
    assert out.status == "NEEDS_CONFIRMATION"


def test_identity_line_candidate_not_sent_for_search(monkeypatch):
    from app.medicare_final import mfds_api

    monkeypatch.setattr(mfds_api, "MFDSClient", lambda **k: MagicMock())
    search = MagicMock()
    out = MedicationScanService(
        Settings(_env_file=None, MFDS_API_KEY="test"), search
    )._analyze("가상정5mg 처방")
    search.search.assert_not_called()
    assert out.medications[0].review_reason == "PRIVACY_REVIEW_REQUIRED"


def test_bag_corroborated_inactive_typo_reaches_public_card(monkeypatch):
    from types import SimpleNamespace

    from app.medicare_final import medication_pipeline, mfds_api
    from app.services.medication_presentation import present_scan

    client = MagicMock()
    client.permit_detail.return_value = SimpleNamespace(
        items=[{"EE_DOC_DATA": "세균 감염증"}]
    )
    monkeypatch.setattr(mfds_api, "MFDSClient", lambda **k: client)
    monkeypatch.setattr(
        medication_pipeline,
        "get_medication_info",
        MagicMock(
            return_value={
                "status": "ready",
                "official_drug_info": [{"item_seq": "111111111", "effects": []}],
            }
        ),
    )

    class Search:
        def search(self, *a):
            return {
                "errors": [],
                "truncated": False,
                "candidates": [
                    {
                        "item_seq": "111111111",
                        "official_name": "씨프로바이정250밀리그램(시프로플록사신)",
                        "permit_status": "취하",
                    }
                ],
            }

    out = MedicationScanService(
        Settings(_env_file=None, MFDS_API_KEY="test"), Search()
    )._analyze("세프로바이정250밀리그램\n{항생제}")
    assert out.medications[0].review_reason == "BAG_CORROBORATED_SIMILAR_NAME"
    card = present_scan(out).medications[0]
    assert card.item_seq == "111111111"
    assert "'취하'" in card.warning and "자동으로 찾은 약" in card.warning
