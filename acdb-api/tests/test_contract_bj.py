"""Benin commissioning writes one French Mionwa contract. Lesotho stays bilingual."""
import os
from datetime import date

os.environ.setdefault("CC_JWT_SECRET", "unit-test-secret")

import country_config
import contract_gen
from pypdf import PdfReader


_JPEG = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a"
    "HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIy"
    "MjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIA"
    "AhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAFBABAAAAAAAAAAAAAAAA"
    "AAAAf/xAAUAQEAAAAAAAAAAAAAAAAAAAAA/8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAwDAQACEQ"
    "MRAD8AoAB//9k="
)


def _pdf_text(path: str) -> str:
    reader = PdfReader(path)
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def test_benin_contract_is_french_mionwa(monkeypatch, tmp_path):
    monkeypatch.setattr(country_config, "COUNTRY", country_config.BENIN)
    monkeypatch.setattr(contract_gen, "CONTRACTS_DIR", str(tmp_path))
    monkeypatch.setattr(contract_gen, "_benin_tariff", lambda concession, customer_id: 160)

    result = contract_gen.generate_contract(
        first_name="Amina",
        last_name="Dossou",
        national_id="123456789",
        phone_number="0197000000",
        concession="GBO",
        customer_type="HH1",
        service_phase="Single",
        ampacity="Standard",
        account_number="0001GBO",
        customer_signature_b64=_JPEG,
        connection_date="2026-09-29",
    )

    assert result["fr_filename"].endswith("_Contract_fr.pdf")
    assert "en_filename" not in result
    text = _pdf_text(result["fr_path"])
    assert "Mionwa Generation" in text or "MIONWA GENERATION" in text
    assert "Amina" in text and "Dossou" in text
    today = date.today()
    assert f"{today.day:02d}/{today.month:02d}/{today.year}" in text
    assert "160" in text
    assert "5 000" in text
    folded = text.casefold()
    assert "inclusive grids" not in folded
    assert "sesotho" not in folded
    assert "lsl" not in folded


def test_lesotho_still_renders_both_templates(monkeypatch, tmp_path):
    monkeypatch.setattr(country_config, "COUNTRY", country_config.LESOTHO)
    monkeypatch.setattr(contract_gen, "CONTRACTS_DIR", str(tmp_path))
    written = []

    def capture(html_source, output_path):
        written.append(html_source)
        with open(output_path, "w", encoding="utf-8") as handle:
            handle.write(html_source)
        return True

    monkeypatch.setattr(contract_gen, "_html_to_pdf", capture)

    import tariff

    monkeypatch.setattr(
        tariff,
        "resolve_rate",
        lambda **kwargs: {"rate_lsl": 5.0, "source": "global"},
    )

    result = contract_gen.generate_contract(
        first_name="Lineo",
        last_name="Mokoena",
        national_id="111",
        phone_number="57123456",
        concession="MAK",
        customer_type="HH1",
        service_phase="Single",
        ampacity="Standard",
        account_number="0002MAK",
        customer_signature_b64=_JPEG,
    )

    assert result["en_filename"].endswith("_Contract_en.pdf")
    assert result["so_filename"].endswith("_Contract_so.pdf")
    assert len(written) == 2
    assert "Mini-Grid Provider" in written[0]
    assert "FOROMO" in written[1]
    assert "Mionwa" not in written[0] and "Mionwa" not in written[1]


def test_french_sms_uses_only_the_french_link(monkeypatch):
    monkeypatch.setattr(country_config, "COUNTRY", country_config.BENIN)
    sent = {}

    def fake_send(phone, message, **kwargs):
        sent["phone"] = phone
        sent["message"] = message
        sent["kwargs"] = kwargs
        return True

    import sms_outbound
    monkeypatch.setattr(sms_outbound, "send_gateway_sms", fake_send)
    monkeypatch.setattr(contract_gen, "shorten_url", lambda url: url)

    ok = contract_gen.send_contract_sms(
        first_name="Amina",
        last_name="Dossou",
        phone_number="0197000000",
        en_url="https://cc.example/en.pdf",
        so_url="https://cc.example/so.pdf",
        fr_url="https://cc.example/fr.pdf",
        account_number="0001GBO",
    )

    assert ok is True
    message = sent["message"]
    assert "https://cc.example/fr.pdf" in message
    assert "https://cc.example/en.pdf" not in message
    assert "https://cc.example/so.pdf" not in message
    assert "Bonjour Amina Dossou" in message
    assert "Mionwa Generation" in message
    assert "Lumela" not in message


def test_list_marks_french_contracts(monkeypatch, tmp_path):
    site = tmp_path / "GBO"
    site.mkdir()
    (site / "0001GBO_Dossou_Amina_Contract_fr.pdf").write_bytes(b"%PDF")
    monkeypatch.setattr(contract_gen, "CONTRACTS_DIR", str(tmp_path))
    rows = contract_gen.list_customer_contracts("0001GBO")
    assert rows[0]["lang"] == "fr"
