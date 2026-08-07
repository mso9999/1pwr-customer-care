import io
import zipfile

from meter_provisioning import STATION_BUNDLE_VERSION, download_station
from provisioning_station_dist import provisioning_station as station


def test_station_download_is_versioned_uncached_and_contains_new_site_picker():
    response = download_station(_user=None)

    assert STATION_BUNDLE_VERSION == station.STATION_VERSION
    assert response.headers["cache-control"].startswith("no-store")
    assert response.headers["x-provisioning-station-version"] == STATION_BUNDLE_VERSION
    assert STATION_BUNDLE_VERSION in response.headers["content-disposition"]

    with zipfile.ZipFile(io.BytesIO(response.body)) as bundle:
        page = bundle.read("provisioning-station/static/index.html").decode()
        assert "select below or enter canonical CC code" in page
        assert 'id="siteChoices"' in page
        assert 'id="versionPill"' in page
