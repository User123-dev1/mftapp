"""Break-glass password reset, run on the server console.

Use when an administrator has forgotten their password or is locked out:

    python -m gmp_assets.reset_password admin

The account is unlocked and given a temporary password that must be changed
at the next sign-in. The reset is recorded in the audit trail, attributed to
the operating-system user who ran the command. Run it from the folder the
server is started from (or set GMP_DB_PATH) so it opens the same database.
"""

import argparse
import getpass
import os
import sys

from . import audit, clock
from .config import Settings
from .db import Database
from .security import hash_password, password_policy_errors


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Reset a user's password (break-glass).")
    ap.add_argument("username")
    args = ap.parse_args(argv)
    settings = Settings()

    if not os.path.exists(settings.db_path):
        print(f"Database not found: {os.path.abspath(settings.db_path)}\n"
              "Run this from the GMP_ASSET_MANAGER folder you start the server from, or set GMP_DB_PATH.", file=sys.stderr)
        return 1

    db = Database(settings.db_path)
    with db.read() as conn:
        user = conn.execute("SELECT * FROM users WHERE username = ?", (args.username,)).fetchone()
    if user is None:
        print(f"No user named '{args.username}' in {os.path.abspath(settings.db_path)}", file=sys.stderr)
        return 1

    while True:
        password = getpass.getpass("New temporary password: ")
        errors = password_policy_errors(password, user["username"], settings.password_min_length)
        if errors:
            print("Password " + "; ".join(errors))
            continue
        if getpass.getpass("Repeat: ") != password:
            print("Passwords do not match")
            continue
        break

    operator = f"console:{getpass.getuser()}"
    with db.transaction() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ?, password_changed_at = ?, must_change_password = 1, "
            "failed_attempts = 0, locked = 0, active = 1 WHERE id = ?",
            (hash_password(password, settings.pbkdf2_iterations), clock.now_iso(), user["id"]))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
        audit.record(conn, username=operator, action="PASSWORD_RESET_CONSOLE", entity_type="user", entity_id=user["id"],
                     new={"username": user["username"], "unlocked": True, "must_change_password": True},
                     reason="Break-glass password reset from server console")
    print(f"Password for '{user['username']}' reset. It must be changed at next sign-in.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
