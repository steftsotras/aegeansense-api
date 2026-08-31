# AegeanSense API

FastAPI backend for **AegeanSense**, the Aegean environmental monitoring network.

---

## Quick Start (Docker)

```bash
# 1. Clone/copy this folder to your machine

# 2. Create your .env file
cp .env.example .env

# 3. Edit .env with your FINoT credentials
nano .env

# 4. Start everything
docker-compose up -d

```

---

## Project Structure

```
aegeansense-api/
├── docker-compose.yml    # Runs PostgreSQL + API
├── Dockerfile            # Builds the API image
├── requirements.txt      # Python dependencies
├── schema.sql           # Database schema (runs on first start)
├── .env.example         # Template for configuration
├── .env                 # Your actual config (create this)
│
└── app/                 # FastAPI application
    ├── main.py          # Entry point
    ├── config.py        # Settings from environment
    ├── database.py      # SQLAlchemy connection
    ├── models.py        # Database models (includes SyncProgress)
    ├── schemas.py       # API request/response types
    │
    ├── dependencies.py  # Shared FastAPI dependencies (get_current_admin)
    │
    ├── routers/         # API endpoints
    │   ├── admin.py     # /api/admin/* (login, FinotProfile CRUD)
    │   ├── stations.py  # /api/stations/*
    │   ├── sync.py      # /api/sync/* (per-profile, admin-only)
    │   └── metrics.py   # /api/metrics
    │
    └── services/        # Business logic
        ├── auth.py           # Admin password hashing + JWT
        ├── crypto.py         # Encrypts FinotProfile passwords at rest
        ├── finot_client.py   # FINoT API client (instantiated per profile)
        └── sync_service.py   # Batch sync with progress tracking
```

---

## Configuration

Edit `.env` file:

```env
# Database
DB_PASSWORD=aegeansense_local_dev

# Batch sync settings
SYNC_BATCH_DAYS=7         # Fetch in 7-day chunks
SYNC_START_DATE=2024-01-01  # How far back to sync

# Admin bootstrap (seeds the first admin_users row on first startup)
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=change_me

# Signs admin JWT tokens - generate with:
#   python -c "import secrets; print(secrets.token_hex(32))"
JWT_SECRET=change-me-in-production
JWT_EXPIRE_MINUTES=720

# Encrypts FinotProfile passwords at rest - generate with:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
CREDENTIAL_ENCRYPTION_KEY=
```

FINoT credentials are no longer set via env vars. Each FINoT tenant is a `FinotProfile`, created through the admin API after logging in (see below) - this supports syncing multiple FINoT accounts into the same database.

---

## API Endpoints

### Stations
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/stations` | List all stations with latest metrics |
| GET | `/api/stations/{id}` | Get single station |
| GET | `/api/stations/{id}/readings` | Get historical data |
| GET | `/api/stations/{id}/latest` | Get latest readings only |

### Admin
All routes below `/api/admin/profiles` require `Authorization: Bearer <token>` from `/api/admin/login`.

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/admin/login` | Log in, get a bearer token |
| GET | `/api/admin/profiles` | List FINoT profiles |
| POST | `/api/admin/profiles` | Create a FINoT profile |
| GET | `/api/admin/profiles/{id}` | Get one profile |
| PATCH | `/api/admin/profiles/{id}` | Update a profile (incl. rotating its password) |
| DELETE | `/api/admin/profiles/{id}` | Delete a profile (its stations keep syncing history but lose the profile link) |

### Sync
All routes require `Authorization: Bearer <token>` and a `profile_id` query param (except `/logs` and `/runs`, which span all profiles).

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/sync?profile_id=1` | Trigger full sync for a profile (blocking) |
| POST | `/api/sync/start?profile_id=1` | Start background sync for a profile |
| POST | `/api/sync/stations?profile_id=1` | Sync only stations for a profile |
| POST | `/api/sync/readings?profile_id=1&days=30` | Sync readings with batch |
| GET | `/api/sync/status?profile_id=1` | Get detailed sync status (omit for all profiles) |
| GET | `/api/sync/progress?profile_id=1` | Get per-entity progress (omit for all profiles) |
| GET | `/api/sync/runs` | Get sync run history (all profiles) |
| POST | `/api/sync/test?profile_id=1` | Test a profile's FINoT connection |

### Metrics
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/metrics` | Get all metric definitions |

### System
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | API info |
| GET | `/health` | Health check (includes count of active FINoT profiles) |
| GET | `/test` | Database connectivity check |
| GET | `/docs` | Swagger UI |

---

### Reset everything
```bash
docker-compose down -v
docker-compose up -d
```
---

### Backup Database
```bash
docker exec -it aegeansense-db pg_dump -U aegeansense -d aegeansense > aegeansense_backup.sql
```

### Restore Database
```bash
docker exec -i aegeansense-db psql -U aegeansense -d aegeansense < aegeansense_backup.sql
```
