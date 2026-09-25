"""An in-service 1Meter is inside a PTB; flag the ones uGridPLAN has no PTB for."""

import os

os.environ.setdefault("CC_JWT_SECRET", "unit-test-secret")

import sync_ugridplan as sug


def test_meter_in_a_ptb_channel_is_not_a_gap():
    meters = [
        {"meter_id": "000023021767", "account_number": "0001SIN"},
        {"meter_id": "000023021718", "account_number": "0002SIN"},
        {"meter_id": "000023021750", "account_number": "0003SIN"},
    ]
    ptbs = [
        {"ptbId": "P1", "channelSerials": ["23021767", ""]},
        {"ptb_id": "P2", "channel_serials": '["000023021750"]'},
    ]
    gaps = sug.meters_missing_ptb(meters, ptbs)
    assert [m["meter_id"] for m in gaps] == ["000023021718"]


def test_no_ptbs_means_every_active_meter_is_a_gap():
    meters = [{"meter_id": "000023021718", "account_number": "0002SIN"}]
    assert sug.meters_missing_ptb(meters, []) == meters
