import re
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app import abelorder, database
from app.config import settings
from app.models import (
    Department, EmailLog, PRStatus, PurchaseRequest, Role, StepState, User, Vendor,
)
from app.security import hash_password

PW = "Password1!"
PROCUREMENT = "abztprocurement@abelzeta.com"


def make_user(email, name, role=Role.REQUESTER, **kw):
    with database.SessionLocal() as db:
        u = User(email=email, full_name=name, role=role, is_active=True, password_hash=hash_password(PW), **kw)
        db.add(u)
        db.commit()
        return u.id


def org(client, manager):
    """Employee Emma -> supervisor Sam; Ops dept head Hana; CSO Carl; Procurement Pat; vendor Acme."""
    ids = {
        "sam": make_user("sam@abelzeta.com", "Sam Supervisor"),
        "hana": make_user("hana@abelzeta.com", "Hana Head"),
        "carl": make_user("carl@abelzeta.com", "Carl CSO"),
        "pat": make_user("pat@abelzeta.com", "Pat Procurement", is_procurement=True),
        "emma": make_user("emma@abelzeta.com", "Emma Employee"),
        "olly": make_user("olly@abelzeta.com", "Olly Other"),
    }
    with database.SessionLocal() as db:
        dept = Department(name="Operations", head_id=ids["hana"])
        db.add(dept)
        db.add(Vendor(name="Acme Supply", contact_name="Al Acme", contact_email="al@acme.com"))
        db.commit()
        ids["dept"] = dept.id
        ids["acme"] = db.scalar(select(Vendor)).id
    manager.post("/admin/abelorder/policy", {"procurement_email": PROCUREMENT, "dept_head_threshold": "10000",
                                             "cso_threshold": "50000", "cso_user_id": str(ids["carl"])})
    b = {k: client().login(f"{k}@abelzeta.com", PW) for k in ("sam", "hana", "carl", "pat", "emma", "olly")}
    return ids, b


def form(ids, items, **over):
    data = {"justification": "Replace failed pump", "urgency": "urgent", "project_number": "WO-4471",
            "department_id": str(ids["dept"]), "supervisor_id": str(ids["sam"]), "vendor_mode": "existing",
            "vendor_id": str(ids["acme"]), "vendor_contact_name": "Al Acme", "vendor_contact_email": "al@acme.com",
            "notes": "Deliver to dock 3", "action": "submit"}
    data.update(over)
    data["item_qty"] = [i[0] for i in items]
    data["item_uom"] = [i[1] for i in items]
    data["item_desc"] = [i[2] for i in items]
    data["item_catalog"] = [i[3] for i in items]
    data["item_unit_cost"] = [i[4] for i in items]
    return data


def post_request(browser, data, files=None):
    data = dict(data, csrf_token=browser.csrf())
    return browser.client.post("/abelorder/new", data=data, files=files or {})


def latest():
    with database.SessionLocal() as db:
        pr = db.scalars(select(PurchaseRequest).order_by(PurchaseRequest.id.desc())).first()
        if pr is not None:
            [(a.approver.full_name, a.state) for a in pr.approvals]
            [i.line_total for i in pr.items]
        return pr


def route(pr):
    return [(a.role.value, a.approver.full_name, a.state.value) for a in pr.approvals]


def emails(to):
    with database.SessionLocal() as db:
        return db.scalars(select(EmailLog).where(EmailLog.to_address == to).order_by(EmailLog.id)).all()


ONE_ITEM = [("2.5", "EA", "Pump seal kit", "RMS-100", "19.99")]


