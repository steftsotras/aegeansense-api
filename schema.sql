-- ============================================================
-- AegeanSense - Environmental Monitoring Database Schema
-- Updated with batch sync support and progress tracking
-- ============================================================

-- FINOT PROFILES TABLE (multi-tenant FINoT accounts)
CREATE TABLE IF NOT EXISTS finot_profiles (
    id                          SERIAL PRIMARY KEY,
    name                        TEXT NOT NULL UNIQUE,
    finot_url                   TEXT NOT NULL,
    finot_username              TEXT NOT NULL,
    finot_password_encrypted    TEXT NOT NULL,
    is_active                   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at                  TIMESTAMPTZ DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ DEFAULT NOW()
);

-- ADMIN USERS TABLE
CREATE TABLE IF NOT EXISTS admin_users (
    id              SERIAL PRIMARY KEY,
    email           TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- STATIONS TABLE
CREATE TABLE IF NOT EXISTS stations (
    id                  TEXT PRIMARY KEY,           -- FINoT entity_id
    type                TEXT NOT NULL,              -- MicroclimateStation, WeatherStation, IndoorAirQuality
    name                TEXT,
    serial_number       TEXT,
    product_id          TEXT,
    latitude            DOUBLE PRECISION,
    longitude           DOUBLE PRECISION,
    connection_status   TEXT DEFAULT 'UNKNOWN',
    last_seen           TIMESTAMPTZ,
    attributes          JSONB,                      -- Raw attributes from FINoT
    synced_at           TIMESTAMPTZ DEFAULT NOW(),
    profile_id          INTEGER REFERENCES finot_profiles(id) ON DELETE SET NULL
);

-- Backfill for databases created before profile_id existed
ALTER TABLE stations ADD COLUMN IF NOT EXISTS profile_id INTEGER REFERENCES finot_profiles(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_stations_type ON stations(type);
CREATE INDEX IF NOT EXISTS idx_stations_profile ON stations(profile_id);

-- METRICS TABLE (reference data)
CREATE TABLE IF NOT EXISTS metrics (
    name            TEXT PRIMARY KEY,
    label           TEXT NOT NULL,
    unit_code       TEXT,
    unit_symbol     TEXT,
    description     TEXT
);

-- Pre-populate metrics
INSERT INTO metrics (name, label, unit_code, unit_symbol, description) VALUES
    ('airTemperature',              'Air Temperature',      'CEL',  '°C',     'Ambient air temperature'),
    ('soilTemperature',             'Soil Temperature',     'CEL',  '°C',     'Soil temperature'),
    ('airRelativeHumidity',         'Air Humidity',         'P1',   '%',      'Relative humidity'),
    ('soilMoisture',                'Soil Moisture',        'P1',   '%',      'Soil moisture'),
    ('leafWetness',                 'Leaf Wetness',         'P1',   '%',      'Leaf wetness'),
    ('soilElectricalConductivity',  'Soil EC',              'G42',  'dS/m',   'Electrical conductivity'),
    ('soilSalinity',                'Soil Salinity',        'M1',   'ppm',    'Salinity'),
    ('soilTDS',                     'Soil TDS',             'M1',   'ppm',    'Total dissolved solids'),
    ('rain',                        'Rainfall',             'MMT',  'mm',     'Rainfall'),
    ('rainRate',                    'Rain Rate',            'H67',  'mm/h',   'Rain rate'),
    ('solarRadiation',              'Solar Radiation',      'D54',  'W/m²',   'Solar radiation'),
    ('windSpeed',                   'Wind Speed',           'MTS',  'm/s',    'Wind speed'),
    ('gustWindSpeed',               'Gust Speed',           'MTS',  'm/s',    'Gust speed'),
    ('windDirection',               'Wind Direction',       'DD',   '°',      'Wind direction'),
    ('PM10',                        'PM10',                 'GQ',   'µg/m³',  'PM10'),
    ('PM2d5',                       'PM2.5',                'GQ',   'µg/m³',  'PM2.5'),
    ('PM1d0',                       'PM1.0',                'GQ',   'µg/m³',  'PM1.0'),
    ('SO2',                         'SO₂',                  '59',   'ppb',    'Sulfur dioxide'),
    ('vocIndex',                    'VOC Index',            NULL,   NULL,     'VOC index'),
    ('ambientIlluminance',          'Illuminance',          'LUX',  'lux',    'Illuminance')
ON CONFLICT (name) DO NOTHING;

-- READINGS TABLE (time-series data)
CREATE TABLE IF NOT EXISTS readings (
    id              BIGSERIAL PRIMARY KEY,
    station_id      TEXT NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    recorded_at     TIMESTAMPTZ NOT NULL,
    metric          TEXT NOT NULL,
    value           DOUBLE PRECISION NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_readings_unique
    ON readings(station_id, recorded_at, metric);
CREATE INDEX IF NOT EXISTS idx_readings_station_time
    ON readings(station_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_readings_station_metric
    ON readings(station_id, metric, recorded_at DESC);

-- SYNC PROGRESS TABLE (tracks per-entity sync state for batch syncing)
CREATE TABLE IF NOT EXISTS sync_progress (
    id              SERIAL PRIMARY KEY,
    entity_id       TEXT NOT NULL UNIQUE REFERENCES stations(id) ON DELETE CASCADE,
    entity_type     TEXT,
    is_complete     BOOLEAN DEFAULT FALSE,          -- True if synced up to current date
    last_synced_to  TIMESTAMPTZ,                    -- Last date we synced data up to
    earliest_data   TIMESTAMPTZ,                    -- Earliest data point found
    latest_data     TIMESTAMPTZ,                    -- Most recent data point found
    total_records   INTEGER DEFAULT 0,              -- Count of records synced
    error_message   TEXT,                           -- Last error if any
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sync_progress_incomplete
    ON sync_progress(is_complete) WHERE is_complete = FALSE;

-- SYNC RUN TABLE (tracks each sync job execution)
CREATE TABLE IF NOT EXISTS sync_runs (
    id              SERIAL PRIMARY KEY,
    status          TEXT DEFAULT 'running',         -- running, completed, failed
    started_at      TIMESTAMPTZ DEFAULT NOW(),
    completed_at    TIMESTAMPTZ,
    entities_synced INTEGER DEFAULT 0,
    records_synced  INTEGER DEFAULT 0,
    error_message   TEXT
);

-- SYNC LOG TABLE (legacy, for backward compatibility)
CREATE TABLE IF NOT EXISTS sync_log (
    id              SERIAL PRIMARY KEY,
    sync_type       TEXT NOT NULL,
    started_at      TIMESTAMPTZ DEFAULT NOW(),
    completed_at    TIMESTAMPTZ,
    status          TEXT DEFAULT 'running',
    records_count   INTEGER DEFAULT 0,
    error_message   TEXT
);

-- HELPFUL VIEW: Latest reading per metric per station
CREATE OR REPLACE VIEW latest_readings AS
SELECT DISTINCT ON (station_id, metric)
    station_id, metric, value, recorded_at
FROM readings
ORDER BY station_id, metric, recorded_at DESC;

-- HELPFUL VIEW: Sync status summary
CREATE OR REPLACE VIEW sync_status_summary AS
SELECT
    (SELECT COUNT(*) FROM stations) as total_stations,
    (SELECT COUNT(*) FROM sync_progress WHERE is_complete = TRUE) as fully_synced,
    (SELECT COUNT(*) FROM sync_progress WHERE is_complete = FALSE) as pending_sync,
    (SELECT COUNT(*) FROM readings) as total_readings,
    (SELECT MAX(completed_at) FROM sync_runs WHERE status = 'completed') as last_successful_sync;
