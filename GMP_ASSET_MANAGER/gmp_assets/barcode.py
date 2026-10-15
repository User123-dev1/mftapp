"""Barcode decoding/parsing and label generation.

Input side
----------
Scanners deliver barcode content as text, whichever way they are connected:

* USB HID "keyboard wedge" scanners type the content followed by Enter
  (handled by the web UI scan field),
* serial / virtual-COM scanners send a line (see ``serial_bridge.py``),
* the browser camera decoder (BarcodeDetector / ZXing) returns a string.

``parse_barcode`` turns that text into structured data. It understands

* AIM symbology identifiers (``]C1`` GS1-128, ``]d2`` GS1 DataMatrix, ...),
* GS1 element strings with FNC1/GS separators or common substitutes,
* GS1 human-readable form, e.g. ``(01)09501101530003(17)261231(10)LOT42``,
* plain linear codes (Code 128 / Code 39 asset and location tags).

GS1 check digits (GTIN, SSCC, GLN, GRAI) are validated and dates are decoded
using the GS1 century rule, so a mis-scan is rejected instead of silently
resolving to the wrong asset or lot.

Output side
-----------
``code128_svg`` renders Code 128 (optionally GS1-128) labels as SVG with no
third-party dependencies, suitable for printing on label printers.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Dict, Iterable, List, Optional, Tuple
from xml.sax.saxutils import escape

from . import clock

GS = "\x1d"

# AIM symbology identifiers: prefix -> (name, carries GS1 element string)
SYMBOLOGY_IDENTIFIERS: Dict[str, Tuple[str, bool]] = {
    "]C1": ("GS1-128", True),
    "]e0": ("GS1 DataBar", True),
    "]d2": ("GS1 DataMatrix", True),
    "]Q3": ("GS1 QR Code", True),
    "]J1": ("GS1 DotCode", True),
    "]E0": ("EAN-13 / UPC-A", True),
    "]E4": ("EAN-8", True),
    "]C0": ("Code 128", False),
    "]A0": ("Code 39", False),
    "]d1": ("Data Matrix", False),
    "]Q1": ("QR Code", False),
}

# GS1 character set 82 - allowed in alphanumeric AIs.
_CSET82 = re.compile(r"^[!\"%&'()*+,\-./0-9:;<=>?A-Z_a-z]+$")


@dataclass(frozen=True)
class AISpec:
    fixed: Optional[int]  # fixed data length, or None for variable length
    max_len: int
    kind: str  # "n" numeric, "an" alphanumeric, "date" YYMMDD, "datetime" YYMMDDHHMM
    title: str
    check_digit: bool = False


AI_TABLE: Dict[str, AISpec] = {
    "00": AISpec(18, 18, "n", "SSCC", True),
    "01": AISpec(14, 14, "n", "GTIN", True),
    "02": AISpec(14, 14, "n", "CONTENT", True),
    "10": AISpec(None, 20, "an", "BATCH/LOT"),
    "11": AISpec(6, 6, "date", "PROD DATE"),
    "12": AISpec(6, 6, "date", "DUE DATE"),
    "13": AISpec(6, 6, "date", "PACK DATE"),
    "15": AISpec(6, 6, "date", "BEST BEFORE"),
    "16": AISpec(6, 6, "date", "SELL BY"),
    "17": AISpec(6, 6, "date", "USE BY OR EXPIRY"),
    "20": AISpec(2, 2, "n", "VARIANT"),
    "21": AISpec(None, 20, "an", "SERIAL"),
    "22": AISpec(None, 20, "an", "CPV"),
    "235": AISpec(None, 28, "an", "TPX"),
    "240": AISpec(None, 30, "an", "ADDITIONAL ID"),
    "241": AISpec(None, 30, "an", "CUST. PART No."),
    "250": AISpec(None, 30, "an", "SECONDARY SERIAL"),
    "251": AISpec(None, 30, "an", "REF. TO SOURCE"),
    "254": AISpec(None, 20, "an", "GLN EXTENSION COMPONENT"),
    "30": AISpec(None, 8, "n", "VAR. COUNT"),
    "37": AISpec(None, 8, "n", "COUNT"),
    "400": AISpec(None, 30, "an", "ORDER NUMBER"),
    "410": AISpec(13, 13, "n", "SHIP TO LOC", True),
    "411": AISpec(13, 13, "n", "BILL TO", True),
    "412": AISpec(13, 13, "n", "PURCHASE FROM", True),
    "413": AISpec(13, 13, "n", "SHIP FOR LOC", True),
    "414": AISpec(13, 13, "n", "LOC No.", True),
    "415": AISpec(13, 13, "n", "PAY TO", True),
    "416": AISpec(13, 13, "n", "PROD/SERV LOC", True),
    "422": AISpec(3, 3, "n", "ORIGIN"),
    "7003": AISpec(10, 10, "datetime", "EXPIRY TIME"),
    "7004": AISpec(None, 4, "n", "ACTIVE POTENCY"),
    "710": AISpec(None, 20, "an", "NHRN PZN"),
    "711": AISpec(None, 20, "an", "NHRN CIP"),
    "712": AISpec(None, 20, "an", "NHRN NDC"),
    "713": AISpec(None, 20, "an", "NHRN DRN"),
    "714": AISpec(None, 20, "an", "NHRN AIM"),
    "8003": AISpec(None, 30, "an", "GRAI"),
    "8004": AISpec(None, 30, "an", "GIAI"),
}
AI_TABLE.update({str(n): AISpec(None, 30, "an", "INTERNAL") for n in range(90, 100)})

# AIs that may start a GS1 string scanned without a symbology identifier.
_GS1_LEADING_AIS = ("00", "01", "02", "8003", "8004")


@dataclass
class ParsedBarcode:
    raw: str
    data: str
    symbology: Optional[str] = None
    is_gs1: bool = False
    elements: Dict[str, str] = field(default_factory=dict)
    gtin: Optional[str] = None
    batch: Optional[str] = None
    serial: Optional[str] = None
    expiry: Optional[date] = None
    sscc: Optional[str] = None
    giai: Optional[str] = None
    grai: Optional[str] = None
    errors: List[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        d = asdict(self)
        d["raw"] = self.raw.replace(GS, "<GS>")
        d["data"] = self.data.replace(GS, "<GS>")
        d["expiry"] = self.expiry.isoformat() if self.expiry else None
        d["valid"] = self.valid
        d["element_titles"] = {ai: _spec_for(ai).title for ai in self.elements if _spec_for(ai)}
        return d


# --------------------------------------------------------------------------
# Check digits and dates
# --------------------------------------------------------------------------

def gs1_check_digit(body: str) -> int:
    """GS1 mod-10 check digit for a numeric string (without the check digit)."""
    total = 0
    for i, ch in enumerate(reversed(body)):
        total += int(ch) * (3 if i % 2 == 0 else 1)
    return (10 - total % 10) % 10


def is_valid_gs1_number(number: str) -> bool:
    return number.isdigit() and len(number) >= 2 and gs1_check_digit(number[:-1]) == int(number[-1])


def _gs1_year(yy: int, today: date) -> int:
    """GS1 General Specifications sliding-window century determination."""
    century = today.year // 100 * 100
    diff = yy - today.year % 100
    if 51 <= diff <= 99:
        century -= 100
    elif -99 <= diff <= -50:
        century += 100
    return century + yy


def parse_gs1_date(value: str, today: Optional[date] = None) -> date:
    """Decode YYMMDD. DD=00 means the last day of the month (GS1 rule)."""
    today = today or clock.today()
    year = _gs1_year(int(value[0:2]), today)
    month, day = int(value[2:4]), int(value[4:6])
    if day == 0:
        day = calendar.monthrange(year, month)[1]
    return date(year, month, day)


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

def _spec_for(ai: str) -> Optional[AISpec]:
    if ai in AI_TABLE:
        return AI_TABLE[ai]
    # 31nn-36nn: trade measures, 4-digit AI with 6 numeric digits
    if len(ai) == 4 and ai[:2] in {"31", "32", "33", "34", "35", "36"} and ai[2:].isdigit():
        return AISpec(6, 6, "n", f"MEASURE ({ai})")
    return None


def _match_ai(s: str, pos: int) -> Optional[str]:
    for length in (2, 3, 4):
        candidate = s[pos:pos + length]
        if len(candidate) == length and candidate.isdigit() and _spec_for(candidate):
            # 31-36 are 4-digit families; don't stop at their 2-digit prefix
            if length == 2 and candidate in {"31", "32", "33", "34", "35", "36"}:
                continue
            return candidate
    return None


def _validate_element(ai: str, value: str, errors: List[str]) -> None:
    spec = _spec_for(ai)
    if spec.fixed is not None and len(value) != spec.fixed:
        errors.append(f"AI ({ai}) {spec.title}: expected {spec.fixed} characters, got {len(value)}")
        return
    if len(value) == 0 or len(value) > spec.max_len:
        errors.append(f"AI ({ai}) {spec.title}: length {len(value)} outside 1..{spec.max_len}")
        return
    if spec.kind in ("n", "date", "datetime") and not value.isdigit():
        errors.append(f"AI ({ai}) {spec.title}: must be numeric")
        return
    if spec.kind == "an" and not _CSET82.match(value):
        errors.append(f"AI ({ai}) {spec.title}: contains characters outside GS1 set 82")
        return
    if spec.check_digit and not is_valid_gs1_number(value):
        errors.append(f"AI ({ai}) {spec.title}: invalid check digit")
    if ai == "8003" and not (len(value) >= 14 and value[:14].isdigit() and is_valid_gs1_number(value[:14])):
        errors.append("AI (8003) GRAI: invalid asset type / check digit")
    if spec.kind == "date":
        try:
            parse_gs1_date(value)
        except ValueError:
            errors.append(f"AI ({ai}) {spec.title}: invalid date {value}")
    if spec.kind == "datetime":
        try:
            parse_gs1_date(value[:6])
            if int(value[6:8]) > 23 or int(value[8:10]) > 59:
                raise ValueError
        except ValueError:
            errors.append(f"AI ({ai}) {spec.title}: invalid date/time {value}")


def _parse_element_string(s: str, errors: List[str]) -> Dict[str, str]:
    elements: Dict[str, str] = {}
    pos = 0
    while pos < len(s):
        if s[pos] == GS:
            pos += 1
            continue
        ai = _match_ai(s, pos)
        if ai is None:
            errors.append(f"Unknown or invalid application identifier at position {pos}: {s[pos:pos + 4]!r}")
            break
        spec = _spec_for(ai)
        start = pos + len(ai)
        if spec.fixed is not None:
            value = s[start:start + spec.fixed]
            pos = start + spec.fixed
        else:
            end = s.find(GS, start)
            end = len(s) if end == -1 else end
            value = s[start:end]
            pos = end
        if ai in elements:
            errors.append(f"AI ({ai}) appears more than once")
        elements[ai] = value
        _validate_element(ai, value, errors)
    return elements


_BRACKETED = re.compile(r"\((\d{2,4})\)([^()]*)")


def _parse_bracketed(s: str, errors: List[str]) -> Dict[str, str]:
    elements: Dict[str, str] = {}
    consumed = 0
    for m in _BRACKETED.finditer(s):
        if m.start() != consumed:
            errors.append(f"Unexpected text at position {consumed}")
        consumed = m.end()
        ai, value = m.group(1), m.group(2).strip()
        if not _spec_for(ai):
            errors.append(f"Unknown application identifier ({ai})")
            continue
        elements[ai] = value
        _validate_element(ai, value, errors)
    if consumed != len(s):
        errors.append("Trailing text after last application identifier")
    return elements


def parse_barcode(raw: str, separators: Iterable[str] = ("\x1d", "<GS>", "{GS}", "|", "~")) -> ParsedBarcode:
    """Parse scanned barcode text into a :class:`ParsedBarcode`."""
    if raw is None:
        raw = ""
    text = raw.strip("\r\n\t ")
    symbology = None
    gs1_hint = None
    for prefix, (name, is_gs1) in SYMBOLOGY_IDENTIFIERS.items():
        if text.startswith(prefix):
            symbology, gs1_hint = name, is_gs1
            text = text[len(prefix):]
            break

    result = ParsedBarcode(raw=raw, data=text, symbology=symbology)
    if not text:
        result.errors.append("Empty scan")
        return result

    normalized = text
    for sep in separators:
        if sep != GS:
            normalized = normalized.replace(sep, GS)
    normalized = normalized.strip(GS)

    # EAN/UPC carry a bare GTIN-8/12/13
    if symbology in ("EAN-13 / UPC-A", "EAN-8"):
        normalized = "01" + normalized.zfill(14)

    errors: List[str] = []
    elements: Dict[str, str] = {}
    looks_gs1 = False
    if gs1_hint is not False and text.startswith("("):
        elements = _parse_bracketed(text, errors)
        normalized = gs1_element_string(list(elements.items())) if not errors else normalized
        looks_gs1 = True
    elif gs1_hint is True or (gs1_hint is None and GS in normalized):
        elements = _parse_element_string(normalized, errors)
        looks_gs1 = True
    elif gs1_hint is None and normalized.startswith(_GS1_LEADING_AIS):
        # No identifier and no separator: accept as GS1 only if it parses cleanly
        trial_errors: List[str] = []
        trial = _parse_element_string(normalized, trial_errors)
        if trial and not trial_errors:
            elements, looks_gs1 = trial, True

    if looks_gs1 and (elements or errors):
        result.is_gs1 = True
        result.data = normalized
        result.elements = elements
        result.errors = errors
        result.gtin = elements.get("01")
        result.batch = elements.get("10")
        result.serial = elements.get("21")
        result.sscc = elements.get("00")
        result.giai = elements.get("8004")
        result.grai = elements.get("8003")
        if "17" in elements and not any("(17)" in e for e in errors):
            result.expiry = parse_gs1_date(elements["17"])
        elif "7003" in elements and not any("(7003)" in e for e in errors):
            result.expiry = parse_gs1_date(elements["7003"][:6])
        return result

    # Plain (non-GS1) code: asset tag, location tag, etc.
    if any(ord(c) < 32 or ord(c) > 126 for c in text):
        result.errors.append("Barcode contains non-printable characters")
    return result


# --------------------------------------------------------------------------
# Code 128 / GS1-128 generation
# --------------------------------------------------------------------------

# Bar/space module widths for Code 128 symbol values 0..106 (106 = STOP).
CODE128_PATTERNS: Tuple[str, ...] = (
    "212222", "222122", "222221", "121223", "121322", "131222", "122213", "122312", "132212", "221213",
    "221312", "231212", "112232", "122132", "122231", "113222", "123122", "123221", "223211", "221132",
    "221231", "213212", "223112", "312131", "311222", "321122", "321221", "312212", "322112", "322211",
    "212123", "212321", "232121", "111323", "131123", "131321", "112313", "132113", "132311", "211313",
    "231113", "231311", "112133", "112331", "132131", "113123", "113321", "133121", "313121", "211331",
    "231131", "213113", "213311", "213131", "311123", "311321", "331121", "312113", "312311", "332111",
    "314111", "221411", "431111", "111224", "111422", "121124", "121421", "141122", "141221", "112214",
    "112412", "122114", "122411", "142112", "142211", "241211", "221114", "413111", "241112", "134111",
    "111242", "121142", "121241", "114212", "124112", "124211", "411212", "421112", "421211", "212141",
    "214121", "412121", "111143", "111341", "131141", "114113", "114311", "411113", "411311", "113141",
    "114131", "311141", "411131", "211412", "211214", "211232", "2331112",
)
_START_B, _START_C, _CODE_B, _CODE_C, _FNC1, _STOP = 104, 105, 100, 99, 102, 106


def _digit_run(data: str, i: int) -> int:
    n = 0
    while i + n < len(data) and data[i + n].isdigit():
        n += 1
    return n


def code128_values(data: str, gs1: bool = False) -> List[int]:
    """Encode text to Code 128 symbol values (start..checksum..stop).

    Uses code set B, switching to code set C for runs of 4+ digits. With
    ``gs1=True`` a leading FNC1 is emitted and GS characters become FNC1.
    """
    if not data:
        raise ValueError("Nothing to encode")
    for ch in data:
        if ch != GS and not 32 <= ord(ch) <= 126:
            raise ValueError(f"Character {ch!r} cannot be encoded in Code 128 B/C")
    if GS in data and not gs1:
        raise ValueError("GS separators are only valid in GS1-128")

    first_run = _digit_run(data, 0)
    mode = "C" if first_run >= 4 and first_run % 2 == 0 or first_run == len(data) == 2 else "B"
    values = [_START_C if mode == "C" else _START_B]
    if gs1:
        values.append(_FNC1)
    i = 0
    while i < len(data):
        ch = data[i]
        if ch == GS:
            values.append(_FNC1)
            i += 1
            continue
        run = _digit_run(data, i)
        if mode == "C":
            if run >= 2:
                values.append(int(data[i:i + 2]))
                i += 2
                continue
            values.append(_CODE_B)
            mode = "B"
            continue
        if run >= 4 and run % 2 == 0:
            values.append(_CODE_C)
            mode = "C"
            continue
        values.append(ord(ch) - 32)
        i += 1

    checksum = (values[0] + sum(pos * v for pos, v in enumerate(values[1:], start=1))) % 103
    return values + [checksum, _STOP]


def code128_modules(values: List[int]) -> List[int]:
    """Expand symbol values to alternating bar/space widths (+2-module stop bar)."""
    widths: List[int] = []
    for v in values:
        widths.extend(int(w) for w in CODE128_PATTERNS[v])
    return widths


def code128_svg(
    data: str,
    gs1: bool = False,
    module_width: float = 2.0,
    height: float = 60.0,
    human_readable: Optional[str] = None,
    extra_lines: Iterable[str] = (),
) -> str:
    """Render a Code 128 / GS1-128 barcode label as an SVG document."""
    widths = code128_modules(code128_values(data, gs1=gs1))
    quiet = 10 * module_width
    total = sum(widths) * module_width + 2 * quiet
    text = human_readable if human_readable is not None else data.replace(GS, "")
    lines = [text, *extra_lines]
    text_h = 16 * len(lines) + 4
    svg_h = height + text_h + 8
    rects = []
    x = quiet
    for idx, w in enumerate(widths):
        if idx % 2 == 0:  # even index = bar
            rects.append(f'<rect x="{x:.2f}" y="4" width="{w * module_width:.2f}" height="{height:.2f}"/>')
        x += w * module_width
    texts = [
        f'<text x="{total / 2:.2f}" y="{height + 20 + 16 * n:.2f}" text-anchor="middle" '
        f'font-family="monospace" font-size="{14 if n == 0 else 11}">{escape(line)}</text>'
        for n, line in enumerate(lines)
    ]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total:.0f}" height="{svg_h:.0f}" '
        f'viewBox="0 0 {total:.2f} {svg_h:.2f}">'
        f'<rect width="100%" height="100%" fill="#fff"/><g fill="#000">{"".join(rects)}</g>'
        f'{"".join(texts)}</svg>'
    )


def gs1_element_string(elements: List[Tuple[str, str]]) -> str:
    """Build a GS1 element string, inserting GS after variable-length AIs."""
    parts = []
    for idx, (ai, value) in enumerate(elements):
        spec = _spec_for(ai)
        if spec is None:
            raise ValueError(f"Unknown AI {ai}")
        parts.append(ai + value)
        if spec.fixed is None and idx < len(elements) - 1:
            parts.append(GS)
    return "".join(parts)


def gs1_human_readable(element_string: str) -> str:
    errors: List[str] = []
    elements = _parse_element_string(element_string, errors)
    return "".join(f"({ai}){value}" for ai, value in elements.items())
