import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from app.medicare_final.mfds_api import MFDSClient, MFDSError, Page
from app.medicare_final.mfds_collector import collect, connect, export


class WorkspaceTemp:
    def __enter__(self):
        self.path = Path(tempfile.mkdtemp(prefix="test_collector_"))
        return self.path

    def __exit__(self, *args):
        shutil.rmtree(self.path)


class CollectorTests(unittest.TestCase):
    def test_resume_deduplicate_failure_and_export(self):
        with WorkspaceTemp() as tmp:
            db = connect(Path(tmp) / "cache.db")
            client = Mock()
            client.calls = 0

            def request(path, params, page, size):
                client.calls += 1
                if page == 1:
                    return Page(
                        [{"ITEM_SEQ": "123456789", "ITEM_NAME": "약정"}],
                        2,
                        page,
                        size,
                        "00",
                    )
                if page == 2:
                    return Page(
                        [{"ITEM_SEQ": "123456789", "ITEM_NAME": "약정"}],
                        2,
                        page,
                        size,
                        "00",
                    )
                raise MFDSError("network", "error")

            client._request.side_effect = request
            first = collect(db, client, "permit", max_pages=1, page_size=1)
            self.assertEqual(first["status"], "partial")
            second = collect(db, client, "permit", max_pages=1, page_size=1)
            self.assertEqual((second["status"], second["duplicates"]), ("complete", 1))
            self.assertEqual(export(db, Path(tmp) / "out")["permit"], 1)
            d = json.loads(
                (Path(tmp) / "out/drug_name_dictionary.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(d["items"][0]["item_seq"], "123456789")
            client._request.side_effect = MFDSError("network", "error")
            self.assertEqual(
                collect(db, client, "permit", mode="update", page_size=1)["status"],
                "error",
            )
            self.assertEqual(
                db.execute("select count(*) from records").fetchone()[0], 1
            )
            db.close()

    def test_xml_success(self):
        response = Mock(
            status_code=200,
            content=b"<response><header><resultCode>00</resultCode></header><body><totalCount>1</totalCount><items><item><itemSeq>123456789</itemSeq></item></items></body></response>",
        )
        session = Mock()
        session.get.return_value = response
        c = MFDSClient("key", session=session, retries=0, interval=0)
        self.assertEqual(c.easy(item_seq="123456789").items[0]["itemSeq"], "123456789")


if __name__ == "__main__":
    unittest.main()