def test_small_order_supervisor_only_then_procurement_and_requester_get_pdf(client, manager):
    ids, b = org(client, manager)
    quote = ("quote.pdf", b"%PDF-1.4 fake quote", "application/pdf")
    r = post_request(b["emma"], form(ids, ONE_ITEM + [("3", "BX", "Gloves", "", "12.50")]), files=[("quotes", quote)])
    assert r.status_code == 200, r.text
    pr = latest()
    assert pr.number.startswith("AO-") and pr.status == PRStatus.PENDING_APPROVAL
    assert pr.requester_name == "Emma Employee"
    # 2.5 x 19.99 = 49.975 -> 49.98 ; 3 x 12.50 = 37.50 ; total 87.48 (server-side maths)
    assert [i.line_total for i in pr.items] == ["49.98", "37.50"] and pr.total == "87.48"
    assert route(pr) == [("supervisor", "Sam Supervisor", "pending")]
    assert any("Approval needed" in e.subject for e in emails("sam@abelzeta.com"))
    assert "1" in b["sam"].get("/abelorder?view=approvals").text

    # Others can't see it; Emma can't approve her own.
    assert b["olly"].get(f"/abelorder/{pr.number}").status_code == 403
    r = b["emma"].post(f"/abelorder/{pr.number}/decide", {"decision": "approve"})
    assert "not waiting for your approval" in r.text

    b["sam"].post(f"/abelorder/{pr.number}/decide", {"decision": "approve", "comment": "OK"})
    pr = latest()
    assert pr.status == PRStatus.APPROVED and route(pr) == [("supervisor", "Sam Supervisor", "approved")]

    proc = [e for e in emails(PROCUREMENT) if "Approved purchase request" in e.subject]
    assert len(proc) == 1 and "$87.48" in proc[0].subject
    names = [a["filename"] for a in proc[0].attachment_list]
    assert names == [f"{pr.number}.pdf", "quote.pdf"]
    pdf_path = Path(proc[0].attachment_list[0]["path"])
    assert pdf_path.read_bytes().startswith(b"%PDF")
    mine = [e for e in emails("emma@abelzeta.com") if "approved" in e.subject.lower()]
    assert mine and [a["filename"] for a in mine[0].attachment_list] == [f"{pr.number}.pdf"]

    # PDF download works for the requester.
    r = b["emma"].get(f"/abelorder/{pr.number}/pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")


def test_thresholds_route_to_department_head_and_cso(client, manager):
    ids, b = org(client, manager)
    post_request(b["emma"], form(ids, [("1", "EA", "Exactly 10k", "", "10000")]))
    assert route(latest()) == [("supervisor", "Sam Supervisor", "pending")]

    post_request(b["emma"], form(ids, [("1", "EA", "Just over", "", "10000.01")]))
    assert [r[0] for r in route(latest())] == ["supervisor", "dept_head"]

    post_request(b["emma"], form(ids, [("1", "EA", "Exactly 50k", "", "50000")]))
    assert [r[0] for r in route(latest())] == ["supervisor", "dept_head"]

    post_request(b["emma"], form(ids, [("2", "EA", "Big", "", "25000"), ("1", "EA", "Small", "", "0.01")]))
    pr = latest()
    assert route(pr) == [("supervisor", "Sam Supervisor", "pending"), ("dept_head", "Hana Head", "waiting"),
                         ("cso", "Carl CSO", "waiting")]

    # Sequential: CSO can't sign before the others.
    r = b["carl"].post(f"/abelorder/{pr.number}/decide", {"decision": "approve"})
    assert "not waiting for your approval" in r.text
    b["sam"].post(f"/abelorder/{pr.number}/decide", {"decision": "approve"})
    assert route(latest())[1][2] == "pending"
    assert any("Approval needed" in e.subject for e in emails("hana@abelzeta.com"))
    b["hana"].post(f"/abelorder/{pr.number}/decide", {"decision": "approve"})
    assert latest().status == PRStatus.PENDING_APPROVAL
    assert not emails(PROCUREMENT)  # nothing sent until every signature is in
    b["carl"].post(f"/abelorder/{pr.number}/decide", {"decision": "approve"})
    pr = latest()
    assert pr.status == PRStatus.APPROVED and all(a.state == StepState.APPROVED for a in pr.approvals)
    assert len(emails(PROCUREMENT)) == 1


def test_same_person_signs_both_roles_and_self_approval_escalates(client, manager):
    ids, b = org(client, manager)
    # Hana is both Emma's supervisor and department head -> one click signs both.
    post_request(b["emma"], form(ids, [("1", "EA", "Server", "", "20000")], supervisor_id=str(ids["hana"])))
    pr = latest()
    b["hana"].post(f"/abelorder/{pr.number}/decide", {"decision": "approve"})
    pr = latest()
    assert pr.status == PRStatus.APPROVED and [a.state for a in pr.approvals] == [StepState.APPROVED] * 2

    # Hana (a department head) requests 20k: her dept-head step escalates to the CSO.
    post_request(b["hana"], form(ids, [("1", "EA", "Lab kit", "", "20000")], supervisor_id=str(ids["sam"])))
    pr = latest()
    assert route(pr) == [("supervisor", "Sam Supervisor", "pending"), ("dept_head", "Carl CSO", "waiting")]
    assert "escalated to CSO" in pr.approvals[1].note

    # The CSO can't self-approve a >50k request.
    r = post_request(b["carl"], form(ids, [("1", "EA", "Fleet", "", "60000")], supervisor_id=str(ids["sam"])))
    assert r.status_code == 400 and "highest approval role" in r.text


