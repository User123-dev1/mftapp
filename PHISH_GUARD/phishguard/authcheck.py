"""SPF, DKIM and DMARC.

Two sources, used together:

* ``Authentication-Results`` stamped by the receiving server (Exchange Online
  stamps spf / dkim / dmarc / compauth on every inbound message). Only headers
  from trusted receivers count - anyone can forge one in the message they send.
* Our own evaluation in gateway mode, where we know the connecting IP:
  SPF and DMARC via DNS (dnspython), DKIM signatures via dkimpy if installed.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .domains import registrable_domain
from .emailparse import ParsedEmail

try:
    import dkim  # dkimpy, optional
except ImportError:  # pragma: no cover - optional dependency
    dkim = None


@dataclass
class AuthResult:
    spf: str = "none"
    dkim: str = "none"
    dmarc: str = "none"
    compauth: str = ""
    dmarc_policy: str = ""
    spf_domain: str = ""
    dkim_domains: List[str] = field(default_factory=list)
    source: str = "none"        # header | evaluated | none
    details: List[str] = field(default_factory=list)


_RESULT_RE = re.compile(r"\b(spf|dkim|dmarc|compauth)\s*=\s*([a-z]+)", re.I)
_PROP_RE = re.compile(r"\b(smtp\.mailfrom|header\.d|header\.from|header\.i)\s*=\s*([^\s;]+)", re.I)


def parse_authentication_results(values: List[str]) -> Optional[AuthResult]:
    """Parse Authentication-Results headers (RFC 8601 / Exchange Online style)."""
    if not values:
        return None
    res = AuthResult(source="header")
    for value in values:
        for clause in re.split(r";", value):
            m = _RESULT_RE.search(clause)
            if not m:
                continue
            method, outcome = m.group(1).lower(), m.group(2).lower()
            props = {k.lower(): v.strip("<>\"'").lower() for k, v in _PROP_RE.findall(clause)}
            if method == "spf":
                res.spf = outcome
                res.spf_domain = props.get("smtp.mailfrom", "").split("@")[-1]
            elif method == "dkim":
                # several signatures: any pass wins
                if res.dkim != "pass":
                    res.dkim = outcome
                d = props.get("header.d") or props.get("header.i", "").split("@")[-1]
                if d and outcome == "pass":
                    res.dkim_domains.append(d)
            elif method == "dmarc":
                res.dmarc = outcome
                act = re.search(r"action=([a-z]+)", clause, re.I)
                if act:
                    res.dmarc_policy = act.group(1).lower()
            elif method == "compauth":
                res.compauth = outcome
    return res


# --------------------------------------------------------------------------
# Own evaluation (gateway mode)
# --------------------------------------------------------------------------

class DNS:
    """Thin DNS wrapper so tests can inject answers."""

    def __init__(self, timeout: float = 3.0):
        self.timeout = timeout

    def _query(self, name: str, rtype: str) -> List[str]:
        import dns.exception
        import dns.resolver
        try:
            answer = dns.resolver.resolve(name, rtype, lifetime=self.timeout)
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers):
            return []
        except dns.exception.DNSException as exc:
            raise TempError(str(exc))
        if rtype == "TXT":
            return [b"".join(r.strings).decode("utf-8", "replace") for r in answer]
        if rtype == "MX":
            return [str(r.exchange).rstrip(".") for r in answer]
        return [r.to_text() for r in answer]

    def txt(self, name: str) -> List[str]:
        return self._query(name, "TXT")

    def a(self, name: str) -> List[str]:
        return self._query(name, "A") + self._query(name, "AAAA")

    def mx(self, name: str) -> List[str]:
        return self._query(name, "MX")


class TempError(Exception):
    pass


def check_spf(ip: str, domain: str, dns_: DNS, _depth: int = 0, _lookups: Optional[List[int]] = None) -> str:
    """Evaluate SPF (RFC 7208 core mechanisms). Returns pass/fail/softfail/neutral/none/permerror/temperror."""
    lookups = _lookups if _lookups is not None else [0]
    if _depth > 10:
        return "permerror"
    try:
        records = [r for r in dns_.txt(domain) if r.lower().startswith("v=spf1")]
    except TempError:
        return "temperror"
    if not records:
        return "none"
    if len(records) > 1:
        return "permerror"
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return "permerror"
    redirect = None
    qualifiers = {"+": "pass", "-": "fail", "~": "softfail", "?": "neutral"}
    for term in records[0].split()[1:]:
        if term.lower().startswith("redirect="):
            redirect = term.split("=", 1)[1]
            continue
        if "=" in term:          # other modifiers (exp=...) are ignored
            continue
        qual = term[0] if term[0] in qualifiers else "+"
        mech = term[1:] if term[0] in qualifiers else term
        name, _, arg = mech.partition(":")
        name = name.lower().split("/")[0]
        try:
            if name == "all":
                return qualifiers[qual]
            if name in ("ip4", "ip6"):
                if addr in ipaddress.ip_network(arg, strict=False):
                    return qualifiers[qual]
            elif name in ("a", "mx"):
                lookups[0] += 1
                target = arg or domain
                hosts = dns_.mx(target) if name == "mx" else [target]
                for h in hosts:
                    for a in dns_.a(h):
                        if addr == ipaddress.ip_address(a):
                            return qualifiers[qual]
            elif name == "include":
                lookups[0] += 1
                if lookups[0] > 10:
                    return "permerror"
                inner = check_spf(ip, arg, dns_, _depth + 1, lookups)
                if inner == "pass":
                    return qualifiers[qual]
                if inner in ("temperror", "permerror"):
                    return inner
            elif name == "exists":
                lookups[0] += 1
                if dns_.a(arg):
                    return qualifiers[qual]
        except TempError:
            return "temperror"
        except ValueError:
            return "permerror"
    if redirect:
        lookups[0] += 1
        return check_spf(ip, redirect, dns_, _depth + 1, lookups)
    return "neutral"


def dmarc_record(domain: str, dns_: DNS) -> Dict[str, str]:
    for name in (f"_dmarc.{domain}", f"_dmarc.{registrable_domain(domain)}"):
        try:
            for rec in dns_.txt(name):
                if rec.lower().startswith("v=dmarc1"):
                    return {k.strip().lower(): v.strip() for k, _, v in
                            (part.partition("=") for part in rec.split(";")) if k.strip()}
        except TempError:
            return {"error": "temperror"}
    return {}


def evaluate(email: ParsedEmail, client_ip: Optional[str], mail_from: Optional[str], dns_: Optional[DNS]) -> AuthResult:
    """Our own SPF/DKIM/DMARC result (gateway mode)."""
    res = AuthResult(source="evaluated")
    from_domain = email.from_domain
    envelope_domain = (mail_from or email.return_path or email.from_address).rsplit("@", 1)[-1].lower()
    if client_ip and dns_ and envelope_domain:
        res.spf = check_spf(client_ip, envelope_domain, dns_)
        res.spf_domain = envelope_domain
        res.details.append(f"SPF for {envelope_domain} from {client_ip}: {res.spf}")
    if dkim is not None and email.header("DKIM-Signature"):
        try:
            ok = dkim.verify(email.raw)
            res.dkim = "pass" if ok else "fail"
            m = re.search(r"\bd=([^;\s]+)", email.header("DKIM-Signature"))
            if ok and m:
                res.dkim_domains.append(m.group(1).lower())
        except Exception as exc:  # malformed signatures count as fail
            res.dkim = "fail"
            res.details.append(f"DKIM error: {exc}")
    elif email.header("DKIM-Signature"):
        res.dkim = "unchecked"
    if dns_ and from_domain:
        rec = dmarc_record(from_domain, dns_)
        if rec.get("error"):
            res.dmarc = "temperror"
        elif rec:
            res.dmarc_policy = rec.get("p", "none").lower()
            org = registrable_domain(from_domain)
            spf_aligned = res.spf == "pass" and registrable_domain(res.spf_domain) == org
            dkim_aligned = any(registrable_domain(d) == org for d in res.dkim_domains)
            res.dmarc = "pass" if (spf_aligned or dkim_aligned) else "fail"
            res.details.append(f"DMARC p={res.dmarc_policy}: {res.dmarc}")
    return res


def trusted_header_result(email: ParsedEmail, trusted_authserv: List[str]) -> Optional[AuthResult]:
    """Authentication-Results from a receiver we trust (or Exchange Online's unlabelled stamp)."""
    values = email.header_all("Authentication-Results")
    if trusted_authserv:
        values = [v for v in values if v.split(";")[0].strip().lower() in trusted_authserv
                  or "protection.outlook.com" in v.lower()]
    # Exchange Online also stamps a copy for its internal hops; the first (topmost) wins
    return parse_authentication_results(values[:1]) if values else None
