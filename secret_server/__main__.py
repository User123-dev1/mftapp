"""
Command-line entry point.

    python -m secret_server init --admin admin        # create key file + first administrator
    python -m secret_server serve --port 8443 --ssl-keyfile key.pem --ssl-certfile cert.pem
    python -m secret_server maintenance               # release expired check-outs, report expiry
    python -m secret_server rotate-master-key         # add a new master key and re-wrap data keys
"""

import argparse
import getpass
import json
import os
import sys

from .audit import AuditLog
from .config import Settings
from .crypto import KeyRing, generate_key
from .db import Database
from .directory import Directory
from .vault import Vault, VaultError


def cmd_init(settings: Settings, args) -> int:
    if not settings.master_key and not os.path.exists(settings.master_key_file):
        KeyRing.write_key_file(settings.master_key_file, {"k1": generate_key()}, "k1")
        print(f"Created master key file {settings.master_key_file} (mode 0600). "
              "Back it up offline; without it the vault cannot be decrypted.")
    keyring = KeyRing.from_settings(settings)
    db = Database(settings.db_path)
    with db.transaction() as conn:
        if conn.execute("SELECT 1 FROM users WHERE role = 'administrator'").fetchone():
            print("An administrator already exists; nothing to do.")
            return 0
        password = os.getenv("SS_ADMIN_PASSWORD") or getpass.getpass(f"Password for {args.admin}: ")
        try:
            Directory(AuditLog(keyring)).create_user(conn, None, args.admin, password,
                                                     role="administrator")
        except VaultError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    print(f"Administrator '{args.admin}' created.")
    return 0


def cmd_serve(settings: Settings, args) -> int:
    import uvicorn
    from .api import create_app

    if not (args.ssl_keyfile and args.ssl_certfile) and args.host not in ("127.0.0.1", "localhost"):
        print("warning: serving without TLS on a non-loopback interface", file=sys.stderr)
    uvicorn.run(create_app(settings), host=args.host, port=args.port,
                ssl_keyfile=args.ssl_keyfile, ssl_certfile=args.ssl_certfile,
                proxy_headers=args.behind_proxy)
    return 0


def cmd_maintenance(settings: Settings, args) -> int:
    keyring = KeyRing.from_settings(settings)
    with Database(settings.db_path).transaction() as conn:
        print(json.dumps(Vault(keyring, AuditLog(keyring)).run_maintenance(conn), indent=2))
    return 0


def cmd_rotate_master_key(settings: Settings, args) -> int:
    if settings.master_key:
        print("error: master key comes from SS_MASTER_KEY; rotate it in your secret store "
              "and use a key file to hold old + new keys during re-wrap", file=sys.stderr)
        return 1
    with open(settings.master_key_file, encoding="utf-8") as fh:
        data = json.load(fh)
    new_id = f"k{len(data['keys']) + 1}"
    while new_id in data["keys"]:
        new_id += "x"
    data["keys"][new_id] = generate_key()
    KeyRing.write_key_file(settings.master_key_file, data["keys"], new_id)
    keyring = KeyRing.from_settings(settings)
    with Database(settings.db_path).transaction() as conn:
        count = Vault(keyring, AuditLog(keyring)).rewrap_all(conn)
    print(f"New master key '{new_id}' is current; re-wrapped {count} keys/values. "
          "Keep retired keys in the key file: audit-chain verification uses them.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="secret_server")
    sub = parser.add_subparsers(dest="command", required=True)
    p_init = sub.add_parser("init")
    p_init.add_argument("--admin", default="admin")
    p_serve = sub.add_parser("serve")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8443)
    p_serve.add_argument("--ssl-keyfile")
    p_serve.add_argument("--ssl-certfile")
    p_serve.add_argument("--behind-proxy", action="store_true",
                         help="trust X-Forwarded-* headers from the reverse proxy")
    sub.add_parser("maintenance")
    sub.add_parser("rotate-master-key")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    handler = {"init": cmd_init, "serve": cmd_serve, "maintenance": cmd_maintenance,
               "rotate-master-key": cmd_rotate_master_key}[args.command]
    return handler(settings, args)


if __name__ == "__main__":
    sys.exit(main())
