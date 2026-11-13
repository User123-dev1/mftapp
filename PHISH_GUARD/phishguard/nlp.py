"""Fast language heuristics for social-engineering cues.

Runs on every message (no network, no cost). Claude (llm.py) adds a deeper
reading of tone, context and intent on top; either can raise the score.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

# (category, weight, pattern). Weights are per category, counted once.
_PATTERNS: List[Tuple[str, int, str]] = [
    ("urgency", 8, r"\b(urgent(ly)?|immediately|asap|right away|within (the next )?\d+ (hours?|minutes?)|"
                   r"as soon as possible|time[- ]sensitive|final (notice|reminder|warning)|act now|today only|"
                   r"before (end of|close of) (day|business)|expires? (today|soon))\b"),
    ("secrecy", 12, r"\b(keep (this|it) (confidential|between us|quiet)|do not (tell|discuss|share)|"
                    r"don'?t (tell|mention) (anyone|this)|strictly confidential|discreet(ly)?)\b"),
    ("availability_pretext", 10, r"\b(are you (available|at your desk|around)|i'?m (in a meeting|travel(l)?ing|"
                                  r"on a flight|tied up)|can'?t (talk|take calls)|reply (to me )?(by|via) (email|text))\b"),
    ("payment_change", 25, r"\b((change|update|new)[d]? (our |my )?(bank(ing)?|account|payment|wire|remittance) "
                           r"(details|information|instructions)|(bank|account) (details|number) (has|have) changed|"
                           r"new (iban|swift|routing|account number)|update (the )?vendor (bank|payment))\b"),
    ("wire_transfer", 15, r"\b(wire transfer|bank transfer|swift|iban|routing number|ach (payment|transfer)|"
                          r"process (a|the) (payment|transfer)|outstanding (invoice|balance|payment)|remit(tance)?)\b"),
    ("gift_cards", 25, r"\b(gift ?cards?|itunes card|google play card|steam card|amazon card|"
                       r"scratch (off )?the (back|code)|send (me )?the (codes|card numbers))\b"),
    ("credential_request", 20, r"\b(verify your (account|identity|email|password|mailbox)|confirm your "
                               r"(password|credentials|account|login)|re-?enter your password|"
                               r"(mailbox|account|password) (will be|is) (suspended|deactivated|deleted|disabled|"
                               r"locked|expired?)|unusual sign-?in|validate your (account|mailbox)|"
                               r"storage (is )?(full|quota)|shared (a )?document with you)\b"),
    ("threat", 10, r"\b(legal action|lawsuit|penalt(y|ies)|arrest|suspend(ed)?|terminat(e|ed|ion)|"
                   r"account (closure|closed)|late fee|debt collect|police)\b"),
    ("reward", 10, r"\b(you('ve| have) won|winner|lottery|prize|refund (is )?(pending|due|available)|"
                   r"claim your|unclaimed|inheritance|compensation fund|beneficiary)\b"),
    ("crypto", 10, r"\b(bitcoin|btc wallet|crypto(currency)? wallet|usdt|seed phrase|recovery phrase|"
                   r"wallet address)\b"),
    ("invoice", 6, r"\b(invoice|purchase order|payment advice|remittance advice|statement of account|"
                   r"overdue|past due)\b"),
    ("callback", 12, r"\b(call (us|our (support|billing))( team)? (at|on)|(toll[- ]free|helpline|support) "
                     r"(number|line)|cancel (your|this) (subscription|order) (call|by calling))\b"),
    ("authority", 8, r"\b(ceo|cfo|chief executive|managing director|president|the board|"
                     r"(irs|hmrc|tax (office|authority))|it (department|support|help ?desk)|administrator)\b"),
]
_COMPILED = [(c, w, re.compile(p, re.I)) for c, w, p in _PATTERNS]


@dataclass
class LanguageFindings:
    score: int = 0
    categories: Dict[str, str] = field(default_factory=dict)   # category -> matched text
    reasons: List[str] = field(default_factory=list)


def analyze_text(subject: str, text: str) -> LanguageFindings:
    content = f"{subject}\n{text}"[:200_000]
    res = LanguageFindings()
    for category, weight, rx in _COMPILED:
        m = rx.search(content)
        if m:
            res.categories[category] = m.group(0)
            res.score += weight
            res.reasons.append(f"{category.replace('_', ' ')}: \"{m.group(0)}\"")
    cats = set(res.categories)
    # Combinations that are much stronger together than apart (classic BEC / invoice fraud)
    if {"payment_change", "urgency"} <= cats or {"payment_change", "secrecy"} <= cats:
        res.score += 15
        res.reasons.append("payment-detail change combined with pressure (invoice/BEC fraud pattern)")
    if "gift_cards" in cats and (cats & {"authority", "secrecy", "availability_pretext", "urgency"}):
        res.score += 15
        res.reasons.append("gift-card request with pressure or authority (CEO-fraud pattern)")
    if "credential_request" in cats and (cats & {"urgency", "threat"}):
        res.score += 10
        res.reasons.append("credential request with urgency or threat")
    if "availability_pretext" in cats and (cats & {"wire_transfer", "gift_cards", "secrecy"}):
        res.score += 10
        res.reasons.append("\"are you available?\" opener leading to money (BEC pattern)")
    res.score = min(res.score, 60)
    return res
