"""Reset the Password Station 'admin' account and clear its lockout.

    docker compose exec passwordstation python reset_admin.py              # back to PS_ADMIN_PASSWORD
    docker compose exec passwordstation python reset_admin.py --password 'New-Pass-2026!'
"""
import argparse
import sys

from pstation.config import Settings
from pstation.db import Database
from pstation.service import Station


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--password", help="new password (default: PS_ADMIN_PASSWORD)")
    args = ap.parse_args()
    s = Settings()
    password = args.password or s.admin_password
    if len(password or "") < 12:
        sys.exit("Need a password of at least 12 characters (set PS_ADMIN_PASSWORD or pass --password).")
    Station(s, Database(s.db_path)).reset_admin(password)
    print("Password Station: 'admin' reset and unlocked." + ("" if args.password else " Password = PS_ADMIN_PASSWORD."))


if __name__ == "__main__":
    main()
