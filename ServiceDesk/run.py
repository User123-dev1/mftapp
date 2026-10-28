"""Start the Service Desk web server.

    python run.py                 # http://localhost:8085
    python run.py --port 9000     # or set SD_PORT=9000
"""

import argparse
import os
import warnings

import uvicorn

# Newer Authlib releases warn about their own httpx integration at import time; it's harmless
# but PowerShell shows it as an error, so hide it.
warnings.filterwarnings("ignore", message=r".*httpx module is deprecated.*")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the IT Service Desk web server")
    parser.add_argument("--port", type=int, default=int(os.getenv("SD_PORT", "8085")))
    parser.add_argument("--host", default=os.getenv("SD_HOST", "0.0.0.0"))
    args = parser.parse_args()
    os.environ["SD_PORT"] = str(args.port)  # so links in emails use the right port
    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=os.getenv("SD_RELOAD", "false").lower() == "true",
    )
