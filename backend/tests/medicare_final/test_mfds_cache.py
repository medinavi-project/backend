import json
import os
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import requests

from app.medicare_final.mfds_api import _network_detail
from app.medicare_final.mfds_collector import connect
from app.medicare_final.rag import build_drug_context
from tests.medicare_final.test_mfds_collector import WorkspaceTemp
from tests.medicare_final.test_rag import Fake

SEQ = "123456789"


class CacheIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = WorkspaceTemp()
        self.path = self.tmp.__enter__() / "cache.db"
        self.db = connect(self.path)
        stamp = datetime.now(UTC).isoformat()
        self.rows = {
            "permit": {"ITEM_SEQ": SEQ, "ITEM_NAME": "시험약정"},
            "permit_detail": {
                "ITEM_SEQ": SEQ,
                "ITEM_NAME": "시험약정",
                "NB_DOC_DATA": "주의 원문",
                "EE_DOC_DATA": "효능 원문",
            },
            "easy": {
                "itemSeq": SEQ,
                "itemName": "시험약정",
                "efcyQesitm": "효능 원문",
                "atpnQesitm": "주의 원문",
            },
            "ingredients": {"_items": [{"ITEM_SEQ": SEQ, "MTRAL_NM": "성분"}]},
        }
        for name, value in self.rows.items():
            self.db.execute(
                "INSERT INTO records VALUES (?,?,?,?)",
                (name, SEQ, json.dumps(value), stamp),
            )
        self.db.commit()
        self.env = patch.dict(
            os.environ,
            {
                "MFDS_CACHE_ENABLED": "true",
                "MFDS_CACHE_DB": str(self.path),
                "MFDS_CACHE_MAX_AGE_HOURS": "24",
                "MFDS_CACHE_API_FIRST": "false",
                "MFDS_CACHE_FORCE_REFRESH": "false",
            },
            clear=False,
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.db.close()
        self.tmp.__exit__(None, None, None)

    def test_fresh_cache_sources_and_dur_are_separate(self):
        r = build_drug_context([SEQ], "효능", client=Fake(fail=True))
        self.assertEqual(r["status"], "ready")
        payload = json.loads(r["context"])
        docs = payload["products"][0]["documents"]
        self.assertTrue(docs)
        self.assertTrue(
            all(
                payload["source_index"][d["source_id"]]["cache_status"] == "fresh"
                for d in docs
            )
        )
        self.assertEqual(payload["dur"][SEQ]["interaction"]["status"], "unknown")

    def test_stale_is_not_promoted_after_api_failure(self):
        old = (datetime.now(UTC) - timedelta(days=5)).isoformat()
        self.db.execute("UPDATE records SET retrieved_at=?", (old,))
        self.db.commit()
        r = build_drug_context([SEQ], "효능", client=Fake(fail=True))
        self.assertEqual(r["status"], "incomplete")
        self.assertIsNone(r["context"])
        self.assertTrue(
            any(
                x["source"] == "permit" and x["cache_status"] == "stale"
                for x in r["cached_evidence"]
            )
        )

    def test_api_first_conflict_and_missing_cache(self):
        os.environ["MFDS_CACHE_API_FIRST"] = "true"
        r = build_drug_context([SEQ], "효능", client=Fake())
        self.assertTrue(r["cache_conflicts"])
        self.db.execute("DELETE FROM records")
        self.db.commit()
        r = build_drug_context([SEQ], "효능", client=Fake(fail=True))
        self.assertEqual(r["status"], "incomplete")
        self.assertEqual(r["cached_evidence"], [])

    def test_mismatched_cache_code_and_inactive(self):
        self.db.execute(
            "UPDATE records SET payload=? WHERE source=?",
            (json.dumps({"ITEM_SEQ": "999999999"}), "permit_detail"),
        )
        self.db.commit()
        self.assertEqual(
            build_drug_context([SEQ], "효능", client=Fake(fail=True))["status"],
            "incomplete",
        )
        self.rows["permit_detail"]["CANCEL_NAME"] = "취하"
        self.db.execute(
            "UPDATE records SET payload=? WHERE source=?",
            (json.dumps(self.rows["permit_detail"]), "permit_detail"),
        )
        self.db.commit()
        self.assertEqual(
            build_drug_context([SEQ], "효능", client=Fake())["status"], "incomplete"
        )

    def test_proxy_diagnostic(self):
        self.assertEqual(
            _network_detail(requests.exceptions.ProxyError("secret")), "proxy_error"
        )


if __name__ == "__main__":
    unittest.main()
