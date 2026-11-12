"""Link analysis, URL rewriting and time-of-click page inspection."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
import socket
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

from .domains import find_lookalike, registrable_domain, to_unicode
from .emailparse import Link, html_facts

SHORTENERS = {
    "bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly", "is.gd", "buff.ly", "rebrand.ly", "cutt.ly", "shorturl.at",
    "rb.gy", "t.ly", "tiny.cc", "s.id", "lnkd.in", "bl.ink", "short.io", "qrco.de",
}
# TLDs heavily abused for phishing (Spamhaus / Interisle reports); a weak signal on its own
RISKY_TLDS = {
    "zip", "mov", "top", "xyz", "click", "link", "work", "rest", "gq", "tk", "ml", "cf", "ga", "country", "kim",
    "cam", "icu", "monster", "buzz", "sbs", "cfd", "lol", "quest", "beauty", "hair", "bond", "support",
}
# Free hosting / form / file services commonly used to host phishing pages
ABUSED_HOSTS = {
    "firebaseapp.com", "web.app", "pages.dev", "workers.dev", "netlify.app", "vercel.app", "glitch.me",
    "github.io", "000webhostapp.com", "weebly.com", "wixsite.com", "sites.google.com", "forms.gle",
    "docs.google.com", "storage.googleapis.com", "blob.core.windows.net", "web.core.windows.net",
    "ipfs.io", "dweb.link", "r2.dev", "typedream.app", "notion.site", "jotform.com", "formstack.com",
}
CREDENTIAL_WORDS = re.compile(r"(?i)(log-?in|sign-?in|verify|verification|account|password|secure|update|unlock|"
                              r"auth|wallet|banking|invoice|payment|webmail|office365|o365|sharepoint|onedrive|mfa)")


@dataclass
class UrlFinding:
    url: str
    host: str
    score: int
    reasons: List[str] = field(default_factory=list)

    def add(self, points: int, reason: str):
        self.score += points
        self.reasons.append(reason)


def normalize(url: str) -> str:
    url = url.strip()
    if url.lower().startswith("www."):
        url = "http://" + url
    return url


def host_of(url: str) -> str:
    try:
        return (urlsplit(normalize(url)).hostname or "").lower()
    except ValueError:
        return ""


def analyze_url(link: Link, protected: List[str], blocked: Callable[[str, str], Optional[str]]) -> UrlFinding:
    """Static risk of one link. ``blocked(kind, value)`` returns a note if threat intel lists it."""
    url = normalize(link.url)
    f = UrlFinding(url, "", 0)
    scheme = url.split(":", 1)[0].lower()
    if scheme in ("javascript", "data", "vbscript", "file"):
        f.add(40, f"Link uses dangerous scheme '{scheme}:'")
        return f
    if scheme in ("mailto", "tel", "cid") or url.startswith("#"):
        return f
    try:
        parts = urlsplit(url)
    except ValueError:
        f.add(10, "Malformed URL")
        return f
    host = (parts.hostname or "").lower()
    f.host = host
    if not host:
        return f
    reg = registrable_domain(host)
    for kind, value in (("url", url), ("domain", host), ("domain", reg)):
        note = blocked(kind, value)
        if note is not None:
            f.add(70, f"Link matches threat intelligence ({kind} {value}{': ' + note if note else ''})")
            break
    try:
        ipaddress.ip_address(host)
        f.add(25, "Link points to a raw IP address")
    except ValueError:
        pass
    if "xn--" in host:
        f.add(15, f"Internationalised (punycode) domain {to_unicode(host)}")
    if "@" in parts.netloc:
        f.add(25, "URL hides its real destination with '@'")
    look = find_lookalike(host, protected)
    if look:
        f.add(look.severity + 10, f"Lookalike of {look.imitates} ({look.method}): {host}")
    if reg in SHORTENERS:
        f.add(10, f"URL shortener ({reg}) hides the destination")
    tld = host.rsplit(".", 1)[-1]
    if tld in RISKY_TLDS:
        f.add(8, f"Frequently abused top-level domain .{tld}")
    if any(host == h or host.endswith("." + h) for h in ABUSED_HOSTS):
        f.add(10, f"Free hosting service often used for phishing pages ({host})")
    if host.count(".") >= 4:
        f.add(5, "Unusually deep subdomain chain")
    if scheme == "http" and CREDENTIAL_WORDS.search(url):
        f.add(8, "Login/verification page over unencrypted HTTP")
    elif CREDENTIAL_WORDS.search(parts.path + "?" + parts.query) and (look or tld in RISKY_TLDS):
        f.add(10, "Credential-themed path on a suspicious domain")
    # Visible text shows one site, link goes to another
    shown = link.text.strip()
    shown_host = host_of(shown) if re.match(r"(?i)^(https?://|www\.)?[\w.-]+\.[a-z]{2,}", shown) else ""
    if shown_host and registrable_domain(shown_host) != reg:
        f.add(30, f"Link text shows {shown_host} but goes to {host}")
    return f


def defang(text: str) -> str:
    """Make URLs in warnings unclickable: http://evil.com/x -> hxxp://evil[.]com/x"""
    def repl(m):
        url = m.group(0)
        scheme, rest = url.split("://", 1)
        host, sep, path = rest.partition("/")
        return scheme.replace("t", "x", 2) + "://" + host.replace(".", "[.]") + sep + path
    return re.sub(r"(?i)\bhttps?://[^\s<>\"')]+", repl, text or "")


# --------------------------------------------------------------------------
# Rewriting (gateway mode)
# --------------------------------------------------------------------------

def sign(secret: str, value: str) -> str:
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()[:20]


