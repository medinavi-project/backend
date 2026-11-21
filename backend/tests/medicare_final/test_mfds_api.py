import json
import unittest
from unittest.mock import Mock

import requests

from app.medicare_final.mfds_api import MFDSClient, MFDSError


def response(data, status=200):
    return Mock(
        status_code=status, content=json.dumps(data, ensure_ascii=False).encode("utf-8")
    )


class APITests(unittest.TestCase):
    def client(self, value):
        session = Mock()
        session.get.return_value = value
        return MFDSClient("test-key", session=session, retries=0, interval=0), session

    def test_unicode_and_parameters(self):
        c, session = self.client(
            response(
                {
                    "header": {"resultCode": "00"},
                    "body": {
                        "totalCount": 1,
                        "items": [{"ITEM_NAME": "활명수", "ITEM_SEQ": "195700020"}],
                    },
                }
            )
        )
        self.assertEqual(c.permit_list(name="활명수").items[0]["ITEM_NAME"], "활명수")
        self.assertEqual(session.get.call_args.kwargs["params"]["item_name"], "활명수")
        c.permit_detail("195700020")
        self.assertEqual(
            session.get.call_args.kwargs["params"]["item_seq"], "195700020"
        )
        c.ingredients("195700020")
        self.assertEqual(
            session.get.call_args.kwargs["params"]["Item_seq"], "195700020"
        )

    def test_mismatched_item_code_is_error(self):
        c, _ = self.client(
            response(
                {
                    "header": {"resultCode": "00"},
                    "body": {"totalCount": 1, "items": [{"ITEM_SEQ": "wrong"}]},
                }
            )
        )
        with self.assertRaises(MFDSError):
            c.permit_detail("195700020")

    def test_empty_and_auth(self):
        c, _ = self.client(
            response({"header": {"resultCode": "00"}, "body": {"totalCount": 0}})
        )
        self.assertEqual(c.easy(name="없는약").status, "no_results")
        c, _ = self.client(response({"header": {"resultCode": "30"}}))
        with self.assertRaises(MFDSError) as ctx:
            c.easy(name="약")
        self.assertEqual(ctx.exception.category, "authentication")

    def test_network_and_budget(self):
        c, session = self.client(None)
        session.get.side_effect = requests.ConnectionError("secret-url")
        with self.assertRaises(MFDSError) as ctx:
            c.easy(name="약")
        self.assertEqual(ctx.exception.category, "network")
        self.assertNotIn("secret-url", str(ctx.exception))
        c.max_calls = 1
        with self.assertRaises(MFDSError) as ctx:
            c.easy(name="약")
        self.assertEqual(ctx.exception.category, "budget_exceeded")

    def test_pages(self):
        c, session = self.client(
            response(
                {
                    "header": {"resultCode": "00"},
                    "body": {"totalCount": 4, "items": [{"id": 1}, {"id": 2}]},
                }
            )
        )
        self.assertEqual(len(list(c.pages(c.easy, name="약", page_size=2))), 2)
        self.assertEqual(session.get.call_count, 2)


if __name__ == "__main__":
    unittest.main()
