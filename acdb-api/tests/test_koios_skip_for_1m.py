"""1Meter and SteamaCo accounts must not be pushed to Koios."""

import os
import sys

os.environ.setdefault("CC_JWT_SECRET", "unit-test-secret")

from unittest.mock import MagicMock, patch

# customer_api imports boto3 through the MAK connectivity router. The credit
# tests never call AWS; they only need the import to succeed.
sys.modules.setdefault("boto3", MagicMock())

import pytest


def _conn():
    conn = MagicMock()
    conn.__enter__.return_value = conn
    return conn


@pytest.mark.parametrize("priority", ["1m", "steamaco"])
def test_non_sparkmeter_priority_does_not_call_koios(priority):
    with (
        patch("customer_api.get_connection", return_value=_conn()),
        patch("balance_engine._resolve_billing_priority", return_value=priority),
        patch("sm_credit_retry.credit_sparkmeter") as credit,
        patch("sm_credit_retry.enqueue_sm_credit_retry") as enqueue,
    ):
        from sm_credit_retry import credit_sm_with_retry

        out = credit_sm_with_retry(
            account_number="0001KOT",
            amount=10.0,
            memo="test",
            external_id="145022",
        )

    credit.assert_not_called()
    enqueue.assert_not_called()
    assert out["success"] is True
    assert out["skipped_koios"] is True
    assert out["platform"] == priority
    assert out["queued_retry"] is False


def test_mak_one_meter_still_credits_thundercloud():
    """MAK runs the meters in series, so 1Meter billing still pushes."""
    result = MagicMock(
        success=True, platform="thundercloud", sm_transaction_id="tc", error=None,
    )
    with (
        patch("customer_api.get_connection", return_value=_conn()),
        patch("balance_engine._resolve_billing_priority", return_value="1m"),
        patch("sm_credit_retry._sparkmeter_credit_override", return_value=None),
        patch("sm_credit_retry._account_credit_eligibility", return_value=(True, None)),
        patch("sm_credit_retry.credit_sparkmeter", return_value=result) as credit,
        patch("sm_credit_retry.process_due_sm_credit_retries", return_value={}),
    ):
        from sm_credit_retry import credit_sm_with_retry

        out = credit_sm_with_retry(
            account_number="0045MAK",
            amount=10.0,
            memo="test",
            external_id="1",
            replay_due_limit=0,
        )

    credit.assert_called_once()
    assert out.get("skipped_koios") is None
    assert out["success"] is True


@pytest.mark.parametrize(
    ("account", "mode", "pushes"),
    [
        ("0001KOT", "push", True),
        ("0045MAK", "skip", False),
    ],
)
def test_sparkmeter_credit_toggle_overrides_the_site_rule(account, mode, pushes):
    result = MagicMock(
        success=True, platform="thundercloud", sm_transaction_id="tc", error=None,
    )
    with (
        patch("customer_api.get_connection", return_value=_conn()),
        patch("balance_engine._resolve_billing_priority", return_value="1m"),
        patch("sm_credit_retry._sparkmeter_credit_override", return_value=mode),
        patch("sm_credit_retry._account_credit_eligibility", return_value=(True, None)),
        patch("sm_credit_retry.credit_sparkmeter", return_value=result) as credit,
        patch("sm_credit_retry.enqueue_sm_credit_retry") as enqueue,
        patch("sm_credit_retry.process_due_sm_credit_retries", return_value={}),
    ):
        from sm_credit_retry import credit_sm_with_retry

        out = credit_sm_with_retry(
            account_number=account,
            amount=10.0,
            memo="test",
            external_id="1",
            replay_due_limit=0,
        )

    if pushes:
        credit.assert_called_once()
        enqueue.assert_not_called()
        assert out.get("skipped_koios") is None
    else:
        credit.assert_not_called()
        enqueue.assert_not_called()
        assert out["skipped_koios"] is True


def test_sparkmeter_priority_still_pushes():
    result = MagicMock(
        success=True, platform="koios", sm_transaction_id="abc", error=None,
    )
    with (
        patch("customer_api.get_connection", return_value=_conn()),
        patch("balance_engine._resolve_billing_priority", return_value="sm"),
        patch("sm_credit_retry._account_credit_eligibility", return_value=(True, None)),
        patch("sm_credit_retry.credit_sparkmeter", return_value=result) as credit,
        patch("sm_credit_retry.process_due_sm_credit_retries", return_value={}),
    ):
        from sm_credit_retry import credit_sm_with_retry

        out = credit_sm_with_retry(
            account_number="0001SAM",
            amount=10.0,
            memo="test",
            external_id="1",
            replay_due_limit=0,
        )

    credit.assert_called_once()
    assert out["success"] is True
    assert out.get("skipped_koios") is None


def test_retry_queue_closes_one_meter_rows_without_calling_koios():
    conn = _conn()
    cur = MagicMock()
    conn.cursor.return_value = cur
    cur.fetchall.return_value = [(9, "0001KOT", 10.0, "memo", "145022", 2)]

    with (
        patch("customer_api.get_connection", return_value=conn),
        patch("sm_credit_retry._release_blocked_retries", return_value=0),
        patch("sm_credit_retry._koios_skip_priority", return_value="1m"),
        patch("sm_credit_retry.credit_sparkmeter") as credit,
    ):
        from sm_credit_retry import process_due_sm_credit_retries

        out = process_due_sm_credit_retries(limit=5)

    credit.assert_not_called()
    updates = [
        call for call in cur.execute.call_args_list
        if "skipped_koios" in str(call)
    ]
    assert updates, cur.execute.call_args_list
    assert out["processed"] == 1
    assert out["ok"] == 0
    assert out["failed"] == 0
