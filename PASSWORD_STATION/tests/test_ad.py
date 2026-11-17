"""The Active Directory connector against ldap3's offline mock server."""
import pytest
from ldap3 import MOCK_SYNC, OFFLINE_AD_2012_R2, Connection, Server

from pstation.directory import ADDirectory, DirectoryError, DomainConfig, _escape

BASE = "DC=gmp,DC=local"
SVC = "CN=svc-pwstation,OU=Service,DC=gmp,DC=local"


@pytest.fixture
def ad():
    server = Server("dc1.gmp.local", get_info=OFFLINE_AD_2012_R2)
    seed = Connection(server, user=SVC, password="svc-pass", client_strategy=MOCK_SYNC)
    seed.strategy.add_entry(SVC, {"objectClass": ["top", "person", "user"], "userPassword": "svc-pass",
                                  "sAMAccountName": "svc-pwstation"})

    def user(cn, sam, **attrs):
        dn = f"CN={cn},OU=Staff,{BASE}"
        base = {"objectClass": ["top", "person", "organizationalPerson", "user"], "objectCategory": "person",
                "sAMAccountName": sam, "userPrincipalName": f"{sam}@gmp.local", "displayName": cn,
                "userAccountControl": 512, "userPassword": "User-Pass-2026", "lockoutTime": 0}
        base.update(attrs)
        seed.strategy.add_entry(dn, base)
        return dn

    jdoe = user("John Doe", "jdoe", mail="john.doe@abelzeta.com", lockoutTime=133000000000000000,
                memberOf=[f"CN=PS-Users,OU=Groups,{BASE}"])
    user("Disabled Guy", "dguy", userAccountControl=514)
    user("Admin Person", "aperson", adminCount=1)
    user("Ops Admin", "opsadmin", memberOf=[f"CN=Domain Admins,CN=Users,{BASE}"])
    user("Tier Zero", "tier0", memberOf=[f"CN=Tier0,OU=Groups,{BASE}"])

    cfg = DomainConfig(id="gmp", name="GMP", kind="ad", secret="svc-pass",
                       settings={"hosts": ["dc1.gmp.local"], "base_dn": BASE, "bind_user": SVC,
                                 "protected_groups": [f"CN=Tier0,OU=Groups,{BASE}"]})

    def factory(u, p):
        # Real AD accepts a UPN for a simple bind; the ldap3 mock only knows DNs.
        u = {"jdoe@gmp.local": jdoe}.get(u, u)
        return Connection(server, user=u, password=p, client_strategy=MOCK_SYNC)

    d = ADDirectory(cfg, connection_factory=factory)
    d.server, d.jdoe_dn = server, jdoe
    return d


def test_find_and_flags(ad):
    u = ad.find("jdoe")
    assert u.key == ad.jdoe_dn and u.email == "john.doe@abelzeta.com" and u.locked and not u.protected
    assert ad.find("GMP\\jdoe").key == ad.jdoe_dn
    assert ad.find("nobody") is None
    assert ad.find("dguy").disabled
    assert ad.find("aperson").protected        # adminCount=1
    assert ad.find("opsadmin").protected       # Domain Admins
    assert ad.find("tier0").protected          # configured protected group
    assert ad.find("*") is None                # wildcard is escaped, not a match-all


def test_verify_password(ad):
    u = ad.find("jdoe")
    assert ad.verify_password(u, "User-Pass-2026")
    assert not ad.verify_password(u, "wrong")
    assert not ad.verify_password(u, "")


def test_unlock_and_reset(ad):
    u = ad.find("jdoe")
    ad.unlock(u)
    assert not ad.find("jdoe").locked
    ad.reset_password(u, "Reset-Pass-2026!")
    conn = Connection(ad.server, user=SVC, password="svc-pass", client_strategy=MOCK_SYNC)
    conn.bind()
    conn.search(BASE, "(sAMAccountName=jdoe)", attributes=["unicodePwd"])
    assert conn.entries[0].entry_attributes_as_dict["unicodePwd"]


def test_bad_service_password():
    server = Server("dc1", get_info=OFFLINE_AD_2012_R2)
    cfg = DomainConfig(id="x", name="x", kind="ad", secret="wrong", settings={"hosts": ["dc1"], "base_dn": BASE,
                                                                              "bind_user": SVC})
    d = ADDirectory(cfg, connection_factory=lambda u, p: Connection(server, user=u, password=p,
                                                                    client_strategy=MOCK_SYNC))
    with pytest.raises(DirectoryError):
        d.find("jdoe")


def test_no_hosts_configured():
    d = ADDirectory(DomainConfig(id="x", name="x", kind="ad", settings={}))
    with pytest.raises(DirectoryError):
        d._servers()


def test_escape():
    assert _escape("a*b(c)\\") == "a\\2ab\\28c\\29\\5c"
