"""Steamaco importer: serial normalisation, auth, pagination, read mapping."""
import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("CC_JWT_SECRET", "unit-test-secret")

import import_steamaco as imp


def test_norm_serial_handles_leading_zero_variants():
    assert imp._norm_serial("0179221230057") == "179221230057"
    assert imp._norm_serial("179221230057") == "179221230057"
    assert imp._norm_serial(" 179221230040 ") == "179221230040"
    assert imp._norm_serial("") == ""
    assert imp._norm_serial(None) == ""


def _mock_response(payload, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload
    r.raise_for_status.return_value = None
    return r


def test_authenticate_with_username_password():
    with patch.object(imp, "API_BASE", "https://api.steama.co"):
        client = imp.SteamacoClient()
        client._http = MagicMock()
        client._http.post.return_value = _mock_response({"token": "abc123"})
        with patch.dict(os.environ, {"STEAMACO_USERNAME": "u", "STEAMACO_PASSWORD": "p"}, clear=False):
            os.environ.pop("STEAMACO_TOKEN", None)
            client.token = ""
            client.authenticate()
        assert client.token == "abc123"
        args, kwargs = client._http.post.call_args
        assert args[0].endswith("/api-token-auth/")
        assert kwargs["json"] == {"username": "u", "password": "p"}


def test_authenticate_prefers_existing_token():
    client = imp.SteamacoClient()
    client._http = MagicMock()
    with patch.dict(os.environ, {"STEAMACO_TOKEN": "preset"}, clear=False):
        client.token = "preset"
        client.authenticate()
    client._http.post.assert_not_called()
    assert client.token == "preset"


def test_paged_follows_next_urls():
    client = imp.SteamacoClient()
    client.token = "t"
    page1 = {"results": [{"reference": "0179221230057", "id": 55}],
             "next": "https://api.steama.co/meters/?page=2&page_size=100"}
    page2 = {"results": [{"reference": "179221230040", "id": 56}], "next": None}
    client._http = MagicMock()
    client._http.get.side_effect = [_mock_response(page1), _mock_response(page2)]

    with patch.object(imp, "API_BASE", "https://api.steama.co"):
        result = client.list_meters()

    assert result == {"179221230057": 55, "179221230040": 56}
    # Second call follows the absolute 'next' URL with no extra params
    second_call = client._http.get.call_args_list[1]
    assert second_call[0][0] == "https://api.steama.co/meters/?page=2&page_size=100"
    assert second_call[1]["params"] is None


def test_readings_uses_utility_and_time_window():
    client = imp.SteamacoClient()
    client.token = "t"
    client._http = MagicMock()
    client._http.get.return_value = _mock_response({"results": [], "next": None})

    from datetime import datetime, timezone
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 8, 2, tzinfo=timezone.utc)
    with patch.object(imp, "API_BASE", "https://api.steama.co"):
        client.readings(55, start, end)

    call = client._http.get.call_args
    assert "/meters/55/utilities/1/readings/" in call[0][0]
    assert call[1]["params"]["start_time"] == start.isoformat()
    assert call[1]["params"]["end_time"] == end.isoformat()
    assert call[1]["headers"]["Authorization"] == "Token t"


def test_balance_engine_accepts_steamaco_priority():
    import balance_engine

    assert "steamaco" in balance_engine.VALID_PRIORITIES
    assert balance_engine.STEAMACO_SOURCES == ("steamaco",)

    # Priority resolution accepts a per-account steamaco override
    cur = MagicMock()
    cur.fetchone.return_value = ("steamaco",)
    assert balance_engine._resolve_billing_priority(cur, "0001KET") == "steamaco"


def test_consumption_query_handles_steamaco_branch():
    import balance_engine

    cur = MagicMock()
    cur.fetchone.return_value = (12.5,)
    total = balance_engine._consumption_kwh(cur, "0001KET", "steamaco")
    assert total == 12.5
    sql = cur.execute.call_args[0][0]
    assert "steamaco_kwh" in sql
    params = cur.execute.call_args[0][1]
    assert ["steamaco"] in list(params)
