"""Start the GMP Asset Manager.

    python run_server.py                    # http://localhost:8000
    GMP_DB_PATH=/data/gmp.db python run_server.py --host 0.0.0.0 --port 8443 \
        --ssl-keyfile key.pem --ssl-certfile cert.pem
"""

import argparse
import logging

import uvicorn

from gmp_assets.api import create_app

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--ssl-keyfile")
    ap.add_argument("--ssl-certfile")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(create_app(), host=args.host, port=args.port,
                ssl_keyfile=args.ssl_keyfile, ssl_certfile=args.ssl_certfile)
