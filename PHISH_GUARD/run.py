"""Start PhishGuard.

    python run.py                 # console + Microsoft 365 protection (if PG_M365_* is set)
    python run.py gateway         # also run the SMTP gateway (PG_SMTP_NEXT_HOP required)
    python run.py --port 8100 --ssl-keyfile key.pem --ssl-certfile cert.pem
"""

import argparse
import logging

import uvicorn

from phishguard.app import create_app
from phishguard.config import Settings

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", nargs="?", default="console", choices=["console", "gateway"])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8100)
    ap.add_argument("--ssl-keyfile")
    ap.add_argument("--ssl-certfile")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings()
    app = create_app(settings)
    if args.mode == "gateway":
        from phishguard.gateway import run_gateway
        run_gateway(app.state.guard, settings)
    uvicorn.run(app, host=args.host, port=args.port, ssl_keyfile=args.ssl_keyfile, ssl_certfile=args.ssl_certfile)
