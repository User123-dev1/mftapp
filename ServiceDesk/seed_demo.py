"""Fill an empty database with demo users, CMDB items and tickets: python seed_demo.py

All demo accounts use the password Password1! (the IT manager keeps SD_ADMIN_PASSWORD).
"""

import sys
from datetime import date, timedelta

from sqlalchemy import func, select

from datetime import datetime

from app import changes, cmdb, database, services
from app.config import settings
from app.main import ensure_admin
from app.models import (
    Category, ChangeType, CIRelationship, CIStatus, CIType, ConfigItem, Level, Priority, Role, Status, SupportGroup,
    Ticket, User,
)
from app.security import hash_password

PASSWORD = "Password1!"


def main() -> None:
    database.init_db()
    ensure_admin()
    db = database.SessionLocal()
    if db.scalar(select(func.count()).select_from(Ticket)) and "--force" not in sys.argv:
        print("The database already has tickets; run with --force to add demo data anyway.")
        return

    def user(email, name, role, dept=None):
        u = db.scalar(select(User).where(User.email == email))
        if u is None:
            u = User(email=email, full_name=name, role=role, department=dept, is_active=True,
                     password_hash=hash_password(PASSWORD))
            db.add(u)
        return u

    manager = db.scalar(select(User).where(User.role == Role.MANAGER))
    alice = user("alice.agent@example.com", "Alice Agent", Role.AGENT)
    sam = user("sam.network@example.com", "Sam Network", Role.AGENT)
    sam.is_change_approver = True
    rita = user("rita@example.com", "Rita Requester", Role.REQUESTER, "Finance")
    omar = user("omar@example.com", "Omar Ops", Role.REQUESTER, "Operations")
    db.flush()

    desktop = SupportGroup(name="Desktop Support", members=[alice], auto_assign=True)
    network = SupportGroup(name="Network", members=[sam])
    apps = SupportGroup(name="Business Apps", members=[alice, sam])
    db.add_all([desktop, network, apps])

    today = date.today()

    def item(name, ci_type, group, owner=None, **kw):
        ci = ConfigItem(name=name, ci_type=ci_type, status=CIStatus.IN_USE, support_group=group,
                        assigned_user=owner, **kw)
        db.add(ci)
        return ci

    rita_lt = item("RITA-LT01", CIType.LAPTOP, desktop, rita, asset_tag="LT-0042", serial_number="5CG1234XYZ",
                   manufacturer="Dell", model="Latitude 7440", version="Windows 11", location="HQ Floor 2",
                   warranty_expires=today - timedelta(days=30))
    item("RITA-MON01", CIType.MONITOR, desktop, rita, asset_tag="MN-0107", manufacturer="Dell", model="U2723QE")
    omar_lt = item("OMAR-LT07", CIType.LAPTOP, desktop, omar, asset_tag="LT-0077", manufacturer="Lenovo",
                   model="ThinkPad T14", version="Windows 11", warranty_expires=today + timedelta(days=45))
    item("HQ-Printer-2F", CIType.PRINTER, desktop, asset_tag="PR-02", model="HP LaserJet M507", location="HQ Floor 2")
    payroll = item("Payroll (ADP)", CIType.APPLICATION, apps, vendor="ADP", change_controlled=True)
    item("Microsoft 365", CIType.APPLICATION, apps, vendor="Microsoft")
    vpn = item("VPN", CIType.SERVICE, network)
    firewall = item("Core Firewall 1", CIType.NETWORK, network, asset_tag="FW-01", manufacturer="Fortinet",
                    model="FortiGate 60F", ip_address="10.0.0.1", location="Data Center", change_controlled=True)
    sql = item("SQL01", CIType.SERVER, apps, asset_tag="SRV-01", version="Windows Server 2022",
               change_controlled=True)
    erp = item("ERP", CIType.APPLICATION, apps, change_controlled=True)
    db.flush()
    db.add_all([CIRelationship(source=erp, target=sql), CIRelationship(source=payroll, target=sql),
                CIRelationship(source=sql, target=firewall), CIRelationship(source=vpn, target=firewall)])

    def ticket(requester, title, category, priority, ci=None, details=None):
        return services.create_ticket(db, requester, title, f"{title}. (demo ticket)", category, priority,
                                      details=details, ci=ci)

    ticket(rita, "Laptop battery drains in an hour", Category.HARDWARE, Priority.MEDIUM, rita_lt)
    ticket(rita, "Blue screen after update", Category.INCIDENT, Priority.HIGH, rita_lt)
    t = ticket(omar, "Cannot connect to VPN from home", Category.INCIDENT, Priority.HIGH, vpn)
    services.assign_ticket(db, t, sam, manager)
    services.change_status(db, t, Status.IN_PROGRESS, sam)
    t = ticket(rita, "Payslip PDF won't open", Category.SOFTWARE, Priority.LOW, payroll)
    services.assign_ticket(db, t, alice, manager)
    services.change_status(db, t, Status.RESOLVED, alice, "Cleared browser cache; PDF opens now.",
                           no_change_reason="Browser issue on the user's PC; Payroll was not modified.")
    fw = ticket(omar, "Open TCP 443 from warehouse to ERP", Category.FIREWALL, Priority.MEDIUM,
                details={"source": "10.20.0.0/24", "destination": "erp01", "ports": "TCP 443",
                         "justification": "New scanners"})
    services.assign_ticket(db, fw, sam, manager)
    next_week = datetime.now().replace(hour=19, minute=0, second=0, microsecond=0) + timedelta(days=7)
    chg = changes.create_change(db, sam, {
        "title": "Allow TCP 443 warehouse VLAN -> ERP", "description": "Firewall rule for new scanners.",
        "change_type": ChangeType.NORMAL, "risk": Level.MEDIUM, "impact": Level.LOW,
        "implementation_plan": "1. Add address objects\n2. Add policy above deny-all\n3. Test from scanner",
        "backout_plan": "Disable the new policy", "test_plan": "Scanner can reach https://erp01",
        "planned_start": next_week, "planned_end": next_week + timedelta(hours=1),
    }, [firewall], sam, ticket=fw)
    changes.submit(db, chg, sam)   # waits for the manager's approval (Sam can't approve his own)
    patch = changes.create_change(db, alice, {
        "title": "Monthly Windows patching - SQL01", "description": "Apply cumulative update and reboot.",
        "change_type": ChangeType.NORMAL, "risk": Level.HIGH, "impact": Level.MEDIUM,
        "implementation_plan": "Snapshot, patch, reboot, verify services", "backout_plan": "Revert snapshot",
        "planned_start": next_week + timedelta(minutes=30), "planned_end": next_week + timedelta(hours=3),
    }, [sql], alice)
    changes.submit(db, patch, alice)
    changes.decide(db, patch, sam, True, "OK outside business hours")
    ticket(omar, "Keyboard keys sticking", Category.HARDWARE, Priority.LOW, omar_lt)
    ticket(rita, "Access to shared Finance drive", Category.ACCOUNT, Priority.MEDIUM,
           details={"request_type": "access", "system": "\\\\fs01\\finance", "access_level": "Read/Write"})
    db.commit()
    db.close()

    print("Demo data added. Sign in at", settings.base_url)
    print(f"  IT manager : {settings.admin_email} / (SD_ADMIN_PASSWORD, default ChangeMe123!)")
    print(f"  Agents     : alice.agent@example.com, sam.network@example.com (change approver) / {PASSWORD}")
    print(f"  Requesters : rita@example.com, omar@example.com / {PASSWORD}")


if __name__ == "__main__":
    main()
