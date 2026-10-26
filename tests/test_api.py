"""Integration checks against a disposable PostgreSQL database.

Set TEST_DATABASE_URL to a dedicated test database. The suite uses a unique
schema and drops only that schema when finished. No FINoT calls are made.
"""
import os
import secrets
import unittest
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock, Mock

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


class ApiIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database_url = os.environ.get("TEST_DATABASE_URL")
        if not database_url:
            raise unittest.SkipTest("Set TEST_DATABASE_URL to run PostgreSQL integration checks")
        cls.schema = "test_" + secrets.token_hex(8)
        cls.control = create_engine(database_url)
        with cls.control.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        url = make_url(database_url).update_query_dict({"options": f"-csearch_path={cls.schema}"})
        cls.environment = patch.dict(os.environ, {
            "DATABASE_URL": url.render_as_string(hide_password=False),
            "JWT_SECRET": secrets.token_hex(32),
            "CREDENTIAL_ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "ADMIN_EMAIL": "test@example.invalid",
            "ADMIN_PASSWORD": secrets.token_urlsafe(24),
            "CORS_ORIGINS": "http://localhost:3000,http://127.0.0.1:3000",
            "SYNC_INTERVAL_HOURS": "0",
        })
        cls.environment.start()
        from app.main import app
        from app.database import engine, SessionLocal
        from app.models import Station, Reading
        cls.app_engine = engine
        cls.client = TestClient(app)
        cls.client.__enter__()
        with SessionLocal() as db:
            db.add(Station(id="test-station", type="WeatherStation", name="Test station"))
            db.commit()
            db.add_all([
                Reading(station_id="test-station", metric="airTemperature", value=value,
                        recorded_at=datetime(2026, 1, 1, hour, tzinfo=timezone.utc))
                for hour, value in [(0, 20), (1, 24)]
            ])
            db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.app_engine.dispose()
        with cls.control.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{cls.schema}" CASCADE'))
        cls.control.dispose()
        cls.environment.stop()

    def login(self):
        response = self.client.post("/api/admin/login", json={
            "email": os.environ["ADMIN_EMAIL"], "password": os.environ["ADMIN_PASSWORD"],
        })
        self.assertEqual(response.status_code, 200, response.text)
        return {"Authorization": "Bearer " + response.json()["access_token"]}

    def test_public_data_and_aggregation(self):
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertGreater(len(self.client.get("/api/metrics").json()), 0)
        self.assertEqual(len(self.client.get("/api/stations/").json()), 1)
        latest = self.client.get("/api/stations/test-station/latest").json()
        self.assertEqual(latest["airTemperature"]["value"], 24)
        for aggregation, expected in [(None, [20, 24]), ("hour", [20, 24]), ("day", [22]), ("month", [22])]:
            params = {"from": "2026-01-01T00:00:00Z", "to": "2026-01-02T00:00:00Z", "metrics": "airTemperature"}
            if aggregation:
                params["agg"] = aggregation
            response = self.client.get("/api/stations/test-station/readings", params=params)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual([point["value"] for point in response.json()["series"][0]["data"]], expected)
        self.assertEqual(self.client.get("/api/stations/missing").status_code, 404)

    def test_metric_filter_treats_sql_as_data(self):
        for metric in ["unknown", "airTemperature'", "x'); DROP TABLE readings; --", 'x" OR 1=1 --']:
            response = self.client.get("/api/stations/test-station/readings", params={
                "metrics": metric, "from": "2026-01-01T00:00:00Z", "to": "2026-01-02T00:00:00Z",
            })
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["series"], [])
        self.assertEqual(self.client.get("/api/stations/test-station/latest").status_code, 200)

    def test_auth_and_profile_lifecycle(self):
        self.assertIn(self.client.get("/api/admin/profiles").status_code, (401, 403))
        self.assertIn(self.client.post("/api/sync/start?profile_id=1").status_code, (401, 403))
        self.assertEqual(self.client.post("/api/admin/login", json={"email": "test@example.invalid", "password": "wrong"}).status_code, 401)
        headers = self.login()
        response = self.client.post("/api/admin/profiles", headers=headers, json={
            "name": "Integration test", "finot_url": "https://example.invalid",
            "finot_username": "test", "finot_password": "not-a-real-credential",
        })
        self.assertEqual(response.status_code, 200, response.text)
        profile = response.json()
        self.assertNotIn("finot_password", profile)
        self.assertNotIn("finot_password_encrypted", profile)
        from app.database import SessionLocal
        from app.models import FinotProfile
        from app.services.crypto import decrypt_password
        with SessionLocal() as db:
            stored = db.get(FinotProfile, profile["id"]).finot_password_encrypted
            self.assertNotEqual(stored, "not-a-real-credential")
            self.assertEqual(decrypt_password(stored), "not-a-real-credential")
        path = f'/api/admin/profiles/{profile["id"]}'
        self.assertEqual(self.client.patch(path, headers=headers, json={"name": "Updated"}).status_code, 200)
        self.assertEqual(self.client.get(path, headers=headers).json()["name"], "Updated")
        self.assertEqual(self.client.delete(path, headers=headers).status_code, 200)
        self.assertEqual(self.client.get(path, headers=headers).status_code, 404)

    def test_cors(self):
        response = self.client.options("/api/stations/", headers={
            "Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET",
        })
        self.assertEqual(response.headers.get("access-control-allow-origin"), "http://localhost:3000")

    def test_admin_profile_errors_and_connection_contract(self):
        headers = self.login()
        ids = []
        try:
            for name in ["Admin check A", "Admin check B"]:
                response = self.client.post("/api/admin/profiles", headers=headers, json={
                    "name": name, "finot_url": "https://example.invalid",
                    "finot_username": "test", "finot_password": "test-only",
                })
                self.assertEqual(response.status_code, 200, response.text)
                ids.append(response.json()["id"])
            path = f"/api/admin/profiles/{ids[1]}"
            self.assertEqual(self.client.patch(path, headers=headers, json={"name": "Admin check A"}).status_code, 409)
            self.assertEqual(self.client.patch(path, headers=headers, json={"finot_password": None}).status_code, 422)
            self.assertEqual(self.client.get(path, headers=headers).json()["name"], "Admin check B")
            for success in [True, False]:
                client = AsyncMock()
                client.test_connection.return_value = {"success": success}
                with patch("app.routers.sync.client_from_profile", return_value=client):
                    response = self.client.post(f"/api/sync/test?profile_id={ids[0]}", headers=headers)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(response.json()["configured"])
                self.assertEqual(response.json()["success"], success)
            task = Mock()
            task.done.return_value = False
            with patch("app.routers.sync._sync_task", task):
                status = self.client.get("/api/sync/status", headers=headers)
                self.assertTrue(status.json()["sync_running"])
                response = self.client.post(f"/api/sync/start?profile_id={ids[0]}", headers=headers)
                self.assertEqual(response.json()["status"], "already_running")
        finally:
            for profile_id in ids:
                self.client.delete(f"/api/admin/profiles/{profile_id}", headers=headers)

    def test_sync_resumes_complete_stations_and_retries_failed_batches(self):
        from app.database import SessionLocal
        from app.models import FinotProfile, Station, SyncProgress, SyncRun
        from app.services.sync_service import sync_readings_batch
        from app.services.crypto import encrypt_password
        cursor = datetime.now(timezone.utc) - timedelta(days=1)
        with SessionLocal() as db:
            profile = FinotProfile(name="Sync regression", finot_url="https://example.invalid",
                                   finot_username="test", finot_password_encrypted=encrypt_password("test-only"))
            db.add(profile)
            db.flush()
            station = Station(id="sync-regression", type="WeatherStation", profile_id=profile.id)
            db.add(station)
            db.flush()
            progress = SyncProgress(entity_id=station.id, is_complete=True, last_synced_to=cursor)
            db.add(progress)
            db.commit()
            client = AsyncMock()
            client.fetch_historical_data.side_effect = RuntimeError("Simulated FINoT failure")
            with patch("app.services.sync_service.client_from_profile", return_value=client):
                asyncio.run(sync_readings_batch(db, profile))
            self.assertEqual(client.fetch_historical_data.await_count, 1)
            db.refresh(progress)
            self.assertEqual(progress.last_synced_to, cursor)
            self.assertFalse(progress.is_complete)
            self.assertIn("Simulated FINoT failure", progress.error_message)
            run = db.query(SyncRun).order_by(SyncRun.id.desc()).first()
            self.assertEqual(run.status, "failed")
            client.fetch_historical_data.side_effect = None
            client.fetch_historical_data.return_value = {"index": [], "attributes": []}
            with patch("app.services.sync_service.client_from_profile", return_value=client):
                asyncio.run(sync_readings_batch(db, profile))
            # Resume re-fetches an overlap window to catch late-arriving readings.
            resumed_from = client.fetch_historical_data.await_args.kwargs["from_date"]
            self.assertLess(resumed_from, cursor)
            db.refresh(progress)
            self.assertTrue(progress.is_complete)
            self.assertGreater(progress.last_synced_to, cursor)
            self.assertIsNone(progress.error_message)
            db.delete(station)
            db.delete(profile)
            db.commit()

    def test_interrupted_sync_does_not_block_new_syncs(self):
        from app.database import SessionLocal
        from app.models import SyncRun
        from app.services.sync_service import recover_interrupted_syncs
        with SessionLocal() as db:
            run = SyncRun(status="running", started_at=datetime.now(timezone.utc))
            db.add(run)
            db.commit()
            self.assertGreaterEqual(recover_interrupted_syncs(db), 1)
            db.refresh(run)
            self.assertEqual(run.status, "failed")
            self.assertIsNotNone(run.completed_at)
            self.assertEqual(db.query(SyncRun).filter(SyncRun.status == "running").count(), 0)

    def test_admin_password_rotates_from_environment(self):
        from app import main
        old_password = os.environ["ADMIN_PASSWORD"]
        new_password = secrets.token_urlsafe(24)
        with patch.object(main.settings, "admin_password", new_password):
            main._sync_bootstrap_admin()
        try:
            for password, expected in [(old_password, 401), (new_password, 200)]:
                response = self.client.post("/api/admin/login", json={
                    "email": os.environ["ADMIN_EMAIL"], "password": password,
                })
                self.assertEqual(response.status_code, expected, response.text)
        finally:
            main._sync_bootstrap_admin()
        self.login()

    def test_placeholder_jwt_secret_is_rejected(self):
        from app.config import Settings
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            Settings(jwt_secret="change-me-in-production")


if __name__ == "__main__":
    unittest.main()
