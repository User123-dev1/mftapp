"""Start the Service Desk web server: python run.py"""

import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=os.getenv("SD_HOST", "0.0.0.0"),
        port=int(os.getenv("SD_PORT", "8000")),
        reload=os.getenv("SD_RELOAD", "false").lower() == "true",
    )
