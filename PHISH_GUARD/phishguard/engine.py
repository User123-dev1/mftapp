"""Scan a message: run every detector and combine them into one verdict."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import authcheck
from .attachments import AttachmentFinding, analyze_attachment
from .config import Settings
from .domains import find_lookalike, is_internal, registrable_domain, skeleton
from .emailparse import ParsedEmail, parse_email
from .intel import Intel
from .llm import LLMAnalyzer, LLMVerdict
from .nlp import analyze_text
from .urls import UrlFinding, analyze_url, normalize

FREEMAIL = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com", "yahoo.com", "ymail.com",
    "aol.com", "icloud.com", "me.com", "gmx.com", "gmx.net", "mail.com", "proton.me", "protonmail.com", "zoho.com",
    "yandex.com", "yandex.ru", "qq.com", "163.com", "tutanota.com",
}
BEC_CATEGORIES = {"payment_change", "gift_cards", "availability_pretext", "wire_transfer"}


@dataclass
class Signal:
    area: str         # sender | auth | language | ai | link | attachment | intel
    points: int
    text: str

    def to_dict(self):
        return {"area": self.area, "points": self.points, "text": self.text}


@dataclass
class ScanResult:
    email: ParsedEmail
    score: int
    verdict: str                    # clean | suspicious | malicious
    category: str                   # clean | phishing | bec | malware | scam | spam
    summary: str
    signals: List[Signal]
    indicators: Dict[str, List[str]]
    auth: Dict[str, object]
    llm: Optional[LLMVerdict]
    detonate: List[AttachmentFinding] = field(default_factory=list)
    links: List[UrlFinding] = field(default_factory=list)

    @property
    def reasons(self) -> List[str]:
        return [s.text for s in sorted(self.signals, key=lambda s: -s.points)]


class Scanner:
    def __init__(self, settings: Settings, llm: Optional[LLMAnalyzer] = None, dns: Optional[authcheck.DNS] = None):
        self.settings = settings
        self.llm = llm
        self.dns = dns

    def scan(self, raw: bytes, intel: Intel, *, client_ip: Optional[str] = None, mail_from: Optional[str] = None,
             use_llm: bool = True) -> ScanResult:
        s = self.settings
        email = parse_email(raw)
        protected = s.all_protected()
        signals: List[Signal] = []
        hard_evidence = False

        def add(area: str, points: int, text: str):
            if text:  # zero-point signals are kept as information (AI notes, allow list)
                signals.append(Signal(area, points, text))

        sender, sender_domain = email.from_address, email.from_domain
        internal_sender = bool(sender_domain) and is_internal(sender_domain, s.org_domains)

        # ---- threat intelligence -------------------------------------------------
        note = intel.sender_blocked(sender) if sender else None
        if note is not None:
            add("intel", 80, f"Sender {sender} is on the block list" + (f" ({note})" if note else ""))
            hard_evidence = True
        allowed = bool(sender) and intel.sender_allowed(sender)

        # ---- sender authentication ---------------------------------------------
        if client_ip:
            auth = authcheck.evaluate(email, client_ip, mail_from, self.dns if s.dns_checks else None)
        else:
            auth = authcheck.trusted_header_result(email, []) or authcheck.AuthResult()
        if auth.dmarc == "fail":
            pts = 35 if auth.dmarc_policy in ("reject", "quarantine", "oreject") else 25
            add("auth", pts, f"DMARC failed for {sender_domain} (the From address is not authenticated)")
            hard_evidence = True
        else:
            if auth.compauth == "fail":
                add("auth", 20, "Microsoft composite authentication failed")
            if auth.spf == "fail":
                add("auth", 12, f"SPF failed for {auth.spf_domain or sender_domain}")
            elif auth.spf == "softfail":
                add("auth", 6, f"SPF soft-failed for {auth.spf_domain or sender_domain}")
        if auth.dkim == "fail":
            add("auth", 8, "DKIM signature is invalid")
        authenticated = auth.dmarc == "pass" or (auth.source == "none" and not client_ip)
        if internal_sender and auth.source != "none" and auth.dmarc != "pass" and auth.compauth != "pass":
            add("sender", 40, f"Claims to be from your own domain ({sender_domain}) but failed authentication")
            hard_evidence = True

        # ---- sender identity ----------------------------------------------------
        look = find_lookalike(sender_domain, protected) if sender_domain else None
        if look:
            add("sender", look.severity + 15, f"Sender domain {sender_domain} imitates {look.imitates} ({look.method})")
            hard_evidence = True
        display = email.from_display.lower()
        embedded = re.search(r"[\w.+-]+@([\w-]+\.[\w.-]+)", display)
        if embedded and registrable_domain(embedded.group(1)) != registrable_domain(sender_domain):
            add("sender", 25, f"Display name shows {embedded.group(0)} but the mail is from {sender}")
        if display and not internal_sender:
            vip = next((v for v in s.vip_names if v and v in display), None)
            if vip:
                add("sender", 30, f"External sender uses the name of an executive ({email.from_display})")
            else:
                for brand in protected:
                    name = brand.split(".")[0]
                    if len(name) >= 5 and skeleton(name) in skeleton(display.replace(" ", "")) and \
                            registrable_domain(sender_domain) != registrable_domain(brand):
                        add("sender", 20, f"Display name '{email.from_display}' impersonates {brand} "
                                          f"but the mail comes from {sender_domain}")
                        break
        if email.reply_to and sender_domain and registrable_domain(email.reply_to.rsplit("@", 1)[-1]) != \
                registrable_domain(sender_domain):
            rt_domain = email.reply_to.rsplit("@", 1)[-1]
            add("sender", 20 if rt_domain in FREEMAIL else 12,
                f"Replies go to a different address ({email.reply_to}) than the sender")
        if sender_domain in FREEMAIL and not internal_sender and s.vip_names and any(v in display for v in s.vip_names):
            add("sender", 10, "Executive name used from a free webmail account")

        # ---- language -------------------------------------------------------------
        lang = analyze_text(email.subject, email.text)
        if lang.score:
            add("language", lang.score, "Social-engineering language: " + "; ".join(lang.reasons[:6]))

        # ---- links ------------------------------------------------------------------
        link_findings = [analyze_url(l, protected, intel.blocked) for l in email.links[:300]]
        link_findings = [f for f in link_findings if f.score]
        if link_findings:
            link_findings.sort(key=lambda f: -f.score)
            top = link_findings[0]
            rest = sum(f.score for f in link_findings[1:])
            add("link", min(top.score + rest // 4, 90), f"Link {top.url[:120]}: " + "; ".join(top.reasons))
            if any("threat intelligence" in r or "Lookalike" in r for f in link_findings for r in f.reasons):
                hard_evidence = True

        # ---- attachments ------------------------------------------------------------
        att_findings = [analyze_attachment(a, intel.blocked) for a in email.attachments]
        scored = sorted([f for f in att_findings if f.score], key=lambda f: -f.score)
        if scored:
            add("attachment", min(scored[0].score + sum(f.score for f in scored[1:]) // 3, 95),
                "; ".join(r for f in scored[:3] for r in f.reasons[:3]))
            if scored[0].score >= 40:
                hard_evidence = True

        heuristic = sum(sig.points for sig in signals)

        # ---- AI -----------------------------------------------------------------------
        verdict_ai: Optional[LLMVerdict] = None
        if use_llm and self.llm and s.llm_enabled and not (internal_sender and authenticated) and heuristic < 95 \
                and not allowed:
            auth_summary = (f"SPF={auth.spf}, DKIM={auth.dkim}, DMARC={auth.dmarc}"
                            + (f", compauth={auth.compauth}" if auth.compauth else "")
                            + f" (source: {auth.source})")
            verdict_ai = self.llm.analyze(email, auth_summary, [sig.text for sig in signals])
        score = heuristic
        if verdict_ai:
            add("ai", 0, f"AI assessment ({verdict_ai.confidence} confidence, risk {verdict_ai.risk}): "
                         f"{verdict_ai.analyst_notes[:400]}")
            if verdict_ai.risk > score:
                score = max(score, round(0.7 * verdict_ai.risk + 0.3 * score))
                if verdict_ai.confidence == "high":
                    score = max(score, verdict_ai.risk)
            elif verdict_ai.risk < 20 and verdict_ai.confidence == "high" and not hard_evidence:
                score = round(score * 0.6)
        if allowed and not hard_evidence:
            score = max(0, score - 30)
            add("intel", 0, f"Sender {sender} is on the allow list")
        score = max(0, min(100, score))

        verdict = "malicious" if score >= s.quarantine_score else "suspicious" if score >= s.warn_score else "clean"
        category = self._category(verdict, scored, lang.categories, verdict_ai, signals, link_findings)
        summary = self._summary(verdict, category, verdict_ai, signals)
        indicators = {
            "sender": [sender] if sender else [],
            "domain": sorted({d for d in [sender_domain, registrable_domain(sender_domain) if sender_domain else ""]
                              + [registrable_domain(f.host) for f in link_findings if f.host] if d}),
            "url": sorted({normalize(l.url) for l in email.links if l.url.lower().startswith(("http", "www."))})[:200],
            "hash": [a.sha256 for a in email.attachments],
        }
        return ScanResult(email, score, verdict, category, summary, signals, indicators,
                          {"spf": auth.spf, "dkim": auth.dkim, "dmarc": auth.dmarc, "compauth": auth.compauth,
                           "dmarc_policy": auth.dmarc_policy, "source": auth.source},
                          verdict_ai, [f for f in att_findings if f.detonate], link_findings)

    @staticmethod
    def _category(verdict, att_findings, lang_cats, ai, signals, links) -> str:
        if verdict == "clean":
            return "clean"
        if att_findings and att_findings[0].score >= 40:
            # executables, macros, exploits -> malware; local HTML login pages -> phishing
            return "malware" if att_findings[0].detonate else "phishing"
        if ai and ai.risk >= 40:
            return {"invoice_fraud": "bec", "malware_delivery": "malware", "marketing": "spam"}.get(ai.category, ai.category)
        if set(lang_cats) & BEC_CATEGORIES or any("executive" in s.text for s in signals):
            return "bec"
        if "credential_request" in lang_cats or links:
            return "phishing"
        if set(lang_cats) & {"reward", "crypto", "callback"}:
            return "scam"
        return "phishing" if verdict == "malicious" else "spam"

    @staticmethod
    def _summary(verdict, category, ai, signals) -> str:
        if verdict == "clean":
            return "No threats found."
        if ai and ai.user_summary and ai.risk >= 40:
            return ai.user_summary
        top = sorted(signals, key=lambda sig: -sig.points)[:2]
        label = {"bec": "business e-mail compromise / payment fraud", "malware": "malware delivery",
                 "phishing": "phishing", "scam": "a scam", "spam": "unwanted mail"}.get(category, category)
        return f"Looks like {label}: " + "; ".join(sig.text for sig in top)
