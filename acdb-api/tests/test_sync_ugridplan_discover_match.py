"""Regression: uGrid discover must not map short codes inside longer site names."""

from sync_ugridplan import _ugp_discover_match_site, canonical_site_code, resolve_site_project


def _name_map():
    return {
        "tosing": "TOS",
        "tos": "TOS",
        "ha makebe": "MAK",
        "matsoaing": "MAT",
    }


def _abbrev():
    return {"TOS": "Tosing", "MAK": "Ha Makebe", "MAT": "Matsoaing"}


def test_sin_not_inside_tosing():
    """uGrid portfolio code ``sin`` must not match Lesotho ``Tosing`` (TOS)."""
    nm = _name_map()
    ab = _abbrev()
    assert _ugp_discover_match_site("sin", nm, ab) is None


def test_tosing_matches_tos():
    assert _ugp_discover_match_site("tosing", _name_map(), _abbrev()) == "TOS"


def test_tos_code_matches():
    assert _ugp_discover_match_site("tos", _name_map(), _abbrev()) == "TOS"


def test_matsoaing_matches_mat():
    assert _ugp_discover_match_site("matsoaing", _name_map(), _abbrev()) == "MAT"


def test_sinlita_name_resolves_to_sin():
    abbrev = {"SIN": "SINLITA", "SAM": "Sam", "TOS": "Tosing"}
    with __import__("unittest.mock").patch(
        "sync_ugridplan._site_name_index",
        return_value={k.upper(): k.upper() for k in abbrev}
        | {v.upper(): k.upper() for k, v in abbrev.items()},
    ):
        assert canonical_site_code("SINLITA") == "SIN"
        assert canonical_site_code("Sinlita") == "SIN"
        assert canonical_site_code("SIN") == "SIN"
        assert canonical_site_code("SAM") == "SAM"


def test_resolve_site_project_falls_back_to_code():
    import tempfile
    from pathlib import Path
    from unittest.mock import patch

    import db_auth

    with tempfile.TemporaryDirectory() as tmp:
        db_auth.AUTH_DB_PATH = str(Path(tmp) / "cc_auth.db")
        db_auth.init_auth_db()
        abbrev = {"SIN": "SINLITA", "SAM": "Sam"}
        index = {k.upper(): k.upper() for k in abbrev}
        index.update({v.upper(): k.upper() for k, v in abbrev.items()})
        with patch("sync_ugridplan._site_name_index", return_value=index):
            assert resolve_site_project("SINLITA") == ("SIN", "SIN")
            assert resolve_site_project("SAM") == ("SAM", "SAM")
