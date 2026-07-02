import datetime
import unittest
from unittest.mock import patch

import app_notifications
from fastapi import Response


def _fake_customer(account_number: str):
    from models import CurrentUser, UserType

    return CurrentUser(user_type=UserType.customer, user_id=account_number, role="customer", name="Test")


class _Cur:
    def __init__(self, rows=(), fetchone_values=None):
        self._rows = list(rows)
        self._fone = list(fetchone_values or [])
        self.rowcount = 0

    def execute(self, sql, params=None):
        self._last_sql = sql

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        if self._fone:
            v = self._fone.pop(0)
            self.rowcount = 1
            return v
        return None


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


class TestAppNotifications(unittest.TestCase):
    def test_mirror_to_app_inserts_and_dispatches_fcm(self):
        cur = _Cur(fetchone_values=[(1,)])  # RETURNING id
        conn = _Conn(cur)
        with patch("customer_api.get_connection", return_value=conn):
            with patch.object(app_notifications, "_ensure_tables"):
                with patch.object(app_notifications, "_dispatch_fcm", return_value="sent") as fcm:
                    app_notifications.mirror_to_app(
                        "0001SAM", "payment_receipt", "1PWR", "msg", {"amount": 5}
                    )
        fcm.assert_called_once()

    def test_mirror_to_app_noop_without_account(self):
        # No DB call should happen; just ensure it does not raise.
        app_notifications.mirror_to_app(None, "x", "t", "b")

    def test_list_notifications_returns_items_and_counts(self):
        rows = [
            (1, "payment_receipt", "1PWR", "msg", None,
             datetime.datetime(2026, 7, 2), None, "sent"),
        ]
        cur = _Cur(rows=rows, fetchone_values=[(1,), (0,)])  # total, unread
        conn = _Conn(cur)
        with patch("customer_api.get_connection", return_value=conn):
            with patch.object(app_notifications, "_ensure_tables"):
                user = _fake_customer("0001SAM")
                response = Response()
                body = app_notifications.list_notifications(
                    response, limit=50, offset=0, unread_only=False, user=user
                )
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["unread"], 0)
        self.assertEqual(body["notifications"][0]["id"], 1)

    def test_register_device_upserts_token(self):
        cur = _Cur(fetchone_values=[(42,)])
        conn = _Conn(cur)
        with patch("customer_api.get_connection", return_value=conn):
            with patch.object(app_notifications, "_ensure_tables"):
                user = _fake_customer("0001SAM")
                body = app_notifications.register_device(
                    app_notifications.DeviceRegistration(token="tok", platform="android"),
                    user=user,
                )
        self.assertEqual(body["id"], 42)

    def test_register_device_requires_token(self):
        user = _fake_customer("0001SAM")
        with self.assertRaises(Exception):
            app_notifications.register_device(
                app_notifications.DeviceRegistration(token="", platform="android"),
                user=user,
            )

    def test_mark_read_all(self):
        cur = _Cur()
        cur.rowcount = 3
        conn = _Conn(cur)
        with patch("customer_api.get_connection", return_value=conn):
            with patch.object(app_notifications, "_ensure_tables"):
                user = _fake_customer("0001SAM")
                body = app_notifications.mark_notifications_read(
                    app_notifications.MarkReadRequest(all=True), user=user
                )
        self.assertEqual(body["updated"], 3)

    def test_mark_read_requires_target(self):
        user = _fake_customer("0001SAM")
        with self.assertRaises(Exception):
            app_notifications.mark_notifications_read(
                app_notifications.MarkReadRequest(), user=user
            )


if __name__ == "__main__":
    unittest.main()
