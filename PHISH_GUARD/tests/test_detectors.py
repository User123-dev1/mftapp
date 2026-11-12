import tempfile

import pytest

from phishguard import authcheck
from phishguard.attachments import analyze_attachment, sniff
from phishguard.config import Settings
from phishguard.db import Database
from phishguard.domains import find_lookalike, registrable_domain, skeleton
from phishguard.emailparse import Attachment, Link, parse_email
from phishguard.engine import Scanner
from phishguard.intel import Intel
from phishguard.nlp import analyze_text
from phishguard.urls import UnsafeDestination, analyze_url, assert_public, inspect_page, rewrite_html
from tests import samples
from tests.fakes import FakeDNS

PROTECTED = ["microsoft.com", "paypal.com", "abelzeta.com"]
no_intel = lambda kind, value: None


# ---- lookalike domains ---------------------------------------------------------

@pytest.mark.parametrize("domain,imitates,method", [
    ("rnicrosoft.com", "microsoft.com", "homoglyph"),      # rn -> m
    ("paypa1.com", "paypal.com", "homoglyph"),
    ("xn--pypal-4ve.com", "paypal.com", "idn"),            # Cyrillic 'а'
    ("abelzetta.com", "abelzeta.com", "typo"),
    ("paypal.co", "paypal.com", "tld-swap"),
    ("micros0ft-support.net", "microsoft.com", "brand-embedded"),
    ("paypal.com.verify-acct.xyz", "paypal.com", "brand-in-subdomain"),
])
def test_lookalikes(domain, imitates, method):
    look = find_lookalike(domain, PROTECTED)
    assert look and look.imitates == imitates and look.method == method


@pytest.mark.parametrize("domain", ["microsoft.com", "login.microsoft.com", "google.com", "annual-report.com", "abelzeta.com"])
def test_not_lookalikes(domain):
    assert find_lookalike(domain, PROTECTED) is None


def test_registrable_domain_and_skeleton():
    assert registrable_domain("mail.example.co.uk") == "example.co.uk"
    assert skeleton("rnicrosoft") == skeleton("microsoft")


# ---- authentication -------------------------------------------------------------

def test_parse_exchange_online_authentication_results():
    res = authcheck.parse_authentication_results([samples.M365_FAIL.format(d="evil.example")])
    assert (res.spf, res.dkim, res.dmarc, res.compauth, res.dmarc_policy) == ("fail", "none", "fail", "fail", "quarantine")
    ok = authcheck.parse_authentication_results([samples.M365_PASS.format(d="good.example")])
    assert ok.dmarc == "pass" and ok.dkim_domains == ["good.example"]


def test_spf_evaluation():
    dns = FakeDNS(txt={"sender.example": ["v=spf1 ip4:198.51.100.0/24 include:_spf.mailer.example -all"],
                       "_spf.mailer.example": ["v=spf1 ip4:203.0.113.10 ~all"],
                       "soft.example": ["v=spf1 ~all"]})
    assert authcheck.check_spf("198.51.100.7", "sender.example", dns) == "pass"
    assert authcheck.check_spf("203.0.113.10", "sender.example", dns) == "pass"     # via include
    assert authcheck.check_spf("192.0.2.1", "sender.example", dns) == "fail"
    assert authcheck.check_spf("192.0.2.1", "soft.example", dns) == "softfail"
    assert authcheck.check_spf("192.0.2.1", "nospf.example", dns) == "none"


def test_dmarc_evaluated_in_gateway_mode():
    raw = samples.build("ceo@abelzeta.com", "Wire", text="Hello")
    dns = FakeDNS(txt={"abelzeta.com": ["v=spf1 ip4:40.92.0.0/15 -all"],
                       "_dmarc.abelzeta.com": ["v=DMARC1; p=reject; rua=mailto:d@abelzeta.com"]})
    res = authcheck.evaluate(parse_email(raw), "203.0.113.50", "ceo@abelzeta.com", dns)
    assert res.spf == "fail" and res.dmarc == "fail" and res.dmarc_policy == "reject"
    res = authcheck.evaluate(parse_email(raw), "40.92.1.1", "ceo@abelzeta.com", dns)
    assert res.spf == "pass" and res.dmarc == "pass"


# ---- links ------------------------------------------------------------------------

def test_link_text_mismatch_and_lookalike():
    f = analyze_url(Link("http://micros0ft-login.xyz/verify", "https://login.microsoftonline.com"), PROTECTED, no_intel)
    assert any("Link text shows" in r for r in f.reasons) and any("Lookalike" in r for r in f.reasons)
    assert analyze_url(Link("https://www.microsoft.com/blog", "Read the blog"), PROTECTED, no_intel).score == 0
    assert analyze_url(Link("javascript:alert(1)"), PROTECTED, no_intel).score >= 40
    assert analyze_url(Link("http://192.0.2.4/login"), PROTECTED, no_intel).score >= 25


