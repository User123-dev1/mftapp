"""Domain helpers and lookalike (homoglyph / typosquat) detection."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Iterable, List, Optional

# Second-level public suffixes common enough to matter for "registrable domain"
_TWO_LEVEL_SUFFIXES = {
    "co.uk", "org.uk", "ac.uk", "gov.uk", "ltd.uk", "plc.uk", "com.au", "net.au", "org.au", "gov.au",
    "co.nz", "co.za", "com.br", "com.cn", "com.hk", "com.sg", "com.mx", "com.tr", "co.jp", "co.in",
    "co.kr", "com.gh", "com.ng", "co.ke", "com.ar", "com.my", "com.ph", "com.pk", "com.sa", "com.eg",
}

# Character sequences commonly used to imitate others ("rn" looks like "m")
_SEQUENCES = (("rn", "m"), ("vv", "w"), ("cl", "d"), ("nn", "m"), ("ii", "u"))
# Single characters that render like Latin letters or each other
_CONFUSABLE = {
    "0": "o", "1": "l", "i": "l", "|": "l", "3": "e", "5": "s", "$": "s", "@": "a", "4": "a", "8": "b",
    # Cyrillic
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "l", "ј": "j", "ѕ": "s",
    "к": "k", "м": "m", "т": "t", "в": "b", "н": "h", "ԁ": "d", "ӏ": "l", "ɡ": "g",
    # Greek
    "α": "a", "ο": "o", "ρ": "p", "ν": "v", "τ": "t", "κ": "k", "ι": "l", "ε": "e", "υ": "u", "χ": "x",
}


def registrable_domain(host: str) -> str:
    """example.co.uk from mail.example.co.uk (good enough without the full PSL)."""
    host = (host or "").strip(".").lower()
    labels = host.split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in _TWO_LEVEL_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def to_unicode(host: str) -> str:
    try:
        return host.encode("ascii").decode("idna") if "xn--" in host else host
    except UnicodeError:
        return host


def skeleton(name: str) -> str:
    """Map a domain label to a canonical form so visually similar strings compare equal."""
    s = unicodedata.normalize("NFKC", to_unicode(name).lower())
    s = "".join(_CONFUSABLE.get(ch, ch) for ch in s)
    for seq, rep in _SEQUENCES:
        s = s.replace(seq, rep)
    return "".join(ch for ch in s if ch not in "-_")


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


@dataclass
class Lookalike:
    domain: str
    imitates: str
    method: str        # homoglyph | typo | brand-embedded | idn
    severity: int      # score contribution


def _label(domain: str) -> str:
    """'paypal' from 'paypal.com' / 'example' from 'example.co.uk'."""
    reg = registrable_domain(domain)
    return reg.split(".")[0]


def find_lookalike(domain: str, protected: Iterable[str]) -> Optional[Lookalike]:
    """Is ``domain`` impersonating one of ``protected``? None if it IS one of them."""
    domain = (domain or "").lower().strip(".")
    if not domain:
        return None
    reg = registrable_domain(domain)
    protected = [registrable_domain(p) for p in protected]
    if reg in protected:
        return None
    label, sk = _label(reg), skeleton(_label(reg))
    best: Optional[Lookalike] = None

    def consider(candidate: Lookalike):
        nonlocal best
        if best is None or candidate.severity > best.severity:
            best = candidate

    for target in protected:
        t_label = _label(target)
        if len(t_label) < 4:
            continue
        if sk == skeleton(t_label) and label != t_label:
            method = "idn" if ("xn--" in reg or any(ord(c) > 127 for c in to_unicode(reg))) else "homoglyph"
            consider(Lookalike(domain, target, method, 45))
        elif label == t_label:
            # same name, different TLD (paypal.co, microsoft.support)
            consider(Lookalike(domain, target, "tld-swap", 35))
        elif len(t_label) >= 5 and levenshtein(label, t_label) <= (1 if len(t_label) < 8 else 2):
            consider(Lookalike(domain, target, "typo", 35))
        elif len(t_label) >= 5 and skeleton(t_label) in sk:
            # microsoft-support.com, secure-paypal-login.net
            consider(Lookalike(domain, target, "brand-embedded", 25))
        elif len(t_label) >= 5 and skeleton(t_label) in [skeleton(x) for x in domain.split(".")[:-2]]:
            # paypal.com.account-verify.xyz (brand in a subdomain)
            consider(Lookalike(domain, target, "brand-in-subdomain", 30))
    return best


def is_internal(domain: str, org_domains: List[str]) -> bool:
    reg = registrable_domain(domain)
    return any(reg == registrable_domain(d) for d in org_domains)
