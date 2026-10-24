"""Fill an empty database with demo users, CMDB items and tickets: python seed_demo.py

All demo accounts use the password Password1! (the IT manager keeps SD_ADMIN_PASSWORD).
"""

import sys
from datetime import date, timedelta

from sqlalchemy import func, select

from app import cmdb, database, services
from app.config import settings
from app.main import ensure_admin
from app.models import Category, CIStatus, CIType, ConfigItem, Priority, Role, Status, SupportGroup, Ticket, User
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
    payroll = item("Payroll (ADP)", CIType.APPLICATION, apps, vendor="ADP")
    item("Microsoft 365", CIType.APPLICATION, apps, vendor="Microsoft")
    vpn = item("VPN", CIType.SERVICE, network)
    item("Core Firewall 1", CIType.NETWORK, network, asset_tag="FW-01", manufacturer="Fortinet",
         model="FortiGate 60F", ip_address="10.0.0.1", location="Data Center")
    db.flush()

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
    services.change_status(db, t, Status.RESOLVED, alice, "Cleared browser cache; PDF opens now.")
    ticket(omar, "Open TCP 443 from warehouse to ERP", Category.FIREWALL, Priority.MEDIUM,
           details={"source": "10.20.0.0/24", "destination": "erp01", "ports": "TCP 443",
                    "justification": "New scanners"})
    ticket(omar, "Keyboard keys sticking", Category.HARDWARE, Priority.LOW, omar_lt)
    ticket(rita, "Access to shared Finance drive", Category.ACCOUNT, Priority.MEDIUM,
           details={"request_type": "access", "system": "\\\\fs01\\finance", "access_level": "Read/Write"})
    db.commit()
    db.close()

    print("Demo data added. Sign in at", settings.base_url)
    print(f"  IT manager : {settings.admin_email} / (SD_ADMIN_PASSWORD, default ChangeMe123!)")
    print(f"  Agents     : alice.agent@example.com, sam.network@example.com / {PASSWORD}")
    print(f"  Requesters : rita@example.com, omar@example.com / {PASSWORD}")


if __name__ == "__main__":
    main()
