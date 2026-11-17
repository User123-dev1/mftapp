"""Start Password Station:  python run.py --host 0.0.0.0 --port 8200"""

import argparse
import logging

import uvicorn

from pstation.app import create_app

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8200)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # Behind the portal's reverse proxy: trust its X-Forwarded-For so audit and rate limits see the real client.
    uvicorn.run(create_app(), host=args.host, port=args.port, proxy_headers=True, forwarded_allow_ips="*")
