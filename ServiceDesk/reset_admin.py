"""Service Desk accounts from the server console.

    docker compose exec servicedesk python reset_admin.py --list
        Show every account (e-mail, role, active) - no passwords.
    docker compose exec servicedesk python reset_admin.py
        Set the IT manager account (SD_ADMIN_EMAIL) back to SD_ADMIN_PASSWORD, reactivate it
        and make sure it is an IT manager. Creates it if it is missing.
    docker compose exec servicedesk python reset_admin.py --email someone@abelzeta.com --password 'New-Pass-2026!'
        Set any existing account's password.
"""
import argparse
import sys

from sqlalchemy import select

from app import database
from app.config import settings
from app.models import Role, User
from app.security import hash_password


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="list accounts and exit")
    ap.add_argument("--email", help="account to reset (default: SD_ADMIN_EMAIL)")
    ap.add_argument("--password", help="new password (default: SD_ADMIN_PASSWORD)")
    args = ap.parse_args()

    database.init_db()
    db = database.SessionLocal()
    try:
        if args.list:
            users = db.scalars(select(User).order_by(User.role, User.email)).all()
            print(f"{len(users)} account(s) in {settings.database_url}:")
            for u in users:
                print(f"  {u.email:40} {u.role.value:12} {'active' if u.is_active else 'DEACTIVATED'}")
            return
        email = (args.email or settings.admin_email).strip().lower()
        password = args.password or settings.admin_password
        if len(password) < 8:
            sys.exit("The password must be at least 8 characters.")
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            if args.email:
                sys.exit(f"No account {email}. Run with --list to see the accounts.")
            user = User(email=email, full_name=settings.admin_name, role=Role.MANAGER, password_hash="")
            db.add(user)
        user.password_hash = hash_password(password)
        user.is_active = True
        if not args.email:
            user.role = Role.MANAGER
        db.commit()
        print(f"Service Desk: password for {email} reset" + ("" if args.password else " to SD_ADMIN_PASSWORD") +
              "; account active.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
