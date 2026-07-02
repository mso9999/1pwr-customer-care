import datetime
import unittest
from unittest.mock import patch

import customer_direct_messages as cdm
from fastapi import Response


def _fake_customer(account_number: str):
    from models import CurrentUser, UserType

    return CurrentUser(user_type=UserType.customer, user_id=account_number, role="customer", name="Test")


class _Cur:
    def __init__(self, resolve_row=None, insert_id=None, rows=()):
        self._resolve_row = resolve_row
        self._insert_id = insert_id
        self._rows = list(rows)

    def execute(self, sql, params=None):
        self._last_sql = sql

    def fetchone(self):
        if self._resolve_row is not None:
            r = self._resolve_row
            self._resolve_row = None
            return r
        if self._insert_id is not None:
            r = (self._insert_id,)
            self._insert_id = None
            return r
        return None

    def fetchall(self):
        return list(self._rows)


class _Conn:
    def __init__(self, cur):
        self._cur = cur

    def cursor(self):
        return self._cur

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestDirectMessages(unittest.TestCase):
    def test_lookup_returns_recipient(self):
        cur = _Cur(resolve_row=("0002SAM", "Jane", "Doe", "100000000"))
        conn = _Conn(cur)
        with patch("customer_api.get_connection", return_value=conn):
            with patch.object(cdm, "_ensure_table"):
                user = _fake_customer("0001SAM")
                body = cdm.lookup_customer(q="0002SAM", user=user)
        self.assertEqual(body["recipient"]["account_number"], "0002SAM")

    def test_lookup_404_when_missing(self):
        cur = _Cur(resolve_row=None)
        conn = _Conn(cur)
        with patch("customer_api.get_connection", return_value=conn):
            with patch.object(cdm, "_ensure_table"):
                user = _fake_customer("0001SAM")
                with self.assertRaises(Exception):
                    cdm.lookup_customer(q="nosuch", user=user)

    def test_lookup_rejects_self(self):
        cur = _Cur(resolve_row=("0001SAM", "Test", "", "100000000"))
        conn = _Conn(cur)
        with patch("customer_api.get_connection", return_value=conn):
            with patch.object(cdm, "_ensure_table"):
                user = _fake_customer("0001SAM")
                with self.assertRaises(Exception):
                    cdm.lookup_customer(q="0001SAM", user=user)

    def test_send_direct_inserts_without_wa_when_toggle_off(self):
        cur = _Cur(resolve_row=("0002SAM", "Jane", "Doe", "100000000"), insert_id=5)
        conn = _Conn(cur)
        with patch("customer_api.get_connection", return_value=conn):
            with patch.object(cdm, "_ensure_table"):
                with patch("cc_bridge_notify.notify_cc_bridge") as notify:
                    user = _fake_customer("0001SAM")
                    req = cdm.DirectMessageCreate(
                        to_customer="0002SAM", body="Hi", mirror_to_whatsapp=False
                    )
                    result = cdm.send_direct_message(req, user=user)
        self.assertEqual(result["id"], 5)
        self.assertEqual(result["delivery_status"], "sent")
        notify.assert_not_called()

    def test_send_direct_wa_mirrored_when_phone_present(self):
        cur = _Cur(resolve_row=("0002SAM", "Jane", "Doe", "100000000"), insert_id=6)
        conn = _Conn(cur)
        with patch("country_config.COUNTRY") as country:
            country.code = "BN"
            with patch("customer_api.get_connection", return_value=conn):
                with patch.object(cdm, "_ensure_table"):
                    with patch("cc_bridge_notify.notify_cc_bridge") as notify:
                        user = _fake_customer("0001SAM")
                        req = cdm.DirectMessageCreate(
                            to_customer="0002SAM", body="Hi", mirror_to_whatsapp=True
                        )
                        result = cdm.send_direct_message(req, user=user)
        self.assertEqual(result["delivery_status"], "sent")
        notify.assert_called_once()

    def test_send_direct_wa_no_phone_status(self):
        cur = _Cur(resolve_row=("0002SAM", "Jane", "Doe", None), insert_id=7)
        conn = _Conn(cur)
        with patch("customer_api.get_connection", return_value=conn):
            with patch.object(cdm, "_ensure_table"):
                with patch("cc_bridge_notify.notify_cc_bridge") as notify:
                    user = _fake_customer("0001SAM")
                    req = cdm.DirectMessageCreate(
                        to_customer="0002SAM", body="Hi", mirror_to_whatsapp=True
                    )
                    result = cdm.send_direct_message(req, user=user)
        self.assertEqual(result["delivery_status"], "wa_no_phone")
        notify.assert_not_called()

    def test_list_direct_messages(self):
        rows = [
            (1, "0001SAM", "0002SAM", "100000000", "Hi", False, "sent",
             datetime.datetime(2026, 7, 2)),
        ]
        cur = _Cur(insert_id=1, rows=rows)  # fetchone -> total count
        conn = _Conn(cur)
        with patch("customer_api.get_connection", return_value=conn):
            with patch.object(cdm, "_ensure_table"):
                user = _fake_customer("0001SAM")
                body = cdm.list_direct_messages(
                    Response(), limit=50, offset=0, user=user
                )
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["messages"][0]["direction"], "outbound")


if __name__ == "__main__":
    unittest.main()
