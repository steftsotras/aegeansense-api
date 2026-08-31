"""
AegeanSense API.

FastAPI application exposing station, reading, and metric endpoints, plus
admin-authenticated endpoints for managing FINoT profiles and triggering
per-profile syncs.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging
import time
from sqlalchemy.exc import OperationalError

from .config import get_settings
from .database import SessionLocal, initialize_database
from .routers import admin, stations, sync, metrics
from .models import AdminUser, FinotProfile
from .services.auth import hash_password

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

settings = get_settings()


def _seed_initial_admin():
    """Create the first admin_users row from ADMIN_EMAIL/ADMIN_PASSWORD if none exists yet."""
    if not settings.admin_email or not settings.admin_password:
        return

    db = SessionLocal()
    try:
        if db.query(AdminUser).first():
            return
        db.add(AdminUser(
            email=settings.admin_email,
            password_hash=hash_password(settings.admin_password)
        ))
        db.commit()
        logger.info(f"Seeded initial admin user: {settings.admin_email}")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown."""
    logger.info("Starting AegeanSense API...")
    logger.info(f"  SYNC_BATCH_DAYS: {settings.sync_batch_days}")
    logger.info(f"  SYNC_START_DATE: {settings.sync_start_date}")

    # Railway starts services independently, so PostgreSQL may need a moment to
    # become reachable during a first deployment.
    for attempt in range(1, 11):
        try:
            initialize_database()
            break
        except OperationalError:
            if attempt == 10:
                raise
            logger.warning("Database is not ready (attempt %s/10); retrying...", attempt)
            time.sleep(2)

    _seed_initial_admin()

    yield

    logger.info("Shutting down...")


# Create FastAPI app
app = FastAPI(
    title="AegeanSense API",
    description="Environmental monitoring API for the Aegean station network, with batch synchronization support",
    version="2.0.0",
    lifespan=lifespan
)

# CORS - allow your Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(admin.router)
app.include_router(stations.router)
app.include_router(sync.router)
app.include_router(metrics.router)


@app.get("/")
def root():
    """API info."""
    return {
        "name": "AegeanSense API",
        "version": "2.0.0",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health")
def health():
    """Health check."""
    db = SessionLocal()
    try:
        active_profiles = db.query(FinotProfile).filter(FinotProfile.is_active == True).count()
    finally:
        db.close()

    return {
        "status": "healthy",
        "finot_profiles_active": active_profiles
    }


@app.get("/test")
def test_connection():
    """Basic database connectivity check (FINoT connectivity is tested per-profile via /api/sync/test)."""
    from sqlalchemy import func
    from .models import Station, Reading

    results = {
        "database": {"connected": False, "stations": 0, "readings": 0},
        "errors": []
    }

    try:
        db = SessionLocal()
        station_count = db.query(func.count(Station.id)).scalar() or 0
        reading_count = db.query(func.count(Reading.id)).scalar() or 0
        db.close()

        results["database"] = {
            "connected": True,
            "stations": station_count,
            "readings": reading_count
        }
    except Exception as e:
        results["errors"].append(f"Database error: {str(e)}")

    return results
