"""Static attachment analysis (no execution).

Dynamic detonation - actually opening the file in an isolated VM and watching
what it does - is delegated to CAPE Sandbox (see sandbox.py). Static analysis
here catches most commodity threats instantly and decides what is worth
detonating.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from .emailparse import Attachment, html_facts

EXECUTABLE_EXT = {
    "exe", "scr", "com", "pif", "bat", "cmd", "vbs", "vbe", "js", "jse", "wsf", "wsh", "ps1", "psm1", "msi", "msp",
    "hta", "cpl", "jar", "lnk", "reg", "dll", "sys", "application", "appref-ms", "gadget", "msc", "url", "chm",
    "sct", "inf", "xll", "wll", "iqy", "slk", "library-ms", "settingcontent-ms", "appx", "msix", "msixbundle",
}
CONTAINER_EXT = {"iso", "img", "vhd", "vhdx", "udf"}
ARCHIVE_EXT = {"zip", "rar", "7z", "gz", "tgz", "tar", "cab", "arj", "ace", "xz", "bz2", "zipx"}
MACRO_EXT = {"docm", "xlsm", "pptm", "dotm", "xltm", "xlam", "ppam", "potm", "xlsb"}
DOC_EXT = {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "rtf", "odt", "txt", "jpg", "png", "csv"}
RTLO = "‮"


@dataclass
class AttachmentFinding:
    filename: str
    sha256: str
    size: int
    kind: str
    score: int = 0
    reasons: List[str] = field(default_factory=list)
    detonate: bool = False      # worth sending to the sandbox

    def add(self, points: int, reason: str, detonate: bool = False):
        self.score += points
        self.reasons.append(reason)
        self.detonate = self.detonate or detonate


def sniff(data: bytes) -> str:
    head = data[:8]
    if head.startswith(b"MZ"):
        return "pe"
    if head.startswith(b"\x7fELF"):
        return "elf"
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        return "zip"
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "ole"
    if head.startswith(b"{\\rt"):
        return "rtf"
    if head.startswith(b"Rar!"):
        return "rar"
    if head.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "7z"
    if head.startswith(b"\x1f\x8b"):
        return "gzip"
    if head.startswith(b"MSCF"):
        return "cab"
    if head.startswith(b"L\x00\x00\x00\x01\x14\x02\x00"):
        return "lnk"
    if len(data) > 0x8006 and data[0x8001:0x8006] == b"CD001":
        return "iso"
    if head.startswith(b"conectix") or data[-512:-504] == b"conectix":
        return "vhd"
    lowered = data[:2048].lstrip().lower()
    if lowered.startswith((b"<!doctype html", b"<html", b"<head", b"<body", b"<script", b"<svg", b"<form")) \
            or b"<html" in lowered[:1024]:
        return "html"
    return "data"


def _ext(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def analyze_attachment(att: Attachment, blocked: Callable[[str, str], Optional[str]], depth: int = 0) -> AttachmentFinding:
    data = att.data
    name = att.filename or "unnamed"
    kind = sniff(data)
    f = AttachmentFinding(name, att.sha256, len(data), kind)
    note = blocked("hash", att.sha256)
    if note is not None:
        f.add(90, f"{name}: matches known-malicious file hash" + (f" ({note})" if note else ""))
    ext = _ext(name)
    if RTLO in name:
        f.add(50, f"{name}: filename uses a right-to-left override character to disguise its extension", True)
    parts = name.lower().split(".")
    if len(parts) >= 3 and parts[-2] in DOC_EXT and parts[-1] in EXECUTABLE_EXT | CONTAINER_EXT:
        f.add(40, f"{name}: double extension disguises an executable", True)
    if ext in EXECUTABLE_EXT or kind in ("pe", "elf", "lnk"):
        f.add(55, f"{name}: executable/script file ({ext or kind})", True)
    if kind in ("pe", "elf") and ext not in EXECUTABLE_EXT:
        f.add(25, f"{name}: real type is an executable but the name says .{ext or '?'}")
    if ext in CONTAINER_EXT or kind in ("iso", "vhd"):
        f.add(40, f"{name}: disk image (used to bypass Mark-of-the-Web protections)", True)

    if kind == "zip" and ext not in ("docx", "xlsx", "pptx", "docm", "xlsm", "pptm", "dotm", "xltm", "potm",
                                     "xlam", "ppam", "odt", "ods", "jar", "apk", "xps", "vsdx"):
        _analyze_zip(f, data, blocked, depth)
    elif kind == "zip":
        _analyze_ooxml(f, data)
    elif kind == "ole":
        _analyze_ole(f, data, ext)
    elif kind == "rtf":
        _analyze_rtf(f, data)
    elif kind == "pdf":
        _analyze_pdf(f, data)
    elif kind == "html" or ext in ("html", "htm", "shtml", "svg", "xhtml", "mht", "mhtml"):
        _analyze_html(f, data)
    elif kind in ("rar", "7z", "cab", "gzip") or ext in ARCHIVE_EXT:
        f.add(10, f"{name}: compressed archive ({kind}) cannot be fully inspected", True)
    if ext in MACRO_EXT:
        f.add(25, f"{name}: macro-enabled Office format", True)
    return f


def _analyze_zip(f: AttachmentFinding, data: bytes, blocked, depth: int):
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        f.add(10, f"{f.filename}: corrupt ZIP")
        return
    infos = zf.infolist()
    if any(i.flag_bits & 0x1 for i in infos):
        f.add(35, f"{f.filename}: password-protected archive (hides its content from scanners)", True)
    for info in infos[:200]:
        inner_ext = _ext(info.filename)
        if inner_ext in EXECUTABLE_EXT or inner_ext in CONTAINER_EXT:
            f.add(45, f"{f.filename}: archive contains {info.filename}", True)
        elif inner_ext in MACRO_EXT:
            f.add(25, f"{f.filename}: archive contains macro document {info.filename}", True)
        if depth < 2 and not (info.flag_bits & 0x1) and info.file_size < 20_000_000:
            try:
                inner = Attachment(info.filename, "application/octet-stream", zf.read(info))
            except Exception:
                continue
            sub = analyze_attachment(inner, blocked, depth + 1)
            if sub.score:
                f.score += sub.score // 2
                f.reasons += [f"{f.filename} > {r}" for r in sub.reasons]
                f.detonate = f.detonate or sub.detonate


def _analyze_ooxml(f: AttachmentFinding, data: bytes):
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        names = zf.namelist()
    except zipfile.BadZipFile:
        f.add(15, f"{f.filename}: damaged Office document")
        return
    if any(n.lower().endswith("vbaproject.bin") for n in names):
        f.add(40, f"{f.filename}: contains VBA macros", True)
    if any("activex" in n.lower() for n in names):
        f.add(15, f"{f.filename}: contains ActiveX controls", True)
    if any(n.lower().startswith(("word/embeddings", "xl/embeddings", "ppt/embeddings")) for n in names):
        f.add(10, f"{f.filename}: contains embedded objects", True)
    for n in names:
        if n.lower().endswith(".rels"):
            rel = zf.read(n)[:200_000]
            if re.search(rb'(?i)TargetMode="External"', rel) and re.search(rb"(?i)attachedTemplate|oleObject|frame", rel):
                f.add(40, f"{f.filename}: loads a remote template/object when opened", True)
                break
    for n in names:
        if n.lower() in ("word/document.xml", "xl/sharedstrings.xml"):
            body = zf.read(n)[:2_000_000]
            if re.search(rb"(?i)\bDDEAUTO\b|instrText[^>]*>\s*DDE\b", body):
                f.add(40, f"{f.filename}: DDE field can run commands", True)


def _analyze_ole(f: AttachmentFinding, data: bytes, ext: str):
    if re.search(rb"(?i)_VBA_PROJECT|\x00V\x00B\x00A\x00|Attribute VB_", data):
        f.add(40, f"{f.filename}: legacy Office document with VBA macros", True)
    if re.search(rb"(?i)AutoOpen|Document_Open|Workbook_Open|Auto_Open", data):
        f.add(15, f"{f.filename}: macro runs automatically when opened", True)
    if re.search(rb"(?i)Equation\.3", data):
        f.add(35, f"{f.filename}: Equation Editor object (CVE-2017-11882 exploit vector)", True)
    if ext == "msi":
        f.add(20, f"{f.filename}: Windows installer package", True)


def _analyze_rtf(f: AttachmentFinding, data: bytes):
    if re.search(rb"(?i)\\objdata|\\objupdate|\\object", data):
        f.add(30, f"{f.filename}: RTF with embedded OLE object", True)
    if re.search(rb"(?i)equation\.3|0002CE02-0000-0000-C000-000000000046", data):
        f.add(35, f"{f.filename}: RTF Equation Editor exploit pattern", True)


def _analyze_pdf(f: AttachmentFinding, data: bytes):
    sample = data[:5_000_000]
    js = len(re.findall(rb"/JavaScript|/JS\b", sample))
    if js:
        f.add(30, f"{f.filename}: PDF contains JavaScript", True)
    if re.search(rb"/Launch", sample):
        f.add(40, f"{f.filename}: PDF can launch programs", True)
    if re.search(rb"/OpenAction|/AA\b", sample) and js:
        f.add(15, f"{f.filename}: PDF runs actions automatically when opened", True)
    if re.search(rb"/EmbeddedFile", sample):
        f.add(15, f"{f.filename}: PDF carries embedded files", True)
    if re.search(rb"/Encrypt", sample):
        f.add(5, f"{f.filename}: encrypted PDF")
    uris = re.findall(rb"/URI\s*\(([^)]{4,300})\)", sample)
    if uris and len(sample) < 200_000 and js == 0:
        # one-page "click here to view your document" lure
        f.add(10, f"{f.filename}: small PDF that mainly links to a website ({uris[0][:60].decode('latin-1')})")


def _analyze_html(f: AttachmentFinding, data: bytes):
    text = data[:3_000_000].decode("utf-8", "replace")
    page = html_facts(text)
    if page.password_inputs:
        f.add(60, f"{f.filename}: HTML attachment with a password form (local credential-phishing page)")
    elif page.forms:
        f.add(20, f"{f.filename}: HTML attachment with a form")
    js = " ".join(page.scripts) + text
    if re.search(r"(?i)\batob\s*\(|unescape\s*\(|String\.fromCharCode|\\x[0-9a-f]{2}\\x[0-9a-f]{2}", js) and \
            re.search(r"(?i)\beval\s*\(|document\.write|window\.location|location\.(href|replace)", js):
        f.add(30, f"{f.filename}: obfuscated script that rewrites or redirects the page")
    if re.search(r"(?i)(window\.location|location\.(href|replace)\s*=|http-equiv=[\"']?refresh)", text):
        f.add(15, f"{f.filename}: HTML attachment redirects to a website")
    if re.search(r"(?i)data:application/(octet-stream|x-msdownload|zip)|msSaveOrOpenBlob|new Blob\(", text):
        f.add(40, f"{f.filename}: HTML smuggling - assembles a file download inside the browser", True)