def test_ssrf_guard():
    with pytest.raises(UnsafeDestination):
        assert_public("127.0.0.1")
    with pytest.raises(UnsafeDestination):
        assert_public("intranet.local", resolver=lambda h: ["10.0.0.5"])
    with pytest.raises(UnsafeDestination):
        assert_public("metadata", resolver=lambda h: ["169.254.169.254"])
    assert_public("example.com", resolver=lambda h: ["93.184.216.34"])


def test_inspect_page_follows_redirects_and_spots_credential_harvesting():
    pages = {
        "https://bit.ly/x": (301, {"Location": "https://cdn.example/r"}, b""),
        "https://cdn.example/r": (302, {"Location": "https://secure-portal.example/login"}, b""),
        "https://secure-portal.example/login": (200, {"Content-Type": "text/html"},
            b"<html><title>Microsoft account sign in</title><form action='https://grab.example/p'>"
            b"<input type=password></form></html>"),
    }
    insp = inspect_page("https://bit.ly/x", PROTECTED, no_intel, lambda u: pages[u], resolver=lambda h: ["93.184.216.34"])
    assert insp.final_url == "https://secure-portal.example/login" and len(insp.hops) == 3
    assert any("imitating microsoft.com" in r for r in insp.reasons)
    assert any("submits to another site" in r for r in insp.reasons)
    # a redirect into the internal network is refused
    pages["https://bit.ly/y"] = (302, {"Location": "http://10.1.2.3/admin"}, b"")
    with pytest.raises(UnsafeDestination):
        inspect_page("https://bit.ly/y", PROTECTED, no_intel, lambda u: pages[u], resolver=lambda h: ["93.184.216.34"])


def test_rewrite_html_only_web_links():
    out = rewrite_html('<a href="https://x.example/a?b=1&amp;c=2">x</a> <a href="mailto:a@b.c">m</a>',
                       lambda u: "https://pg/c/" + str(len(u)))
    assert 'href="https://pg/c/' in out and 'href="mailto:a@b.c"' in out


# ---- attachments ---------------------------------------------------------------------

def test_attachment_detection():
    assert sniff(b"MZ\x90\x00") == "pe" and sniff(b"%PDF-1.7") == "pdf"
    docm = analyze_attachment(Attachment("Invoice.docm", "application/octet-stream", samples.macro_docm()), no_intel)
    assert docm.detonate and any("VBA macros" in r for r in docm.reasons)
    z = analyze_attachment(Attachment("scan.zip", "application/zip", samples.zip_with_exe()), no_intel)
    assert z.score >= 45 and any("pdf.exe" in r for r in z.reasons)
    rtlo = analyze_attachment(Attachment("invoice‮fdp.exe", "application/octet-stream", b"MZ.."), no_intel)
    assert any("right-to-left" in r for r in rtlo.reasons)
    pdf = analyze_attachment(Attachment("doc.pdf", "application/pdf",
                                        b"%PDF-1.7 /OpenAction << /JS (app.alert(1)) /S /JavaScript >>"), no_intel)
    assert any("JavaScript" in r for r in pdf.reasons)
    clean = analyze_attachment(Attachment("notes.txt", "text/plain", b"meeting notes"), no_intel)
    assert clean.score == 0
    blocked = analyze_attachment(Attachment("x.bin", "application/octet-stream", b"abc"),
                                 lambda kind, value: "" if kind == "hash" else None)
    assert blocked.score >= 90


# ---- language ---------------------------------------------------------------------------

def test_language_bec_patterns():
    r = analyze_text("Updated bank details", "Our bank details have changed. Please process the outstanding "
                                             "invoice urgently to our new account.")
    assert {"payment_change", "urgency"} <= set(r.categories) and r.score >= 40
    assert analyze_text("Lunch", "See you at noon").score == 0


# ---- whole-message verdicts -------------------------------------------------------------

@pytest.mark.parametrize("name,verdict,category", [
    ("newsletter", "clean", "clean"),
    ("internal_ok", "clean", "clean"),
    ("credential_phish", "malicious", "phishing"),
    ("ceo_gift_cards", "malicious", "bec"),
    ("vendor_bank_change", "malicious", "bec"),
    ("macro_malware", "malicious", "malware"),
    ("zipped_exe", "malicious", "malware"),
    ("html_attachment_phish", "malicious", "phishing"),
    ("internal_spoof", "malicious", "bec"),
])
def test_sample_verdicts(name, verdict, category):
    s = Settings(db_path=tempfile.mktemp(), org_domains=["abelzeta.com"], vip_names=["john mensah"],
                 llm_enabled=False, dns_checks=False)
    with Database(s.db_path).read() as conn:
        intel = Intel(conn)
    r = Scanner(s).scan(samples.ALL[name], intel)
    assert (r.verdict, r.category) == (verdict, category), r.reasons
