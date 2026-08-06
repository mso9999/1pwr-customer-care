from types import SimpleNamespace

from om_tickets import _cc_to_om_create, _cc_to_om_update, _om_to_cc


def test_update_preserves_canonical_maintenance_field_names():
    mapped = _cc_to_om_update({
        "precautions": "Use LOTO and PPE",
        "resolution_approach": "Replace the AVR and function-test",
    })

    assert mapped["precautions"] == "Use LOTO and PPE"
    assert mapped["resolution_approach"] == "Replace the AVR and function-test"
    assert "preventive_action" not in mapped
    assert "resolution_notes" not in mapped


def test_create_includes_maintenance_documentation():
    user = SimpleNamespace(name="Operator", user_id="operator-1")
    mapped = _cc_to_om_create({
        "site_code": "SEH",
        "category": "Generator",
        "fault_description": "Generator will not start",
        "troubleshooting_steps": "Inspected regulator",
        "cause_of_fault": "Failed AVR",
        "precautions": "Use LOTO and PPE",
        "resolution_approach": "Replace the AVR and function-test",
    }, user)

    assert mapped["troubleshooting_steps"] == "Inspected regulator"
    assert mapped["cause_of_fault"] == "Failed AVR"
    assert mapped["precautions"] == "Use LOTO and PPE"
    assert mapped["resolution_approach"] == "Replace the AVR and function-test"


def test_read_prefers_canonical_fields_but_supports_legacy_records():
    canonical = _om_to_cc({
        "ticket_id": "SEH-2026-0029",
        "precautions": "Canonical precaution",
        "preventive_action": "Different preventive action",
        "resolution_approach": "Canonical approach",
        "resolution_notes": "Legacy notes",
    })
    legacy = _om_to_cc({
        "ticket_id": "SEH-2026-0028",
        "preventive_action": "Legacy precaution",
        "resolution_notes": "Legacy approach",
    })

    assert canonical["precautions"] == "Canonical precaution"
    assert canonical["resolution_approach"] == "Canonical approach"
    assert legacy["precautions"] == "Legacy precaution"
    assert legacy["resolution_approach"] == "Legacy approach"
