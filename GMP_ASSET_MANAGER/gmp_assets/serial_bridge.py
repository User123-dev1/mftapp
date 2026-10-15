"""Serial / virtual-COM barcode scanner bridge.

For fixed-mount or RS-232/USB-CDC scanners that are not in keyboard mode.
Each line received from the scanner is sent to ``/api/scan`` and the
fitness-for-use verdict is printed - useful for a gowning-room or
equipment-room kiosk.

    python -m gmp_assets.serial_bridge --port /dev/ttyACM0 --url http://localhost:8000 --user op1

Requires ``pyserial`` (``pip install pyserial``). Scanners should be
configured to transmit the AIM symbology identifier and a CR/LF suffix.
"""

import argparse
import getpass
import json
import sys
import urllib.error
import urllib.request


def _post(url, payload, token=None):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": f"Bearer {token}"} if token else {})})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def describe(result: dict) -> str:
    if result["match_type"] == "asset":
        a, f = result["asset"], result["fitness"]
        verdict = "FIT FOR USE" if f["fit_for_use"] else "DO NOT USE"
        lines = [f"{a['asset_tag']}  {a['description']}  ->  {verdict}"] + [f"   - {r}" for r in f["reasons"]]
        return "\n".join(lines)
    if result["match_type"] == "location":
        return f"LOCATION {result['location']['code']} - {result['location']['name']}"
    parsed = result["parsed"]
    if not parsed["valid"]:
        return "INVALID BARCODE: " + "; ".join(parsed["errors"])
    if result["match_type"] == "gs1_item":
        return f"GS1 item GTIN {parsed['gtin']} lot {parsed['batch']} expiry {parsed['expiry']}" + \
            ("  ** EXPIRED **" if result.get("expired") else "")
    return f"Not found: {parsed['data']}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", required=True, help="Serial port, e.g. COM3 or /dev/ttyACM0")
    ap.add_argument("--baud", type=int, default=9600)
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--user", required=True)
    args = ap.parse_args(argv)

    try:
        import serial  # type: ignore
    except ImportError:
        print("pyserial is required: pip install pyserial", file=sys.stderr)
        return 2

    password = getpass.getpass(f"Password for {args.user}: ")
    login = _post(f"{args.url}/api/auth/login", {"username": args.user, "password": password})
    if login.get("must_change_password"):
        print("Password change required - sign in through the web UI first.", file=sys.stderr)
        return 1
    token = login["token"]
    print(f"Signed in as {login['user']['full_name']}. Waiting for scans on {args.port} (Ctrl+C to stop)...")

    with serial.Serial(args.port, args.baud, timeout=1) as port:
        try:
            while True:
                line = port.readline()
                if not line:
                    continue
                raw = line.decode("latin-1").rstrip("\r\n")
                try:
                    print(describe(_post(f"{args.url}/api/scan", {"data": raw}, token)))
                except urllib.error.HTTPError as exc:
                    print(f"Error {exc.code}: {exc.read().decode(errors='replace')}")
                    if exc.code == 401:
                        return 1
        except KeyboardInterrupt:
            _post(f"{args.url}/api/auth/logout", {}, token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
