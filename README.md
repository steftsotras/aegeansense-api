# AegeanSense API

FastAPI service for station metadata, time-series readings, FINoT synchronization, and admin management. This folder is the backend of the combined repository; see the [root README](../README.md) for the complete setup.

## Configuration

Copy `.env.example` to `.env` and set:

| Variable | Purpose |
| --- | --- |
| `DB_PASSWORD` | Local Compose PostgreSQL password |
| `DATABASE_URL` | PostgreSQL connection URL for direct or hosted execution; Compose constructs its own URL using `DB_PASSWORD` |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | Bootstrap account, created when the admin table is empty; a changed `ADMIN_PASSWORD` is applied to that account at the next startup |
| `JWT_SECRET` | Generated signing secret of at least 32 characters |
| `JWT_EXPIRE_MINUTES` | Token lifetime, default 720 minutes |
| `CREDENTIAL_ENCRYPTION_KEY` | Fernet key used to encrypt FINoT passwords |
| `CORS_ORIGINS` | Comma-separated browser origins allowed to call the API |
| `SYNC_BATCH_DAYS`, `SYNC_START_DATE` | Historical synchronization window settings |
| `SYNC_INTERVAL_HOURS` | Automatic incremental sync of every active profile, default 6 hours; `0` disables it |

Generate a signing secret with `python -c "import secrets; print(secrets.token_hex(32))"`. Generate a Fernet key with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` after installing dependencies. Replace the example admin password. Preserve the encryption key when restoring a database. Changing `CREDENTIAL_ENCRYPTION_KEY` makes stored FINoT passwords unreadable; re-enter each profile's password in the admin interface afterwards.

FINoT URLs and credentials are configured through `/api/admin/profiles` after login. No FINoT account is bundled.

## Docker

Run from this directory after configuring `.env`:

```bash
docker compose up --build -d
```

PostgreSQL runs on port 5432 and the API on port 8000. The schema and reference metrics are initialized automatically. Browse `http://localhost:8000/docs` for the complete endpoint contract.

## Python development

Use Python 3.11 and PostgreSQL 16:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.lock
.venv/Scripts/python.exe -m uvicorn app.main:app --reload
```

On Unix, use `.venv/bin/python`. Set `DATABASE_URL` in `.env` for your local database. `requirements.txt` lists direct dependencies; `requirements.lock` pins the resolved dependency set used for installation and Docker builds.

## API groups

| Path | Access | Purpose |
| --- | --- | --- |
| `/api/stations/` | Public | Station list and filters |
| `/api/stations/{id}` | Public | Station details |
| `/api/stations/{id}/readings` | Public | Date/metric filters and hour/day/month averages |
| `/api/stations/{id}/latest` | Public | Latest reading for each metric |
| `/api/metrics/` | Public | Metric definitions |
| `/api/admin/login` | Public | Exchange admin credentials for a bearer token |
| `/api/admin/profiles` | Admin | Create, list, update, and delete FINoT profiles |
| `/api/sync/`, `/api/sync/start` | Admin | Blocking or in-process background sync for a profile |
| `/api/sync/stations`, `/api/sync/readings`, `/api/sync/test` | Admin | Profile-specific operations; require `profile_id` |
| `/api/sync/status`, `/api/sync/progress` | Admin | Optional profile-scoped status |
| `/api/sync/logs`, `/api/sync/runs` | Admin | Sync history |
| `/health`, `/test` | Public | Database health and connectivity information |

Protected calls require `Authorization: Bearer <token>`. Deleting a profile keeps its stations and readings but removes the profile association; those stations cannot sync through the deleted profile.

Background synchronization runs inside the API process. Use one worker; shutdown can interrupt a run, and runs left as `running` are marked failed at the next startup so they can resume. Every `SYNC_INTERVAL_HOURS` the API starts an incremental sync of all active profiles; there is no distributed job queue.

## Tests

Point `TEST_DATABASE_URL` at a dedicated PostgreSQL database:

```powershell
$env:TEST_DATABASE_URL = 'postgresql://user:password@localhost:5432/aegeansense_test'
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

The suite creates a unique schema and removes it afterward. It tests startup, authentication, profile encryption and CRUD, public readings, aggregation, CORS, and hostile metric filter strings without contacting FINoT.

## Backup

Use `pg_dump` in the database container and copy the result out (avoids shell encoding changes on Windows):

```bash
docker compose exec db pg_dump -U aegeansense -d aegeansense -f /tmp/aegeansense_backup.sql
docker compose cp db:/tmp/aegeansense_backup.sql ./aegeansense_backup.sql
```

Backups contain application data and must not be committed. `docker compose down` stops the stack and preserves the database volume; adding `-v` deletes that volume and its data.
