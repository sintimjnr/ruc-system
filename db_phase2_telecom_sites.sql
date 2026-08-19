-- RUC SYSTEM Phase 2: Telecom Site / DUID operational management.
-- Safe to run more than once. It stores only RUC operational site data
-- and keeps globe_nlz / planning_reference as tracker-reference sources.

BEGIN;

CREATE TABLE IF NOT EXISTS telecom_sites (
    id SERIAL PRIMARY KEY,
    du_id TEXT NOT NULL UNIQUE,
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    vendor VARCHAR(150),
    current_stage VARCHAR(50) DEFAULT 'Planning',
    overall_progress INTEGER DEFAULT 0,
    overall_status VARCHAR(50) DEFAULT 'Not Started',
    access_valid_from DATE,
    access_valid_until DATE,
    access_status VARCHAR(30),
    pat_status VARCHAR(30) DEFAULT 'MISSING',
    operational_team_leader VARCHAR(150),
    remarks TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'telecom_sites_progress_range'
    ) THEN
        ALTER TABLE telecom_sites
            ADD CONSTRAINT telecom_sites_progress_range
            CHECK (overall_progress BETWEEN 0 AND 100);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_telecom_sites_du_id
    ON telecom_sites(du_id);

CREATE INDEX IF NOT EXISTS idx_telecom_sites_project_id
    ON telecom_sites(project_id);

CREATE INDEX IF NOT EXISTS idx_telecom_sites_current_stage
    ON telecom_sites(current_stage);

CREATE INDEX IF NOT EXISTS idx_telecom_sites_overall_status
    ON telecom_sites(overall_status);

CREATE INDEX IF NOT EXISTS idx_telecom_sites_vendor
    ON telecom_sites(vendor);

CREATE INDEX IF NOT EXISTS idx_telecom_sites_updated_at
    ON telecom_sites(updated_at);

CREATE INDEX IF NOT EXISTS idx_globe_nlz_towerco
    ON globe_nlz(towerco);

CREATE INDEX IF NOT EXISTS idx_globe_nlz_province
    ON globe_nlz(province);

CREATE INDEX IF NOT EXISTS idx_globe_nlz_town
    ON globe_nlz(town);

CREATE INDEX IF NOT EXISTS idx_planning_reference_du_id
    ON planning_reference(du_id);

CREATE INDEX IF NOT EXISTS idx_planning_reference_municipality
    ON planning_reference(municipality);

ALTER TABLE telecom_tasks
    ALTER COLUMN status SET DEFAULT 'PENDING';

COMMIT;
