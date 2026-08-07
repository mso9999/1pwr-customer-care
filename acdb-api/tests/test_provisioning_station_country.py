"""Country-lane safety tests for the downloadable local station."""

import io
from unittest.mock import patch

import pytest

from provisioning_station_dist import provisioning_station as station


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return io.BytesIO(b'{"ok": true}').read()


def test_benin_station_uses_benin_api_lane():
    station.SESSION = station.Session("https://cc.1pwrafrica.com", None, "BN")
    with patch.object(station.urllib.request, "urlopen", return_value=_Response()) as opener:
        assert station.cc_request("GET", "/provisioning/site-codes", auth=False) == {"ok": True}
    request = opener.call_args.args[0]
    assert request.full_url == "https://cc.1pwrafrica.com/api/bn/provisioning/site-codes"


def test_zambia_station_uses_zambia_api_lane():
    station.SESSION = station.Session("https://cc.1pwrafrica.com", None, "ZM")
    with patch.object(station.urllib.request, "urlopen", return_value=_Response()) as opener:
        station.cc_request("GET", "/provisioning/readiness", auth=False)
    request = opener.call_args.args[0]
    assert request.full_url == "https://cc.1pwrafrica.com/api/zm/provisioning/readiness"


def test_station_refuses_requests_until_country_is_selected():
    station.SESSION = station.Session("https://cc.1pwrafrica.com", None)
    with pytest.raises(RuntimeError, match="Select the deployment country"):
        station.cc_request("GET", "/provisioning/site-codes", auth=False)


def test_station_normalizes_flat_and_wrapped_site_lists():
    rows = [{"code": "GBO", "name": "Gbo"}, {"code": "SAM", "name": "Sam"}]

    assert station.normalize_site_codes_response(rows) == rows
    assert station.normalize_site_codes_response({"sites": rows}) == rows
    assert station.normalize_site_codes_response({"data": rows}) == rows


def test_station_rejects_invalid_site_list_shape():
    with pytest.raises(RuntimeError, match="invalid site list"):
        station.normalize_site_codes_response("SAM")