def rewrite_html(markup: str, make_link: Callable[[str], str]) -> str:
    """Replace http(s) hrefs with protected links; leaves mailto:, anchors etc."""
    def repl(m):
        quote, url = m.group(1), m.group(2)
        if not re.match(r"(?i)https?://", url):
            return m.group(0)
        return f"href={quote}{make_link(url.replace('&amp;', '&'))}{quote}"
    return re.sub(r"""(?i)href\s*=\s*(["'])(.*?)\1""", repl, markup)


def rewrite_text(text: str, make_link: Callable[[str], str]) -> str:
    return re.sub(r"(?i)\bhttps?://[^\s<>\"')\]]+", lambda m: make_link(m.group(0)), text)


# --------------------------------------------------------------------------
# Time-of-click inspection
# --------------------------------------------------------------------------

class UnsafeDestination(Exception):
    pass


def assert_public(host: str, resolver: Callable[[str], List[str]] = None) -> None:
    """Refuse to fetch anything that resolves to a private/loopback/link-local address (SSRF)."""
    resolver = resolver or (lambda h: [ai[4][0] for ai in socket.getaddrinfo(h, None)])
    try:
        addresses = [host] if _is_ip(host) else resolver(host)
    except OSError as exc:
        raise UnsafeDestination(f"cannot resolve {host}: {exc}")
    if not addresses:
        raise UnsafeDestination(f"{host} does not resolve")
    for a in addresses:
        ip = ipaddress.ip_address(a.split("%")[0])
        if not ip.is_global or ip.is_multicast:
            raise UnsafeDestination(f"{host} resolves to a non-public address ({ip})")


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


@dataclass
class PageInspection:
    final_url: str
    hops: List[str]
    status: int = 0
    score: int = 0
    reasons: List[str] = field(default_factory=list)
    title: str = ""


def inspect_page(url: str, protected: List[str], blocked: Callable[[str, str], Optional[str]],
                 http_get: Callable[[str], Tuple[int, dict, bytes]],
                 resolver: Callable[[str], List[str]] = None, max_hops: int = 6) -> PageInspection:
    """Follow redirects one hop at a time (each hop re-checked), then inspect the landing page."""
    hops: List[str] = []
    current = normalize(url)
    status, headers, body = 0, {}, b""
    for _ in range(max_hops):
        host = host_of(current)
        if not current.lower().startswith(("http://", "https://")) or not host:
            raise UnsafeDestination("not an http(s) URL")
        assert_public(host, resolver)
        hops.append(current)
        status, headers, body = http_get(current)
        location = {k.lower(): v for k, v in headers.items()}.get("location")
        if status in (301, 302, 303, 307, 308) and location:
            current = urljoin(current, location)
            continue
        break
    insp = PageInspection(current, hops, status)
    for hop in hops[1:]:
        f = analyze_url(Link(hop), protected, blocked)
        if f.score:
            insp.score += f.score
            insp.reasons += [f"Redirect hop {hop}: {r}" for r in f.reasons]
    if len(hops) > 3:
        insp.score += 10
        insp.reasons.append(f"Redirect chain of {len(hops)} hops")
    ctype = {k.lower(): v for k, v in headers.items()}.get("content-type", "")
    final_host = host_of(current)
    if "html" in ctype or body[:200].lstrip().lower().startswith((b"<!doctype html", b"<html")):
        page = html_facts(body[:1_000_000].decode("utf-8", "replace"))
        insp.title = page.title.strip()[:200]
        text = (page.title + " " + page.text[:5000]).lower()
        if page.password_inputs:
            brands = [p for p in protected if p.split(".")[0] in text and registrable_domain(final_host) != registrable_domain(p)]
            if brands:
                insp.score += 60
                insp.reasons.append(f"Password form imitating {brands[0]} on {final_host}")
            else:
                insp.score += 15
                insp.reasons.append(f"Page asks for a password ({final_host})")
        for action in page.forms:
            target = host_of(urljoin(current, action)) if action else final_host
            if target and registrable_domain(target) != registrable_domain(final_host) and page.password_inputs:
                insp.score += 25
                insp.reasons.append(f"Login form submits to another site ({target})")
        js = " ".join(page.scripts)
        if re.search(r"(?i)\b(atob|unescape|fromCharCode)\s*\(", js) and re.search(r"(?i)\beval\s*\(|document\.write", js):
            insp.score += 15
            insp.reasons.append("Obfuscated JavaScript on landing page")
        if page.meta_refresh:
            insp.score += 5
            insp.reasons.append("Page auto-redirects (meta refresh)")
    elif any(t in ctype for t in ("application/x-msdownload", "application/x-dosexec", "application/octet-stream",
                                   "application/x-iso9660", "application/java-archive", "application/hta")):
        insp.score += 35
        insp.reasons.append(f"Link downloads a file ({ctype})")
    final = analyze_url(Link(current), protected, blocked)
    insp.score += final.score
    insp.reasons += final.reasons
    return insp


def httpx_get(timeout: float = 8.0, max_bytes: int = 1_000_000) -> Callable[[str], Tuple[int, dict, bytes]]:
    """Single-hop GET (no automatic redirects, bounded body) for inspect_page."""
    import httpx

    def get(url: str):
        with httpx.Client(follow_redirects=False, timeout=timeout,
                          headers={"User-Agent": "Mozilla/5.0 (PhishGuard link inspection)"}) as client:
            with client.stream("GET", url) as resp:
                chunks, size = [], 0
                for chunk in resp.iter_bytes():
                    chunks.append(chunk)
                    size += len(chunk)
                    if size >= max_bytes:
                        break
                return resp.status_code, dict(resp.headers), b"".join(chunks)
    return get
