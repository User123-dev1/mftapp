"""Reset a GMP Asset Manager account from the server console (e.g. the only admin is locked out).

    docker compose exec gmp python reset_admin.py                  # 'admin' back to GMP_ADMIN_PASSWORD
    docker compose exec -T gmp python reset_admin.py --password-stdin < file

The account is unlocked, re-activated and signed out everywhere. With GMP_ADMIN_PASSWORD it must
be changed at the next sign-in; with --password-stdin (a password its owner chose) it need not.
The reset is written to the audit trail as done by 'system' from the server console.
"""
import argparse
import os
import sys

from gmp_assets import audit, clock
from gmp_assets.config import Settings
from gmp_assets.db import Database
from gmp_assets.security import hash_password, password_policy_errors


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--username", default=os.environ.get("GMP_ADMIN_USERNAME", "admin"))
    ap.add_argument("--password-stdin", action="store_true", help="read the new password from stdin")
    args = ap.parse_args()

    settings = Settings()
    chosen = args.password_stdin
    password = sys.stdin.readline().rstrip("\r\n") if chosen else os.environ.get("GMP_ADMIN_PASSWORD", "")
    errors = password_policy_errors(password, args.username, settings.password_min_length)
    if errors:
        print("That password is not allowed: password " + "; ".join(errors), file=sys.stderr)
        return 1
    db = Database(settings.db_path)
    with db.transaction() as conn:
        row = conn.execute("SELECT id, password_hash FROM users WHERE username = ?", (args.username,)).fetchone()
        if row is None:
            print(f"No user called {args.username!r}.", file=sys.stderr)
            return 1
        now = clock.now_iso()
        conn.execute("INSERT INTO password_history (user_id, password_hash, created_at) VALUES (?, ?, ?)",
                     (row["id"], row["password_hash"], now))
        conn.execute("UPDATE users SET password_hash = ?, password_changed_at = ?, must_change_password = ?, "
                     "failed_attempts = 0, locked = 0, active = 1 WHERE id = ?",
                     (hash_password(password, settings.pbkdf2_iterations), now, 0 if chosen else 1, row["id"]))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],))
        audit.record(conn, username="system", action="PASSWORD_RESET", entity_type="user", entity_id=row["id"],
                     reason="Reset from the server console")
    print(f"GMP Asset Manager: '{args.username}' unlocked; password reset"
          + ("." if chosen else " (must be changed at next sign-in)."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
