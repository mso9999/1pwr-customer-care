"""Regression: uGrid discover must not map short codes inside longer site names."""

from sync_ugridplan import _ugp_discover_match_site


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
