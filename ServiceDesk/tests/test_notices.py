from datetime import datetime, timedelta

from sqlalchemy import select

from app import database
from app.models import DistributionList, EmailLog, NoticeStatus, ServiceNotice, User

from .test_cmdb import upload
from .test_workflow import make_agent, register


def emails(to):
    with database.SessionLocal() as db:
        return db.scalars(select(EmailLog).where(EmailLog.to_address == to).order_by(EmailLog.id)).all()


def latest():
    with database.SessionLocal() as db:
        n = db.scalars(select(ServiceNotice).order_by(ServiceNotice.id.desc())).first()
        if n:
            [dl.email for dl in n.lists], [u.kind for u in n.updates]
        return n


def add_lists(manager):
    manager.post("/admin/mail/lists", {"name": "All Staff", "email": "allstaff@abelzeta.com", "is_default": "true",
                                       "active": "true"})
    manager.post("/admin/mail/lists", {"name": "Plant Ops", "email": "plantops@abelzeta.com", "active": "true"})
    with database.SessionLocal() as db:
        return {dl.name: str(dl.id) for dl in db.scalars(select(DistributionList))}


def draft(browser, lists, **over):
    data = {"kind": "maintenance", "severity": "warning", "title": "Email unavailable Saturday 8-10pm",
            "message": "Exchange is being upgraded.", "impact": "Outlook and webmail.",
            "user_action": "Save drafts before 8pm.", "start_at": "2026-11-07T20:00", "end_at": "2026-11-07T22:00",
            "show_banner": "true", "list_ids": lists}
    data.update(over)
    return browser.client.post("/notices/new", data=dict(data, csrf_token=browser.csrf()))


def test_mail_setup_lists_and_test_email(client, manager):
    lists = add_lists(manager)
    assert set(lists) == {"All Staff", "Plant Ops"}
    r = manager.post("/admin/mail/lists", {"name": "Dup", "email": "ALLSTAFF@abelzeta.com", "active": "true"})
    assert "already uses" in r.text
    r = manager.post("/admin/mail/lists", {"name": "Bad", "email": "nope", "active": "true"})
    assert "valid list email" in r.text
    page = manager.get("/admin/mail").text
    assert "Not configured" in page and "allstaff@abelzeta.com" in page
    r = manager.post("/admin/mail/test", {"to": "me@abelzeta.com"})
    assert "Test email queued" in r.text and emails("me@abelzeta.com")[0].status == "logged"
    make_agent(manager)
    agent = client().login("agent@example.com", "Password1!")
    assert agent.get("/admin/mail").status_code == 403
    # Default list is pre-selected on a new notice.
    page = agent.get("/notices/new").text
    assert f'value="{lists["All Staff"]}" checked' in page and f'value="{lists["Plant Ops"]}" checked' not in page


def test_full_notice_lifecycle_and_banner(client, manager):
    lists = add_lists(manager)
    make_agent(manager)
    agent = client().login("agent@example.com", "Password1!")
    rita = register(client, "Rita", "rita@example.com")

    r = draft(agent, [lists["All Staff"], lists["Plant Ops"]], extra_recipients="ceo@abelzeta.com; bad-address")
    assert r.status_code == 400 and "Not valid email addresses: bad-address" in r.text
    draft(agent, [lists["All Staff"], lists["Plant Ops"]], extra_recipients="ceo@abelzeta.com")
    n = latest()
    assert n.status == NoticeStatus.DRAFT and n.number.startswith("NTC")
    page = agent.get(f"/notices/{n.number}").text
    assert "[PLANNED MAINTENANCE] Email unavailable Saturday 8-10pm" in page
    assert "Recipients (3)" in page and "must send it" in page  # agent can't send by default

    # Agent can't send; manager must confirm.
    r = agent.post(f"/notices/{n.number}/send", {"confirm": "true"})
    assert "Only IT managers" in r.text and latest().status == NoticeStatus.DRAFT
    r = manager.post(f"/notices/{n.number}/send", {})
    assert "Tick the confirmation box" in r.text
    r = manager.post(f"/notices/{n.number}/send", {"confirm": "true"})
    assert "sent to 3 address(es)" in r.text
    for addr in ("allstaff@abelzeta.com", "plantops@abelzeta.com", "ceo@abelzeta.com"):
        m = emails(addr)
        assert len(m) == 1 and m[0].subject == "[PLANNED MAINTENANCE] Email unavailable Saturday 8-10pm"
        assert "Save drafts before 8pm." in m[0].body and "/status" in m[0].body
    # Banner for everyone (until the window ends) + status page.
    assert "Email unavailable Saturday 8-10pm" in rita.get("/").text
    status = rita.get("/status").text
    assert "Upcoming planned work" in status and "Exchange is being upgraded." in status

    # Allow the agent to send, then post an update and complete it.
    with database.SessionLocal() as db:
        agent_id = db.scalar(select(User).where(User.email == "agent@example.com")).id
    manager.post(f"/admin/users/{agent_id}", {"role": "agent", "is_active": "true", "can_send_notices": "true"})
    agent.post(f"/notices/{n.number}/update", {"message": "Starting 15 minutes late."})
    assert emails("allstaff@abelzeta.com")[-1].subject == "[PLANNED MAINTENANCE - UPDATE 1] Email unavailable Saturday 8-10pm"
    agent.post(f"/notices/{n.number}/resolve", {"message": "Upgrade finished at 9:40pm."})
    assert latest().status == NoticeStatus.RESOLVED
    assert emails("plantops@abelzeta.com")[-1].subject == "[COMPLETED] Email unavailable Saturday 8-10pm"
    assert "notice-banner" not in rita.get("/").text
    assert "Recently resolved" in rita.get("/status").text
    assert "All services are running normally" in rita.get("/status").text


