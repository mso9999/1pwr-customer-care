"""Meter detail page: DynamoDB key padding and reading parsing."""

import os
import unittest

os.environ.setdefault("CC_JWT_SECRET", "unit-test-secret")

import meter_lifecycle as ml


class MeterDetailHelpersTests(unittest.TestCase):
    def test_meter_id_is_padded_for_dynamodb(self):
        self.assertEqual(ml._ddb_meter_id("23024464"), "000023024464")
        self.assertEqual(ml._ddb_meter_id("000023024464"), "000023024464")
        self.assertEqual(ml._ddb_meter_id("SM-123"), "SM-123")

    def test_reading_row_strips_units(self):
        item = {
            "sample_time": {"S": "202609251923"},
            "EnergyActive": {"S": "12.3456 kWh"},
            "Power": {"S": "41.5 W"},
            "Voltage": {"S": "229.8 V"},
            "Current": {"S": "180 mA"},
            "Frequency": {"S": "49.98 Hz"},
            "Relay": {"S": "1"},
            "FirmwareVersion": {"S": "1.1.71"},
            "thingName": {"S": "MAK-GW-0184"},
        }
        row = ml._reading_row(item)
        self.assertEqual(row["energy_kwh"], 12.3456)
        self.assertEqual(row["power_w"], 41.5)
        self.assertEqual(row["current_ma"], 180.0)
        self.assertEqual(row["fw_version"], "1.1.71")
        self.assertIsNone(ml._reading_row({})["energy_kwh"])


if __name__ == "__main__":
    unittest.main()
