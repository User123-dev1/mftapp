import tempfile

from phishguard.config import Settings
from phishguard.db import Database
from phishguard.engine import Scanner
from phishguard.intel import Intel
from phishguard.llm import LLMAnalyzer
from tests import samples
from tests.fakes import FakeClaude

# Polite, typo-free BEC with no trigger words: heuristics alone barely notice it
SUBTLE_BEC = samples.build(
    "kwame.boateng@abelzeta-group.example", "Re: Q4 vendor reconciliation", display="Kwame Boateng",
    text=("Hi Ama, following our call last week, the auditors asked that the Hartwell balance be settled through "
          "the escrow account I mentioned rather than the usual one. I've attached nothing as it's all in the "
          "portal. Could you handle it before Thursday and let me know once done? I'd prefer we keep this off "
          "the general finance thread until the audit closes. Thanks, Kwame"),
    msgid="<subtle-1@abelzeta-group.example>")

BEC_VERDICT = {
    "category": "bec", "risk": 88, "confidence": "high",
    "tactics": ["payment redirection", "secrecy", "deadline"], "impersonated_entity": "Kwame Boateng (colleague)",
    "requested_action": "Pay a supplier balance to a different 'escrow' account",
    "user_summary": "This asks you to send a payment to a new account and keep it quiet - a classic payment-fraud pattern.",
    "analyst_notes": "External lookalike group domain; redirection of payment; secrecy request; no prior context.",
}


def scanner(client, **kw):
    s = Settings(db_path=tempfile.mktemp(), org_domains=["abelzeta.com"], llm_enabled=True, dns_checks=False, **kw)
    with Database(s.db_path).read() as conn:
        intel = Intel(conn)
    return Scanner(s, LLMAnalyzer(s.llm_model, s.llm_max_chars, client=client)), intel


def test_ai_catches_subtle_bec_and_request_is_well_formed():
    client = FakeClaude(BEC_VERDICT)
    sc, intel = scanner(client)
    heuristics_only = Scanner(sc.settings).scan(SUBTLE_BEC, intel)
    r = sc.scan(SUBTLE_BEC, intel)
    assert heuristics_only.score < r.score and r.verdict == "malicious" and r.category == "bec"
    assert r.summary.startswith("This asks you to send a payment")
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in call["betas"]
    # the e-mail is passed as untrusted data, never in the system prompt
    assert "<email>" in call["messages"][0]["content"] and "untrusted" in call["system"]


def test_ai_clean_verdict_softens_weak_signals_but_not_hard_evidence():
    clean = {**BEC_VERDICT, "category": "marketing", "risk": 5, "confidence": "high", "user_summary": "Newsletter."}
    sc, intel = scanner(FakeClaude(clean))
    weak = samples.build("offers@shop.example", "Sale ends today - act now", text="Final reminder: sale ends today.")
    assert sc.scan(weak, intel).score < Scanner(sc.settings).scan(weak, intel).score
    # a lookalike sender stays malicious whatever the AI says
    assert sc.scan(samples.CREDENTIAL_PHISH, intel).verdict == "malicious"


def test_refusal_or_error_falls_back_to_heuristics():
    sc, intel = scanner(FakeClaude(None, stop_reason="refusal"), vip_names=["john mensah"])
    r = sc.scan(samples.CEO_GIFT_CARDS, intel)
    assert r.llm is None and r.verdict == "malicious"


def test_internal_authenticated_mail_is_not_sent_to_ai():
    client = FakeClaude(BEC_VERDICT)
    sc, intel = scanner(client)
    sc.scan(samples.INTERNAL_OK, intel)
    assert client.calls == []
