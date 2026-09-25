"""uGridPLAN project resolution must not require a ``cc_site_projects`` row.

BN KOT was created in the CC Site Registry, which never writes that table, so
Field install 404'd with "No uGridPLAN project configured" although uGridPLAN
loads the KOT project by its code.
"""

import os
import sqlite3
import unittest
from contextlib import contextmanager
from unittest.mock import patch

os.environ.setdefault("CC_JWT_SECRET", "unit-test-secret")

import sync_ugridplan as sug


def auth_db(rows):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE cc_site_projects (site_code TEXT PRIMARY KEY, project_id TEXT)")
    db.executemany("INSERT INTO cc_site_projects VALUES (?, ?)", rows)

    @contextmanager
    def get_auth_db():
        yield db

    return get_auth_db


class SiteProjectKeyTests(unittest.TestCase):
    def key(self, site, rows, names=None):
        names = names or {}
        with patch.object(sug, "get_auth_db", auth_db(rows)), \
                patch.object(sug, "canonical_site_code", lambda s: names.get(s.upper(), s.upper())):
            return sug._site_project_key(site)

    def test_unmapped_site_falls_back_to_its_code(self):
        self.assertEqual(self.key("KOT", [("SIN", "SIN")]), "KOT")

    def test_mapped_site_uses_registry_key(self):
        self.assertEqual(self.key("MAK", [("MAK", "MAK_minigrid")]), "MAK_minigrid")

    def test_display_name_resolves_to_site_code_mapping(self):
        self.assertEqual(self.key("Sinlita", [("SIN", "SIN")], {"SINLITA": "SIN"}), "SIN")


if __name__ == "__main__":
    unittest.main()
