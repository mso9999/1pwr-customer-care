"""Publishing-but-not-accepted detection for commissioned 1Meters."""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "check_1meter_ingest",
    Path(__file__).resolve().parents[1] / "scripts" / "ops" / "check_1meter_ingest.py",
)
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)

NOW = datetime(2026, 9, 27, 16, 0, tzinfo=timezone.utc)


def test_flags_publishing_meter_that_cc_never_accepted():
    stuck = check.find_stuck(NOW, [("000023021769", "0001KOT", None)],
                             {"000023021769": NOW - timedelta(minutes=5)})
    assert [s["account"] for s in stuck] == ["0001KOT"]


def test_flags_meter_whose_accepts_stopped():
    stuck = check.find_stuck(NOW, [("23022613", "0003MAK", NOW - timedelta(hours=2))],
                             {"23022613": NOW - timedelta(minutes=2)})
    assert len(stuck) == 1


def test_ignores_healthy_and_silent_meters():
    meters = [
        ("A", "0001AAA", NOW - timedelta(minutes=10)),  # accepted recently
        ("B", "0002AAA", None),                          # gateway silent: offline monitor's job
    ]
    published = {"A": NOW - timedelta(minutes=1), "B": NOW - timedelta(hours=3)}
    assert check.find_stuck(NOW, meters, published) == []
