"""End-to-end flows against the built-in demo directory."""
from conftest import ADMIN_PASSWORD, DEMO_PASSWORD, csrf, last_code, outbox

NEW = "Brand-New-Pass-77"


def enroll(client, app, contact="john.personal@example.org", domain="gmp", user="jdoe", password=DEMO_PASSWORD):
    r = client.post("/enroll", data=dict(csrf_token=csrf(client, "/enroll"), domain=domain, username=user,
                                         password=password))
    assert r.status_code == 200 and "Your recovery details" in r.text, r.text
    r = client.post("/enroll/contact", data=dict(csrf_token=csrf(client, "/enroll/contact"), channel="email",
                                                 contact=contact))
    assert "Enter your code" in r.text
    r = client.post("/enroll/verify", data=dict(csrf_token=csrf(client, "/enroll/contact"), code=last_code(app)))
    assert "Saved." in r.text


def test_pages_and_headers(client):
    for path in ["/", "/change", "/forgot", "/unlock", "/enroll", "/admin/login", "/health"]:
        r = client.get(path)
        assert r.status_code == 200, path
    r = client.get("/change")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert "GMP domain (demo)" in r.text and "Business" in r.text


def test_csrf_required(client):
    r = client.post("/change", data=dict(csrf_token="nope", domain="gmp", username="jdoe", current=DEMO_PASSWORD,
                                         new=NEW, confirm=NEW))
    assert r.status_code in (400, 403)


def test_change_password(client):
    r = client.post("/change", data=dict(csrf_token=csrf(client), domain="gmp", username="jdoe",
                                         current="wrong-password", new=NEW, confirm=NEW))
    assert r.status_code == 400
    r = client.post("/change", data=dict(csrf_token=csrf(client), domain="gmp", username="jdoe",
                                         current=DEMO_PASSWORD, new="short", confirm="short"))
    assert r.status_code == 400
    r = client.post("/change", data=dict(csrf_token=csrf(client), domain="gmp", username="jdoe",
                                         current=DEMO_PASSWORD, new=NEW, confirm=NEW))
    assert r.status_code == 200 and "changed" in r.text.lower()
    # old password no longer works, new one does; history blocks re-use
    r = client.post("/change", data=dict(csrf_token=csrf(client), domain="gmp", username="jdoe",
                                         current=NEW, new=DEMO_PASSWORD, confirm=DEMO_PASSWORD))
    assert r.status_code == 400


def test_domains_are_separate(client):
    """Same user name in two domains: changing one must not touch the other."""
    r = client.post("/change", data=dict(csrf_token=csrf(client), domain="business", username="jdoe",
                                         current=DEMO_PASSWORD, new=NEW, confirm=NEW))
    assert r.status_code == 200
    r = client.post("/enroll", data=dict(csrf_token=csrf(client), domain="gmp", username="jdoe",
                                         password=DEMO_PASSWORD))
    assert "Your recovery details" in r.text


def test_protected_account_refused(client):
    r = client.post("/change", data=dict(csrf_token=csrf(client), domain="gmp", username="da-admin",
                                         current=DEMO_PASSWORD, new=NEW, confirm=NEW))
    assert r.status_code == 400
    r = client.post("/enroll", data=dict(csrf_token=csrf(client), domain="gmp", username="da-admin",
                                         password=DEMO_PASSWORD))
    assert r.status_code == 400


def test_forgot_password_flow(client, app):
    enroll(client, app)
    n = len(outbox(app))
    r = client.post("/forgot", data=dict(csrf_token=csrf(client), domain="gmp", username="jdoe", channel="email",
                                         contact="John.Personal@example.org"))
    assert "Enter your code" in r.text
    assert len(outbox(app)) == n + 1
    code = last_code(app)
    r = client.post("/forgot/verify", data=dict(csrf_token=csrf(client), code="000000" if code != "000000" else "111111"))
    assert r.status_code == 400
    r = client.post("/forgot/verify", data=dict(csrf_token=csrf(client), code=code))
    assert "Choose a new password" in r.text
    r = client.post("/forgot/reset", data=dict(csrf_token=csrf(client), new=NEW, confirm=NEW))
    assert r.status_code == 200
    # the code / flow can't be replayed
    r = client.post("/forgot/reset", data=dict(csrf_token=csrf(client), new=NEW + "x", confirm=NEW + "x"))
    assert r.status_code == 400
    r = client.post("/change", data=dict(csrf_token=csrf(client), domain="gmp", username="jdoe",
                                         current=NEW, new=NEW + "-2", confirm=NEW + "-2"))
    assert r.status_code == 200


def test_unlock_flow(client, app):
    enroll(client, app)
    for _ in range(6):     # lock the account with bad passwords
        client.post("/change", data=dict(csrf_token=csrf(client), domain="gmp", username="jdoe",
                                         current="bad-guess", new=NEW, confirm=NEW))
    r = client.post("/change", data=dict(csrf_token=csrf(client), domain="gmp", username="jdoe",
                                         current=DEMO_PASSWORD, new=NEW, confirm=NEW))
    assert r.status_code == 400 and "locked" in r.text.lower()
    r = client.post("/unlock", data=dict(csrf_token=csrf(client), domain="gmp", username="jdoe", channel="email",
                                         contact="john.personal@example.org"))
    assert "Enter your code" in r.text
    r = client.post("/unlock/verify", data=dict(csrf_token=csrf(client), code=last_code(app)))
    assert r.status_code == 200 and "unlocked" in r.text.lower()
    r = client.post("/change", data=dict(csrf_token=csrf(client), domain="gmp", username="jdoe",
                                         current=DEMO_PASSWORD, new=NEW, confirm=NEW))
    assert r.status_code == 200


