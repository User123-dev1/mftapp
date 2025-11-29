"""
MFT API Server with Scheduler Integration
"""

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime
import logging
import os

logger = logging.getLogger(__name__)

# Initialize FastAPI
app = FastAPI(
    title="Managed File Transfer API",
    description="Enterprise MFT with scheduler",
    version="2.0.0"
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global instances (set by startup)
mft_app = None
scheduler = None


# Include advanced router
from api_advanced import router as advanced_router
app.include_router(advanced_router)


class HealthResponse(BaseModel):
    status: str
    timestamp: str
    components: Dict[str, str]


@app.on_event("startup")
async def startup_event():
    """Initialize on startup"""
    global mft_app, scheduler
    
    logger.info("Starting MFT Application...")
    
    from database import init_database
    from mft_application import MFTApplication
    from transfer_scheduler import TransferScheduler
    
    # Initialize database
    db = init_database()
    logger.info("Database initialized")
    
    # Initialize MFT
    mft_app = MFTApplication()
    logger.info("MFT core initialized")
    
    # Initialize and start scheduler
    scheduler = TransferScheduler(mft_app)
    await scheduler.start()
    logger.info("Scheduler started")
    
    logger.info("MFT Application ready!")


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    logger.info("Shutting down...")
    
    if scheduler:
        await scheduler.stop()
    
    logger.info("Shutdown complete")


@app.get("/", response_class=HTMLResponse)
async def root():
    """Root endpoint"""
    return HTMLResponse("""
    <html>
        <head><title>MFT Application</title></head>
        <body>
            <h1>Managed File Transfer Application</h1>
            <p>Enterprise MFT with multi-protocol support</p>
            <ul>
                <li><a href="/docs">API Documentation</a></li>
                <li><a href="/health">Health Check</a></li>
                <li><a href="/api/v1/devices">List Devices</a></li>
                <li><a href="/api/v1/rules">List Rules</a></li>
            </ul>
        </body>
    </html>
    """)


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check"""
    components = {
        "mft_application": "healthy" if mft_app else "unavailable",
        "scheduler": "healthy" if scheduler else "unavailable",
        "database": "healthy"
    }
    
    overall = "healthy" if all(v == "healthy" for v in components.values()) else "degraded"
    
    return HealthResponse(
        status=overall,
        timestamp=datetime.utcnow().isoformat(),
        components=components
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail, "timestamp": datetime.utcnow().isoformat()}
    )


@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "timestamp": datetime.utcnow().isoformat()}
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api_server:app", host="0.0.0.0", port=8000, reload=False)
