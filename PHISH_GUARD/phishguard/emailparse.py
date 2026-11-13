"""Parse RFC 5322 messages into the facts the detectors need."""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, parseaddr
from html.parser import HTMLParser
from typing import List, Optional, Tuple

URL_RE = re.compile(r"""(?i)\b((?:https?://|www\.)[^\s<>"'()\[\]{}]+)""")


@dataclass
class Link:
    url: str
    text: str = ""            # visible anchor text (HTML) or "" for bare URLs
    source: str = "html"      # html | text | attachment


@dataclass
class Attachment:
    filename: str
    content_type: str
    data: bytes
    sha256: str = ""

    def __post_init__(self):
        self.sha256 = hashlib.sha256(self.data).hexdigest()


@dataclass
class ParsedEmail:
    raw: bytes
    subject: str = ""
    from_display: str = ""
    from_address: str = ""
    reply_to: str = ""
    return_path: str = ""
    to: List[str] = field(default_factory=list)
    message_id: str = ""
    date: str = ""
    headers: List[Tuple[str, str]] = field(default_factory=list)
    text: str = ""            # plain text (from text/plain, or derived from HTML)
    html: str = ""
    links: List[Link] = field(default_factory=list)
    attachments: List[Attachment] = field(default_factory=list)

    @property
    def from_domain(self) -> str:
        return self.from_address.rsplit("@", 1)[-1].lower() if "@" in self.from_address else ""

    def header_all(self, name: str) -> List[str]:
        return [v for k, v in self.headers if k.lower() == name.lower()]

    def header(self, name: str) -> str:
        values = self.header_all(name)
        return values[0] if values else ""


class _HTMLExtractor(HTMLParser):
    """Collect visible text, anchors (href + text) and form/script facts."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text_parts: List[str] = []
        self.links: List[Link] = []
        self._href: Optional[str] = None
        self._anchor_text: List[str] = []
        self._skip = 0
        self.forms: List[str] = []
        self.password_inputs = 0
        self.scripts: List[str] = []
        self._in_script = False
        self.meta_refresh: List[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "style":
            self._skip += 1
        if tag == "script":
            self._in_script = True
            if a.get("src"):
                self.scripts.append("src:" + a["src"])
        if tag == "title":
            self._in_title = True
        if tag == "a" and a.get("href"):
            self._href = a["href"].strip()
            self._anchor_text = []
        if tag == "form":
            self.forms.append(a.get("action", ""))
        if tag == "input" and a.get("type", "").lower() == "password":
            self.password_inputs += 1
        if tag == "meta" and a.get("http-equiv", "").lower() == "refresh":
            self.meta_refresh.append(a.get("content", ""))
        if tag in ("br", "p", "div", "tr", "li"):
            self.text_parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "style" and self._skip:
            self._skip -= 1
        if tag == "script":
            self._in_script = False
        if tag == "title":
            self._in_title = False
        if tag == "a" and self._href is not None:
            self.links.append(Link(self._href, " ".join("".join(self._anchor_text).split()), "html"))
            self._href = None

    def handle_data(self, data):
        if self._in_script:
            self.scripts.append(data)
            return
        if self._in_title:
            self.title += data
        if self._skip:
            return
        self.text_parts.append(data)
        if self._href is not None:
            self._anchor_text.append(data)

    @property
    def text(self) -> str:
        return re.sub(r"\n\s*\n+", "\n\n", "".join(self.text_parts)).strip()


def html_facts(markup: str) -> _HTMLExtractor:
    parser = _HTMLExtractor()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # malformed HTML must never break scanning
        pass
    return parser


def _decode(part: EmailMessage) -> str:
    try:
        return part.get_content()
    except Exception:
        payload = part.get_payload(decode=True) or b""
        return payload.decode(part.get_content_charset() or "utf-8", errors="replace")


def _leaf_parts(part: EmailMessage):
    """Like walk(), but an attached message/rfc822 is one leaf, not descended into."""
    if part.get_content_type() == "message/rfc822" and part is not None:
        yield part
    elif part.is_multipart():
        for sub in part.iter_parts():
            yield from _leaf_parts(sub)
    else:
        yield part


def parse_email(raw: bytes) -> ParsedEmail:
    msg: EmailMessage = BytesParser(policy=policy.default).parsebytes(raw)
    p = ParsedEmail(raw=raw)
    p.headers = [(k, str(v)) for k, v in msg.raw_items()]
    p.subject = str(msg.get("Subject", "") or "")
    display, address = parseaddr(str(msg.get("From", "") or ""))
    p.from_display, p.from_address = display.strip(), address.strip().lower()
    p.reply_to = parseaddr(str(msg.get("Reply-To", "") or ""))[1].lower()
    p.return_path = parseaddr(str(msg.get("Return-Path", "") or ""))[1].lower()
    p.to = [a.lower() for _, a in getaddresses([str(v) for v in msg.get_all("To", []) + msg.get_all("Cc", [])]) if a]
    p.message_id = str(msg.get("Message-ID", "") or "").strip()
    p.date = str(msg.get("Date", "") or "")

    texts, htmls = [], []
    for part in _leaf_parts(msg):
        disposition = (part.get_content_disposition() or "").lower()
        filename = part.get_filename()
        ctype = part.get_content_type()
        if ctype == "message/rfc822":
            # attached / forwarded e-mail: keep it whole (it is analysed on its own when reported)
            inner = part.get_payload()
            inner = inner[0] if isinstance(inner, list) and inner else inner
            data = inner.as_bytes() if hasattr(inner, "as_bytes") else bytes(inner or b"")
            cte = str(part.get("Content-Transfer-Encoding", "")).lower()
            if cte in ("base64", "quoted-printable"):
                # some clients encode the attached message; undo it to get the original bytes
                import base64
                import quopri
                data = base64.b64decode(data) if cte == "base64" else quopri.decodestring(data)
            p.attachments.append(Attachment(filename or "attached-message.eml", ctype, data))
        elif filename or disposition == "attachment" or ctype not in ("text/plain", "text/html"):
            data = part.get_payload(decode=True) or b""
            p.attachments.append(Attachment(filename or f"unnamed.{ctype.split('/')[-1]}", ctype, data))
        elif ctype == "text/html":
            htmls.append(_decode(part))
        else:
            texts.append(_decode(part))

    p.html = "\n".join(htmls)
    if p.html:
        facts = html_facts(p.html)
        p.links.extend(facts.links)
        derived = facts.text
    else:
        derived = ""
    p.text = "\n".join(texts).strip() or derived
    seen = {l.url for l in p.links}
    for m in URL_RE.finditer(p.text):
        url = m.group(1).rstrip(".,;:!?")
        if url not in seen:
            seen.add(url)
            p.links.append(Link(url, "", "text"))
    p.links = [Link(html.unescape(l.url), l.text, l.source) for l in p.links]
    return p
