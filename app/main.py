"""
AegeanSense API.

FastAPI application exposing station, reading, and metric endpoints, plus
admin-authenticated endpoints for managing FINoT profiles and triggering
per-profile syncs.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager, suppress
import asyncio
import logging
import time
from sqlalchemy.exc import OperationalError

from .config import get_settings
from .database import SessionLocal, initialize_database
from .routers import admin, stations, sync, metrics
from .models import AdminUser, FinotProfile
from .services.auth import hash_password, verify_password
from .services.sync_service import recover_interrupted_syncs

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

settings = get_settings()


def _sync_bootstrap_admin():
    """
    Apply ADMIN_EMAIL/ADMIN_PASSWORD at startup.

    Creates the first admin on an empty table, and resets that admin's password
    when ADMIN_PASSWORD has changed, so rotating it only needs a redeploy.
    """
    if not settings.admin_email or not settings.admin_password:
        return

    db = SessionLocal()
    try:
        admin = db.query(AdminUser).filter(AdminUser.email == settings.admin_email).first()
        if admin is None:
            if db.query(AdminUser).first():
                logger.warning(f"ADMIN_EMAIL {settings.admin_email} matches no admin; existing admins left unchanged")
                return
            db.add(AdminUser(
                email=settings.admin_email,
                password_hash=hash_password(settings.admin_password)
            ))
            db.commit()
            logger.info(f"Seeded initial admin user: {settings.admin_email}")
        elif not verify_password(settings.admin_password, admin.password_hash):
            admin.password_hash = hash_password(settings.admin_password)
            db.commit()
            logger.info(f"Updated password for admin {settings.admin_email} from ADMIN_PASSWORD")
    finally:
        db.close()


async def _scheduled_sync_loop(interval_hours: float):
    """Periodically run an incremental sync of every active profile."""
    # Short delay so a crash-looping container doesn't hammer FINoT on every boot.
    await asyncio.sleep(60)
    while True:
        db = SessionLocal()
        try:
            if sync.sync_in_progress(db):
                logger.info("Scheduled sync skipped: a sync is already in progress")
            else:
                profile_ids = [p.id for p in db.query(FinotProfile).filter(FinotProfile.is_active == True)]
                if profile_ids:
                    logger.info(f"Starting scheduled sync for profiles {profile_ids}")
                    sync.start_background_sync(profile_ids)
        except Exception as e:
            logger.error(f"Scheduled sync could not start: {e}")
        finally:
            db.close()
        await asyncio.sleep(interval_hours * 3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown."""
    logger.info("Starting AegeanSense API...")
    logger.info(f"  SYNC_BATCH_DAYS: {settings.sync_batch_days}")
    logger.info(f"  SYNC_START_DATE: {settings.sync_start_date}")
    logger.info(f"  SYNC_INTERVAL_HOURS: {settings.sync_interval_hours or 'disabled'}")

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

    _sync_bootstrap_admin()

    db = SessionLocal()
    try:
        recover_interrupted_syncs(db)
    finally:
        db.close()

    scheduler = None
    if settings.sync_interval_hours > 0:
        scheduler = asyncio.create_task(_scheduled_sync_loop(settings.sync_interval_hours))

    yield

    logger.info("Shutting down...")
    if scheduler:
        scheduler.cancel()
        with suppress(asyncio.CancelledError):
            await scheduler


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
