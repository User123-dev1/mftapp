"""Unlock PhishGuard's 'admin' account and reset its password.

PhishGuard locks an account after 5 wrong passwords, and only another administrator can unlock
it - which leaves you stuck if 'admin' is the only one. Run this on the server instead:

    docker compose exec phishguard python reset_admin.py              # back to PG_ADMIN_PASSWORD
    docker compose exec phishguard python reset_admin.py --password 'New-Temp-Pass-123'

The account is unlocked, re-activated, signed out everywhere, and must change the password at
the next sign-in. Nothing is printed about the password itself.
"""

import argparse
import sys

from phishguard.auth import Users, password_problems
from phishguard.config import Settings
from phishguard.db import Database


def main() -> int:
    ap = argparse.ArgumentParser(description="Unlock and reset the PhishGuard admin account")
    ap.add_argument("--username", default="admin")
    ap.add_argument("--password", help="temporary password (default: PG_ADMIN_PASSWORD)")
    args = ap.parse_args()

    settings = Settings()
    password = args.password or settings.admin_password
    if not password:
        print("No password given and PG_ADMIN_PASSWORD is not set.", file=sys.stderr)
        return 1
    problem = password_problems(password, args.username)
    if problem:
        print(f"That password is not allowed: {problem}", file=sys.stderr)
        return 1

    users = Users(Database(settings.db_path), settings.session_idle_minutes)
    with users.db.tx() as conn:
        row = conn.execute("SELECT id FROM users WHERE username = ?", (args.username,)).fetchone()
        empty = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    if row is None:
        if empty and args.username == "admin":
            users.ensure_admin(password)
            print("No users existed; created 'admin'.")
            return 0
        print(f"No user called {args.username!r}.", file=sys.stderr)
        return 1
    users.update(row["id"], active=False)            # also ends every existing session
    users.update(row["id"], active=True, unlock=True, temp_password=password)
    print(f"'{args.username}' unlocked; password reset (must be changed at next sign-in).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
