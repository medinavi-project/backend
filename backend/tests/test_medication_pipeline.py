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