def test_reject_revise_resubmit(client, manager):
    ids, b = org(client, manager)
    post_request(b["emma"], form(ids, ONE_ITEM))
    pr = latest()
    r = b["sam"].post(f"/abelorder/{pr.number}/decide", {"decision": "reject"})
    assert "give a reason" in r.text
    b["sam"].post(f"/abelorder/{pr.number}/decide", {"decision": "reject", "comment": "Get a second quote"})
    assert latest().status == PRStatus.REJECTED
    assert any("Get a second quote" in e.body for e in emails("emma@abelzeta.com"))
    b["emma"].post(f"/abelorder/{pr.number}/revise")
    assert latest().status == PRStatus.DRAFT and latest().approvals == []
    page = b["emma"].get(f"/abelorder/{pr.number}/edit").text
    assert "Pump seal kit" in page
    data = form(ids, [("4", "EA", "Pump seal kit", "RMS-100", "18.00")])
    b["emma"].client.post(f"/abelorder/{pr.number}/edit", data=dict(data, csrf_token=b["emma"].csrf()))
    pr = latest()
    assert pr.status == PRStatus.PENDING_APPROVAL and pr.total == "72.00"
    assert route(pr) == [("supervisor", "Sam Supervisor", "pending")]


def test_validation_and_drafts(client, manager):
    ids, b = org(client, manager)
    bad = form(ids, [("abc", "EA", "Thing", "", "5"), ("1", "EA", "", "", "5"), ("1", "EA", "X", "", "1.23456")],
               justification="", project_number="", vendor_contact_email="not-an-email")
    r = post_request(b["emma"], bad)
    assert r.status_code == 400
    for msg in ("Line 1: quantity &#39;abc&#39; is not a number", "Line 2: description is required",
                "at most 4 decimal places", "justification is required", "work order number is required",
                "valid vendor contact email"):
        assert msg in r.text, msg
    assert latest() is None
    # The entered rows come back (escaped) so nothing is lost.
    assert '"desc": "Thing"' in r.text

    # Drafts may be incomplete.
    r = post_request(b["emma"], form(ids, [("1", "EA", "Thing", "", "5")], action="save", justification=""))
    pr = latest()
    assert pr.status == PRStatus.DRAFT
    r = b["emma"].post(f"/abelorder/{pr.number}/submit")
    assert "justification is required" in r.text and latest().status == PRStatus.DRAFT

    # New vendor whose name already exists -> told to pick it.
    r = post_request(b["emma"], form(ids, ONE_ITEM, vendor_mode="new", vendor_name="ACME supply", vendor_id=""))
    assert "already an existing vendor" in r.text


def test_script_in_description_is_escaped(client, manager):
    ids, b = org(client, manager)
    evil = "</script><script>alert(1)</script>"
    r = post_request(b["emma"], form(ids, [("x", "EA", evil, "", "1")]))
    assert r.status_code == 400 and "<script>alert(1)" not in r.text


def test_new_vendor_added_after_approval(client, manager):
    ids, b = org(client, manager)
    post_request(b["emma"], form(ids, ONE_ITEM, vendor_mode="new", vendor_id="", vendor_name="Brand New Parts",
                                 vendor_contact_name="Nia", vendor_contact_email="nia@bnp.com"))
    pr = latest()
    assert pr.vendor_is_new
    b["sam"].post(f"/abelorder/{pr.number}/decide", {"decision": "approve"})
    with database.SessionLocal() as db:
        v = db.scalar(select(Vendor).where(Vendor.name == "Brand New Parts"))
        assert v is not None and v.contact_email == "nia@bnp.com"


def test_profile_supervisor_overrides_form(client, manager):
    ids, b = org(client, manager)
    manager.post(f"/admin/abelorder/people/{ids['emma']}", {"supervisor_id": str(ids["hana"]),
                                                            "department_id": str(ids["dept"])})
    assert "Set by your administrator" in b["emma"].get("/abelorder/new").text
    post_request(b["emma"], form(ids, ONE_ITEM, supervisor_id=str(ids["olly"])))
    assert route(latest())[0][1] == "Hana Head"


def test_missing_cso_and_department_head(client, manager):
    ids, b = org(client, manager)
    manager.post("/admin/abelorder/policy", {"procurement_email": PROCUREMENT, "dept_head_threshold": "10000",
                                             "cso_threshold": "50000", "cso_user_id": ""})
    r = post_request(b["emma"], form(ids, [("1", "EA", "Big", "", "60000")]),
                     files=[("quotes", ("q.pdf", b"%PDF", "application/pdf"))])
    assert "no CSO is configured" in r.text
    # The upload from the failed submission doesn't linger on disk.
    assert not [p for p in (Path(settings.upload_dir) / "abelorder").glob("*.pdf")]
    r = post_request(b["emma"], form(ids, [("1", "EA", "Mid", "", "20000")], department_id=""))
    assert "need a department head" in r.text


