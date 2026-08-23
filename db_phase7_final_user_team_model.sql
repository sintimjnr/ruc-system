-- Phase 7 revision: final user, team, access, and field management model.
-- Intended order: run db_phase7_rbac_audit.sql first, then this final model migration.
-- Additive/idempotent where practical. Does not remove business records.
-- Final production login roles are super_admin, hr, and team_leader.

ALTER TABLE admins
    ADD COLUMN IF NOT EXISTS employee_id INTEGER,
    ADD COLUMN IF NOT EXISTS created_by_admin_id INTEGER,
    ADD COLUMN IF NOT EXISTS activated_by_admin_id INTEGER,
    ADD COLUMN IF NOT EXISTS deactivated_by_admin_id INTEGER,
    ADD COLUMN IF NOT EXISTS status_changed_at TIMESTAMP WITHOUT TIME ZONE;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'admins_employee_id_fkey'
          AND conrelid = 'admins'::regclass
    ) THEN
        ALTER TABLE admins
            ADD CONSTRAINT admins_employee_id_fkey
            FOREIGN KEY (employee_id) REFERENCES employees(id) ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'admins_created_by_admin_id_fkey'
          AND conrelid = 'admins'::regclass
    ) THEN
        ALTER TABLE admins
            ADD CONSTRAINT admins_created_by_admin_id_fkey
            FOREIGN KEY (created_by_admin_id) REFERENCES admins(id) ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'admins_activated_by_admin_id_fkey'
          AND conrelid = 'admins'::regclass
    ) THEN
        ALTER TABLE admins
            ADD CONSTRAINT admins_activated_by_admin_id_fkey
            FOREIGN KEY (activated_by_admin_id) REFERENCES admins(id) ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'admins_deactivated_by_admin_id_fkey'
          AND conrelid = 'admins'::regclass
    ) THEN
        ALTER TABLE admins
            ADD CONSTRAINT admins_deactivated_by_admin_id_fkey
            FOREIGN KEY (deactivated_by_admin_id) REFERENCES admins(id) ON DELETE SET NULL;
    END IF;
END
$$;

ALTER TABLE admins
    DROP CONSTRAINT IF EXISTS admins_role_check;

UPDATE admins
SET role = 'hr'
WHERE role IS NULL OR trim(role) = '';

UPDATE admins
SET role = CASE
    WHEN role IN ('admin', 'project_manager', 'viewer') THEN 'hr'
    WHEN role = 'site_supervisor' THEN 'team_leader'
    ELSE role
END
WHERE role IN ('admin', 'project_manager', 'site_supervisor', 'viewer');

UPDATE admins
SET role = 'hr'
WHERE role NOT IN ('super_admin', 'hr', 'team_leader');

ALTER TABLE admins
    ADD CONSTRAINT admins_role_check
    CHECK (role IN ('super_admin', 'hr', 'team_leader'));

CREATE INDEX IF NOT EXISTS idx_admins_role_active
    ON admins (role, active);

CREATE INDEX IF NOT EXISTS idx_admins_employee_id
    ON admins (employee_id);

CREATE INDEX IF NOT EXISTS idx_admins_created_by_admin_id
    ON admins (created_by_admin_id);

CREATE INDEX IF NOT EXISTS idx_admins_activated_by_admin_id
    ON admins (activated_by_admin_id);

CREATE TABLE IF NOT EXISTS teams (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    du_id TEXT,
    team_leader_admin_id INTEGER REFERENCES admins(id) ON DELETE SET NULL,
    team_leader_employee_id INTEGER REFERENCES employees(id) ON DELETE SET NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_by_admin_id INTEGER REFERENCES admins(id) ON DELETE SET NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_teams_project_id
    ON teams (project_id);

CREATE INDEX IF NOT EXISTS idx_teams_du_id
    ON teams (du_id);

CREATE INDEX IF NOT EXISTS idx_teams_team_leader_admin_id
    ON teams (team_leader_admin_id);

CREATE INDEX IF NOT EXISTS idx_teams_active
    ON teams (active);

CREATE TABLE IF NOT EXISTS team_memberships (
    id SERIAL PRIMARY KEY,
    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    start_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    end_at TIMESTAMP WITHOUT TIME ZONE,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    assigned_by_admin_id INTEGER REFERENCES admins(id) ON DELETE SET NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_team_memberships_team_id
    ON team_memberships (team_id);

CREATE INDEX IF NOT EXISTS idx_team_memberships_employee_id
    ON team_memberships (employee_id);

CREATE INDEX IF NOT EXISTS idx_team_memberships_active
    ON team_memberships (active);

CREATE UNIQUE INDEX IF NOT EXISTS idx_team_memberships_one_active_employee
    ON team_memberships (employee_id)
    WHERE active IS TRUE;