def test_outage_from_ticket_and_cancel(client, manager):
    lists = add_lists(manager)
    rita = register(client, "Rita", "rita@example.com")
    upload(manager, "name,type,assigned_to_email\nVPN,service,\n")
    rita.post("/tickets/new", {"category": "incident", "priority": "high", "title": "VPN down", "description": "x"})
    with database.SessionLocal() as db:
        from app.models import Ticket, ConfigItem
        t = db.scalars(select(Ticket)).one()
        t.ci_id = db.scalar(select(ConfigItem)).id
        db.commit()
        number = t.number
    page = manager.get(f"/tickets/{number}").text
    assert f"/notices/new?ticket={number}" in page
    form = manager.get(f"/notices/new?ticket={number}").text
    assert "VPN unavailable" in form and 'value="outage" selected' in form

    draft(manager, [lists["All Staff"]], kind="outage", severity="critical", title="VPN unavailable",
          start_at="", end_at="", ticket=number)
    n = latest()
    assert n.ticket_id is not None
    manager.post(f"/notices/{n.number}/send", {"confirm": "true"})
    assert emails("allstaff@abelzeta.com")[0].subject == "[OUTAGE] VPN unavailable"
    assert "sev-banner-critical" in rita.get("/").text
    r = manager.post(f"/notices/{n.number}/cancel", {"message": ""})
    assert "Say why" in r.text
    manager.post(f"/notices/{n.number}/cancel", {"message": "False alarm - VPN was fine."})
    assert latest().status == NoticeStatus.CANCELLED
    assert emails("allstaff@abelzeta.com")[-1].subject == "[CANCELLED] VPN unavailable"


def test_notice_from_change_prefills(client, manager):
    from .test_changes import raise_change, setup, change
    rita, alice, bob = setup(client, manager)
    raise_change(bob)
    c = change()
    assert f"/notices/new?change={c.number}" in manager.get(f"/changes/{c.number}").text
    form = manager.get(f"/notices/new?change={c.number}").text
    assert "2026-11-05T19:00" in form and "2026-11-05T20:00" in form and 'value="maintenance" selected' in form


def test_permissions_and_validation(client, manager):
    lists = add_lists(manager)
    rita = register(client, "Rita", "rita@example.com")
    assert rita.get("/notices").status_code == 403
    assert rita.get("/status").status_code == 200
    r = draft(manager, [], extra_recipients="")
    n = latest()
    r = manager.post(f"/notices/{n.number}/send", {"confirm": "true"})
    assert "Choose at least one distribution list" in r.text
    r = draft(manager, [lists["All Staff"]], start_at="2026-11-07T22:00", end_at="2026-11-07T20:00")
    assert "end time must be after" in r.text
    # Ended windows drop off the banner even if nobody resolved the notice.
    draft(manager, [lists["All Staff"]], title="Old work", start_at="2020-01-01T20:00", end_at="2020-01-01T21:00")
    old = latest()
    manager.post(f"/notices/{old.number}/send", {"confirm": "true"})
    assert "notice-banner" not in rita.get("/tickets").text