def test_po_number_issue(client, manager):
    ids, b = org(client, manager)
    post_request(b["emma"], form(ids, ONE_ITEM))
    pr = latest()
    r = b["pat"].post(f"/abelorder/{pr.number}/po", {"po_number": "PO-1"})
    assert "only be added after the request is fully approved" in r.text
    b["sam"].post(f"/abelorder/{pr.number}/decide", {"decision": "approve"})
    assert pr.number in b["pat"].get("/abelorder?view=procurement").text
    r = b["emma"].post(f"/abelorder/{pr.number}/po", {"po_number": "PO-1"})
    assert "Only Procurement" in r.text
    b["pat"].post(f"/abelorder/{pr.number}/po", {"po_number": "PO-2026-0001"})
    pr = latest()
    assert pr.status == PRStatus.PO_ISSUED and pr.po_number == "PO-2026-0001"
    issued = [e for e in emails("emma@abelzeta.com") if "PO PO-2026-0001 issued" in e.subject]
    assert issued and issued[0].attachment_list[0]["filename"] == f"{pr.number}-PO-PO-2026-0001.pdf"

    post_request(b["emma"], form(ids, ONE_ITEM))
    second = latest()
    b["sam"].post(f"/abelorder/{second.number}/decide", {"decision": "approve"})
    r = b["pat"].post(f"/abelorder/{second.number}/po", {"po_number": "PO-2026-0001"})
    assert "already used" in r.text


def test_attachments_rules(client, manager):
    ids, b = org(client, manager)
    r = post_request(b["emma"], form(ids, ONE_ITEM), files=[("quotes", ("run.exe", b"MZ", "application/octet-stream"))])
    assert r.status_code == 400 and "file type not allowed" in r.text and latest() is None
    post_request(b["emma"], form(ids, ONE_ITEM, action="save"), files=[("quotes", ("q.xlsx", b"data", "x"))])
    pr = latest()
    with database.SessionLocal() as db:
        att = db.scalars(select(PurchaseRequest).where(PurchaseRequest.id == pr.id)).one().attachments[0]
        att_id = att.id
    url = f"/abelorder/{pr.number}/attachments/{att_id}"
    assert b["emma"].get(url).content == b"data"
    assert b["olly"].get(url).status_code == 403
    assert b["sam"].get(url).status_code == 403  # not submitted yet, so not visible to the approver
    b["emma"].post(f"/abelorder/{pr.number}/submit")
    assert b["sam"].get(url).status_code == 200  # now it's with Sam
    assert b["pat"].get(url).status_code == 200  # procurement sees everything


def test_cancel_after_approval_tells_procurement(client, manager):
    ids, b = org(client, manager)
    post_request(b["emma"], form(ids, ONE_ITEM))
    pr = latest()
    b["sam"].post(f"/abelorder/{pr.number}/decide", {"decision": "approve"})
    b["emma"].post(f"/abelorder/{pr.number}/cancel", {"reason": "Found in stock"})
    assert latest().status == PRStatus.CANCELLED
    assert any("CANCELLED - do not issue a PO" in e.subject for e in emails(PROCUREMENT))


def test_smtp_sends_attachments(client, manager, monkeypatch):
    from app import emailer

    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self, context=None): pass
        def login(self, *a): pass
        def send_message(self, msg): sent.append(msg)

    ids, b = org(client, manager)
    monkeypatch.setattr(emailer.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    post_request(b["emma"], form(ids, ONE_ITEM), files=[("quotes", ("quote.pdf", b"%PDF-q", "application/pdf"))])
    b["sam"].post(f"/abelorder/{latest().number}/decide", {"decision": "approve"})
    proc = [m for m in sent if m["To"] == PROCUREMENT]
    assert proc
    files = [(p.get_filename(), p.get_content_type()) for p in proc[0].iter_attachments()]
    assert files == [(f"{latest().number}.pdf", "application/pdf"), ("quote.pdf", "application/pdf")]


def test_settings_access(client, manager):
    ids, b = org(client, manager)
    assert b["emma"].get("/admin/abelorder").status_code == 403
    page = b["pat"].get("/admin/abelorder").text
    assert "Vendors" in page and "Procurement email" not in page  # vendors only
    assert b["pat"].post("/admin/abelorder/policy", {"procurement_email": "x@y.com", "dept_head_threshold": "1",
                                                     "cso_threshold": "2"}).status_code == 403
    r = manager.post("/admin/abelorder/policy", {"procurement_email": PROCUREMENT, "dept_head_threshold": "50000",
                                                 "cso_threshold": "10000"})
    assert "must be higher" in r.text
