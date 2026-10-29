"""Permission matrix: every page, opened by every kind of user.

A = allowed (200), D = denied (403), L = sent to the login page.
"""

import pytest
from sqlalchemy import select

from app import database
from app.models import Role, Ticket, User
from app.security import hash_password

from .test_abelorder import ONE_ITEM, form, latest as latest_order, org, post_request

PW = "Password1!"

# path:                         anon requester agent manager procurement(requester role)
MATRIX = {
    "/status":                  "L A A A A",
    "/tickets/new":             "L A A A A",
    "/tickets":                 "L A A A A",   # requesters only get their own (checked below)
    "/abelorder":               "L A A A A",
    "/abelorder/new":           "L A A A A",
    "/profile":                 "L A A A A",
    "/dashboard":               "L D A A D",
    "/reports":                 "L D A A D",
    "/cmdb":                    "L D A A D",
    "/cmdb/new":                "L D A A D",
    "/cmdb/export.csv":         "L D A A D",
    "/changes":                 "L D A A D",
    "/changes/new":             "L D A A D",
    "/notices":                 "L D A A D",
    "/notices/new":             "L D A A D",
    "/admin/abelorder":         "L D D A A",   # procurement: vendor list only
    "/cmdb/import":             "L D D A D",
    "/cmdb/sync":               "L D D A D",
    "/admin/users":             "L D D A D",
    "/admin/groups":            "L D D A D",
    "/admin/change-settings":   "L D D A D",
    "/admin/mail":              "L D D A D",
    "/admin/emails":            "L D D A D",
}
WHO = ["anon", "requester", "agent", "manager", "procurement"]


@pytest.fixture()
def people(client, manager):
    def make(email, role, **kw):
        with database.SessionLocal() as db:
            db.add(User(email=email, full_name=email.split("@")[0], role=role, is_active=True,
                        password_hash=hash_password(PW), **kw))
            db.commit()
        return client().login(email, PW)
    return {
        "anon": client(),
        "requester": make("req@example.com", Role.REQUESTER),
        "agent": make("agent@example.com", Role.AGENT),
        "manager": manager,
        "procurement": make("buyer@example.com", Role.REQUESTER, is_procurement=True),
    }


@pytest.mark.parametrize("path", list(MATRIX))
def test_page_access(people, path):
    expected = dict(zip(WHO, MATRIX[path].split()))
    for who, want in expected.items():
        r = people[who].get(path, follow_redirects=False)
        got = {200: "A", 403: "D", 303: "L"}.get(r.status_code, str(r.status_code))
        if got == "L":
            assert r.headers["location"].startswith("/login"), (who, path)
        assert got == want, f"{who} on {path}: expected {want}, got {got}"


def test_requesters_only_see_their_own_tickets(client, people):
    people["requester"].post("/tickets/new", {"category": "general", "priority": "low", "title": "Mine",
                                              "description": "x"})
    other = client()
    other.post("/register", {"full_name": "Other", "email": "other@example.com", "password": PW})
    other.post("/tickets/new", {"category": "general", "priority": "low", "title": "Not yours", "description": "y"})
    with database.SessionLocal() as db:
        theirs = db.scalar(select(Ticket).where(Ticket.title == "Not yours")).number
    listing = people["requester"].get("/tickets?status=all").text
    assert "Mine" in listing and "Not yours" not in listing
    assert people["requester"].get(f"/tickets/{theirs}").status_code == 403
    assert people["agent"].get(f"/tickets/{theirs}").status_code == 200
    # Internal work notes stay hidden from the requester.
    people["manager"].post(f"/tickets/{theirs}/comment", {"body": "internal only", "internal": "true"})
    assert "internal only" not in other.get(f"/tickets/{theirs}").text


def test_purchase_requests_visible_only_to_people_involved(client, manager):
    ids, b = org(client, manager)
    post_request(b["emma"], form(ids, ONE_ITEM))
    number = latest_order().number
    assert b["emma"].get(f"/abelorder/{number}").status_code == 200     # requester
    assert b["sam"].get(f"/abelorder/{number}").status_code == 200      # on the signature route
    assert b["pat"].get(f"/abelorder/{number}").status_code == 200      # procurement
    assert manager.get(f"/abelorder/{number}").status_code == 200       # IT manager
    assert b["olly"].get(f"/abelorder/{number}").status_code == 403     # anyone else
    assert b["hana"].get(f"/abelorder/{number}").status_code == 403     # not on this route (< $10k)
    agent = client()
    with database.SessionLocal() as db:
        db.add(User(email="it.agent@example.com", full_name="IT Agent", role=Role.AGENT, is_active=True,
                    password_hash=hash_password(PW)))
        db.commit()
    assert agent.login("it.agent@example.com", PW).get(f"/abelorder/{number}").status_code == 403
