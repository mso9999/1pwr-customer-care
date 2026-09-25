"""Firmware history in the fleet-map gateway popup: runs and how each arrived."""

import os
import unittest

os.environ.setdefault("CC_JWT_SECRET", "unit-test-secret")

import meter_provisioning as mp


def job(thing, ota_id, sample_time, version, delta_s=0):
    return {
        "thing_name": thing,
        "ota_update_id": ota_id,
        "completed_epoch": mp._sample_epoch(sample_time) + delta_s,
        "file_version": version,
    }


class FirmwareHistoryTests(unittest.TestCase):
    def test_sample_time_is_utc_plus_two(self):
        self.assertEqual(mp._epoch_iso(mp._sample_epoch("202609251816")), "2026-09-25T16:16:00Z")
        self.assertIsNone(mp._sample_epoch("garbage"))

    def test_runs_and_methods(self):
        readings = [
            ("202607291105", "1.1.56", "MAK-GW-0047"),
            ("202609221601", "1.1.56", "MAK-GW-0047"),
            ("202609221616", "1.1.70", "MAK-GW-0183"),
            ("202609251750", "1.1.70", "MAK-GW-0183"),
            ("202609251816", "1.1.71", "MAK-GW-0183"),
            ("202609251832", "1.1.71", "MAK-GW-0183"),
        ]
        spans = mp._firmware_spans(readings)
        self.assertEqual([(s["fw_version"], s["readings"]) for s in spans],
                         [("1.1.56", 2), ("1.1.70", 2), ("1.1.71", 2)])
        jobs = [job("MAK-GW-0183", "1m1171-MAK-GW-0183", "202609251816", "1.1.71", delta_s=6)]
        out = mp._classify_firmware_spans(spans, jobs)
        self.assertEqual([h["method"] for h in out], ["initial", "serial", "ota"])
        self.assertTrue(out[1]["gateway_changed"])
        self.assertEqual(out[2]["ota_update_id"], "1m1171-MAK-GW-0183")

    def test_same_gateway_version_change_without_job_is_serial(self):
        spans = mp._firmware_spans([
            ("202609010800", "1.1.68", "KOT-GW-0004"),
            ("202609251827", "1.1.71", "KOT-GW-0004"),
        ])
        stale = [job("KOT-GW-0004", "old", "202608010800", "1.1.71")]
        out = mp._classify_firmware_spans(spans, stale)
        self.assertEqual(out[1]["method"], "serial")
        self.assertFalse(out[1]["gateway_changed"])

    def test_legacy_file_version_one_still_counts_as_ota(self):
        spans = mp._firmware_spans([
            ("202605010800", "1.1.53", "MAK-GW-0010"),
            ("202605020800", "1.1.54", "MAK-GW-0010"),
        ])
        out = mp._classify_firmware_spans(spans, [job("MAK-GW-0010", "legacy", "202605020750", "1")])
        self.assertEqual(out[1]["method"], "ota")

    def test_ota_for_other_version_does_not_match(self):
        spans = mp._firmware_spans([
            ("202609010800", "1.1.61", "KOT-GW-0004"),
            ("202609020800", "1.1.70", "KOT-GW-0004"),
        ])
        out = mp._classify_firmware_spans(spans, [job("KOT-GW-0004", "x", "202609020750", "1.1.61")])
        self.assertEqual(out[1]["method"], "serial")


if __name__ == "__main__":
    unittest.main()
