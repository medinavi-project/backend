import json
from unittest.mock import MagicMock

from app.medicare import medicare


def test_dur_unknown_does_not_block_effects(monkeypatch):
    sid = "MFDS-" + "a" * 20
    product = {
        "item_seq": "123456789",
        "drug_name": "테스트약",
        "source": {"permit_list": {"status": "ok", "total_count": 1}},
        "warnings": [],
        "precautions": [],
        "interactions": [],
        "raw_documents": [],
        "documents": [{"source_id": sid, "field": "effects", "text": "공식 효능"}],
    }
    monkeypatch.setattr(
        medicare,
        "build_drug_context",
        lambda *a, **k: {
            "status": "incomplete",
            "products": [product],
            "sources": {sid: {"url": "https://example.test"}},
            "reasons": ["병용 관계 unknown"],
            "dur_selected": {},
            "pairs_selected": [{"status": "unknown", "records": []}],
        },
    )
    client = MagicMock()
    client.model = "HCX-005"
    client.tokenize.return_value = {"content_tokens": 100, "latency": 0}
    client.generate.return_value = {
        "answer": json.dumps(
            {
                "medications": [
                    {
                        "item_seq": "123456789",
                        "summary": [{"text": "효능 설명입니다.", "source_ids": [sid]}],
                    }
                ]
            }
        )
    }
    result = medicare.generate_medication_answer(
        ["123456789"],
        "효능",
        clova_client=client,
        allow_tokenizer=True,
        allow_generation=True,
    )
    assert result["status"] == "ok"
    assert result["dur_result"]["pairs"][0]["status"] == "unknown"
    payload = json.loads(
        client.generate.call_args.args[0][1]["content"].split("\n", 1)[1]
    )
    assert all(
        d["field"] == "effects" for p in payload["products"] for d in p["documents"]
    )
    assert "dur" not in payload
