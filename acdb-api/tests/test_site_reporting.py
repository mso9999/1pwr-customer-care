"""1Meter silence is a meter fault only when SparkMeter or the site PCS is live."""

from datetime import datetime, timedelta, timezone

from site_reporting import build_report, classify_site

NOW = datetime(2026, 9, 29, 8, 0, tzinfo=timezone.utc)


def _row(**kwargs):
    base = dict(
        site="MAK",
        onemeter_known=12,
        onemeter_live=0,
        onemeter_last_seen=NOW - timedelta(hours=10),
        has_spark=True,
        spark_last_seen=NOW - timedelta(hours=5),
        has_pcs=True,
        pcs_last_seen=NOW - timedelta(hours=5),
        now=NOW,
    )
    base.update(kwargs)
    return classify_site(**base)


def test_pcs_live_while_meters_silent_is_a_meter_fault():
    row = _row(pcs_last_seen=NOW - timedelta(minutes=2))
    assert row["condition"] == "meter_fault"
    assert row["pcs_live"] is True
    assert row["spark_live"] is False


def test_sparkmeter_live_while_meters_silent_is_a_meter_fault():
    row = _row(spark_last_seen=NOW - timedelta(minutes=20), has_pcs=False, pcs_last_seen=None)
    assert row["condition"] == "meter_fault"
    assert row["spark_live"] is True
    assert row["pcs_live"] is False


def test_either_live_witness_is_enough():
    row = _row(
        spark_last_seen=NOW - timedelta(minutes=10),
        pcs_last_seen=NOW - timedelta(hours=3),
    )
    assert row["condition"] == "meter_fault"
    assert row["spark_live"] is True
    assert row["pcs_live"] is False


def test_everything_silent_is_a_site_outage():
    row = _row()
    assert row["condition"] == "site_outage"
    assert row["spark_live"] is False
    assert row["pcs_live"] is False


def test_a_live_1meter_is_not_flagged():
    row = _row(onemeter_live=1, pcs_last_seen=NOW - timedelta(minutes=1))
    assert row["condition"] == "ok"


def test_late_but_not_down_is_not_either_flag():
    # SparkMeter an hour ago is past the 45-minute live window and inside the
    # 2-hour outage window. PCS is absent, so nothing confirms the site.
    row = _row(
        spark_last_seen=NOW - timedelta(minutes=70),
        has_pcs=False,
        pcs_last_seen=None,
    )
    assert row["condition"] == "uncertain"


def test_no_comparison_source_is_not_called_an_outage():
    row = _row(has_spark=False, spark_last_seen=None, has_pcs=False, pcs_last_seen=None)
    assert row["condition"] == "uncertain"


def test_site_without_1meters_is_ignored():
    row = _row(onemeter_known=0, onemeter_live=0, pcs_last_seen=NOW - timedelta(minutes=1))
    assert row["condition"] == "none"


def test_report_keeps_only_the_two_flags_and_puts_meter_faults_first():
    rows = build_report(
        onemeters=[
            ("MAK", 10, 0, NOW - timedelta(hours=3)),
            ("KOT", 4, 0, NOW - timedelta(hours=3)),
            ("SIN", 6, 2, NOW - timedelta(minutes=5)),
            ("LAB", 3, 0, NOW - timedelta(minutes=40)),
        ],
        spark_readings=[
            ("MAK", NOW - timedelta(hours=3)),
            ("LAB", NOW - timedelta(minutes=70)),
        ],
        spark_sites=["MAK", "KOT", "LAB"],
        pcs_readings=[("KOT", NOW - timedelta(minutes=1))],
        pcs_sites=["MAK", "KOT"],
        now=NOW,
    )
    assert [r["site"] for r in rows] == ["KOT", "MAK"]
    assert rows[0]["condition"] == "meter_fault"
    assert rows[0]["pcs_live"] is True
    assert rows[1]["condition"] == "site_outage"
    # LAB's SparkMeter is an hour late: not live, not silent long enough.
    assert all(r["site"] != "LAB" for r in rows)
    assert all(r["site"] != "SIN" for r in rows)