def test_no_enumeration(client, app):
    """Unknown user, wrong contact and not-enrolled all look identical and send nothing."""
    enroll(client, app)
    n = len(outbox(app))
    pages = []
    for user, contact in [("nobody", "x@example.org"), ("jdoe", "wrong@example.org"), ("asmith", "a@example.org")]:
        r = client.post("/forgot", data=dict(csrf_token=csrf(client), domain="gmp", username=user, channel="email",
                                             contact=contact))
        assert r.status_code == 200
        pages.append(r.text.replace(csrf(client), ""))
    assert len(set(p.split("<main>")[1] for p in pages)) == 1
    assert len(outbox(app)) == n
    # and a guessed code for such a flow never verifies
    r = client.post("/forgot/verify", data=dict(csrf_token=csrf(client), code="123456"))
    assert r.status_code == 400


def test_code_attempt_limit(client, app):
    enroll(client, app)
    client.post("/forgot", data=dict(csrf_token=csrf(client), domain="gmp", username="jdoe", channel="email",
                                      contact="john.personal@example.org"))
    code = last_code(app)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        client.post("/forgot/verify", data=dict(csrf_token=csrf(client), code=wrong))
    r = client.post("/forgot/verify", data=dict(csrf_token=csrf(client), code=code))
    assert r.status_code == 400


def test_send_throttle(client, app):
    enroll(client, app)
    n = len(outbox(app))
    for _ in range(8):
        client.post("/forgot", data=dict(csrf_token=csrf(client), domain="gmp", username="jdoe", channel="email",
                                          contact="john.personal@example.org"))
    assert len(outbox(app)) - n <= app.state.settings.sends_per_account_hour


def test_contacts_encrypted_at_rest(client, app):
    enroll(client, app, contact="secret.person@example.org")
    rows = app.state.station.db.read().execute("SELECT email_enc FROM enrollments").fetchall()
    assert rows and "secret.person" not in str(rows[0][0])


def test_admin_console(client, app):
    r = client.post("/admin/login", data=dict(csrf_token=csrf(client, "/admin/login"), username="admin",
                                              password="wrong"))
    assert r.status_code in (400, 401)
    assert client.get("/admin", follow_redirects=False).status_code in (302, 303, 401, 403)
    r = client.post("/admin/login", data=dict(csrf_token=csrf(client, "/admin/login"), username="admin",
                                              password=ADMIN_PASSWORD))
    assert r.status_code == 200 and "Domains" in r.text
    enroll(client, app)
    r = client.get("/admin")
    assert "verified" in r.text and "enroll" in r.text
    assert "jdoe" in client.get("/admin/enrollments").text
    assert client.get("/admin/audit.csv").text.startswith("id,at,")
    assert "Outbox" in client.get("/admin/outbox").text
    # add an AD domain (unreachable) and test it - must fail gracefully
    r = client.post("/admin/domain", data=dict(csrf_token=csrf(client, "/admin"), id="lab", name="Lab AD", kind="ad",
                                               hosts="127.0.0.1", port="1", base_dn="DC=lab,DC=local",
                                               bind_user="svc@lab.local", bind_password="pw", enabled="true",
                                               action="test"))
    assert r.status_code == 200 and "Connection test" in r.text
    row = app.state.station.db.read().execute("SELECT secret_enc FROM domains WHERE id='lab'").fetchone()
    assert row[0] and "pw" != row[0]
    r = client.post("/admin/enrollments/remove", data=dict(csrf_token=csrf(client, "/admin"), domain_id="gmp",
                                                           username="jdoe"), follow_redirects=True)
    assert "jdoe" not in r.text.split("<tbody>")[-1] or "None." in r.text


def test_audit_chain(client, app):
    enroll(client, app)
    ok, n = app.state.station.audit.verify()
    assert ok and n > 0
    with app.state.station.db.tx() as conn:
        conn.execute("UPDATE events SET result='ok' WHERE id=(SELECT MIN(id) FROM events)")
        conn.execute("UPDATE events SET detail='tampered' WHERE id=(SELECT MIN(id) FROM events)")
    ok, _ = app.state.station.audit.verify()
    assert not ok


def test_admin_lockout_and_reset(client, app):
    for _ in range(5):
        client.post("/admin/login", data=dict(csrf_token=csrf(client, "/admin/login"), username="admin", password="x"))
    r = client.post("/admin/login", data=dict(csrf_token=csrf(client, "/admin/login"), username="admin",
                                              password=ADMIN_PASSWORD))
    assert r.status_code == 400          # temporarily locked even with the right password
    app.state.station.reset_admin(ADMIN_PASSWORD)
    r = client.post("/admin/login", data=dict(csrf_token=csrf(client, "/admin/login"), username="admin",
                                              password=ADMIN_PASSWORD))
    assert r.status_code == 200 and "Domains" in r.text
