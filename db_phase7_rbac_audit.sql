-- Phase 7: User management, role-based access control, and audit trail.
-- Intended order: run this baseline first, then db_phase7_final_user_team_model.sql.
-- Safe to run more than once. Does not drop or recreate business tables.
-- This baseline permits final roles plus legacy roles so reruns do not demote
-- hr/team_leader accounts back to admin.

ALTER TABLE admins
    ADD COLUMN IF NOT EXISTS active BOOLEAN DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    ADD COLUMN IF NOT EXISTS last_login TIMESTAMP WITHOUT TIME ZONE;

UPDATE admins
SET active = TRUE
WHERE active IS NULL;

UPDATE admins
SET created_at = CURRENT_TIMESTAMP
WHERE created_at IS NULL;

UPDATE admins
SET updated_at = CURRENT_TIMESTAMP
WHERE updated_at IS NULL;

UPDATE admins
SET role = 'admin'
WHERE role IS NULL OR trim(role) = '';

UPDATE admins
SET role = 'admin'
WHERE role NOT IN (
    'super_admin',
    'hr',
    'team_leader',
    'admin',
    'project_manager',
    'site_supervisor',
    'viewer'
);

UPDATE admins
SET role = 'super_admin'
WHERE id = (
    SELECT MIN(id)
    FROM admins
    WHERE username IS NOT NULL
)
AND NOT EXISTS (
    SELECT 1
    FROM admins
    WHERE role = 'super_admin'
      AND active IS TRUE
);

ALTER TABLE admins
    ALTER COLUMN active SET NOT NULL,
    ALTER COLUMN role SET NOT NULL,
    ALTER COLUMN created_at SET DEFAULT CURRENT_TIMESTAMP,
    ALTER COLUMN updated_at SET DEFAULT CURRENT_TIMESTAMP;

CREATE UNIQUE INDEX IF NOT EXISTS admins_username_lower_unique
    ON admins (LOWER(username))
    WHERE username IS NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'admins_role_check'
          AND conrelid = 'admins'::regclass
    ) THEN
        ALTER TABLE admins
            ADD CONSTRAINT admins_role_check
            CHECK (
                role IN (
                    'super_admin',
                    'hr',
                    'team_leader',
                    'admin',
                    'project_manager',
                    'site_supervisor',
                    'viewer'
                )
            );
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS audit_logs (
    id SERIAL PRIMARY KEY,
    admin_id INTEGER REFERENCES admins(id) ON DELETE SET NULL,
    username_snapshot TEXT,
    role_snapshot VARCHAR(50),
    action VARCHAR(120) NOT NULL,
    entity_type VARCHAR(120),
    entity_id TEXT,
    description TEXT,
    ip_address TEXT,
    http_method VARCHAR(12),
    route TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at
    ON audit_logs (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_logs_username
    ON audit_logs (username_snapshot);

CREATE INDEX IF NOT EXISTS idx_audit_logs_action
    ON audit_logs (action);

CREATE INDEX IF NOT EXISTS idx_audit_logs_entity_type
    ON audit_logs (entity_type);
