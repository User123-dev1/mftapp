from datetime import date

import pytest

from gmp_assets import barcode, clock
from gmp_assets.barcode import GS, code128_values, gs1_check_digit, parse_barcode


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.setattr(clock, "now", lambda: datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc))


def test_check_digit_gs1_examples():
    assert gs1_check_digit("0950110153000") == 3          # GTIN 09501101530003
    assert gs1_check_digit("10614141234567890") == 8     # SSCC 106141412345678908
    assert gs1_check_digit("03600029145") == 2           # UPC-A 036000291452
    assert gs1_check_digit("629104150021") == 3          # EAN-13 6291041500213
    assert barcode.is_valid_gs1_number("09501101530003")
    assert not barcode.is_valid_gs1_number("09501101530004")


def test_gs1_datamatrix_with_symbology_identifier_and_gs():
    raw = f"]d201095011015300031728123110LOT-A42{GS}21SN0001"
    p = parse_barcode(raw)
    assert p.valid, p.errors
    assert p.is_gs1 and p.symbology == "GS1 DataMatrix"
    assert p.gtin == "09501101530003"
    assert p.expiry == date(2028, 12, 31)
    assert p.batch == "LOT-A42" and p.serial == "SN0001"


def test_gs1_variable_fields_split_on_gs():
    p = parse_barcode(f"]C10109501101530003{GS}10LOT42{GS}21SN0001")
    assert p.valid, p.errors
    assert p.batch == "LOT42" and p.serial == "SN0001"


def test_keyboard_wedge_substitute_separator():
    p = parse_barcode("0109501101530003|10LOT42|21SN0001\r\n")
    assert p.valid and p.is_gs1
    assert (p.gtin, p.batch, p.serial) == ("09501101530003", "LOT42", "SN0001")


def test_bracketed_human_readable_and_day_zero():
    p = parse_barcode("(01)09501101530003(17)270200(10)ABC")
    assert p.valid, p.errors
    assert p.expiry == date(2027, 2, 28)  # DD=00 -> last day of month
    assert p.batch == "ABC"


def test_gs1_without_identifier_needs_clean_parse():
    assert parse_barcode("0109501101530003").is_gs1
    # bad check digit, no identifier -> treated as plain code, not mis-resolved
    assert not parse_barcode("0109501101530004").is_gs1


def test_invalid_check_digit_reported_when_gs1_declared():
    p = parse_barcode("]d20109501101530004")
    assert p.is_gs1 and not p.valid
    assert any("check digit" in e for e in p.errors)


def test_giai_asset_identifier():
    p = parse_barcode("]C18004PUMP-0001")
    assert p.valid and p.giai == "PUMP-0001"


def test_ean13_symbology_becomes_gtin():
    p = parse_barcode("]E09501101530003")
    assert p.valid and p.gtin == "09501101530003"


def test_plain_code128_asset_tag():
    p = parse_barcode("]C0AST-000123")
    assert not p.is_gs1 and p.valid and p.data == "AST-000123"
    p = parse_barcode("BAL-0001\n")
    assert not p.is_gs1 and p.data == "BAL-0001"


def test_invalid_date():
    p = parse_barcode("]C10109501101530003171399991")
    assert not p.valid


def test_century_window():
    assert barcode.parse_gs1_date("991231").year == 2099 - 100   # 1999: >50 years ahead -> previous century
    assert barcode.parse_gs1_date("701231").year == 2070


# ---- Code 128 -------------------------------------------------------------

def test_code128_patterns_well_formed():
    pats = barcode.CODE128_PATTERNS
    assert len(pats) == 107 and len(set(pats)) == 107
    assert all(sum(map(int, p)) == 11 for p in pats[:106])
    assert sum(map(int, pats[106])) == 13


def _decode(values):
    """Minimal Code 128 B/C decoder to round-trip our encoder."""
    start, *body, check, stop = values
    assert stop == 106
    assert (start + sum(i * v for i, v in enumerate(body, 1))) % 103 == check
    mode = {104: "B", 105: "C"}[start]
    out = ""
    for v in body:
        if v == 102:
            out += GS
        elif mode == "C" and v == 100:
            mode = "B"
        elif mode == "B" and v == 99:
            mode = "C"
        elif mode == "C":
            out += f"{v:02d}"
        else:
            out += chr(v + 32)
    return out


@pytest.mark.parametrize("text", ["AST-000123", "12345678", "A1234567B", "123456789", "LOC.QC-LAB_2", "99"])
def test_code128_round_trip(text):
    assert _decode(code128_values(text)) == text


def test_gs1_128_leading_fnc1():
    values = code128_values(f"0109501101530003{GS}10LOT1", gs1=True)
    assert values[1] == 102
    assert _decode(values) == GS + f"0109501101530003{GS}10LOT1"


def test_code128_rejects_non_ascii():
    with pytest.raises(ValueError):
        code128_values("ÄB")


def test_svg_contains_text():
    svg = barcode.code128_svg("AST-1", extra_lines=["Balance <XS205>"])
    assert svg.startswith("<svg") and "AST-1" in svg and "&lt;XS205&gt;" in svg
