-- RUC SYSTEM Phase 3: Personnel dossier and safety document management.
-- Safe to run more than once. It preserves existing employee/safety data
-- while allowing safety_documents to keep history records.

BEGIN;

ALTER TABLE safety_documents
    ADD COLUMN IF NOT EXISTS is_current BOOLEAN DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS original_filename TEXT,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP;

UPDATE safety_documents
SET is_current = TRUE
WHERE is_current IS NULL;

DROP INDEX IF EXISTS uq_safety_documents_employee_type;

CREATE UNIQUE INDEX IF NOT EXISTS uq_safety_documents_current_employee_type
    ON safety_documents(employee_id, document_type)
    WHERE is_current = TRUE;

CREATE INDEX IF NOT EXISTS idx_safety_documents_employee_id
    ON safety_documents(employee_id);

CREATE INDEX IF NOT EXISTS idx_safety_documents_current
    ON safety_documents(is_current);

CREATE INDEX IF NOT EXISTS idx_safety_documents_employee_type_current
    ON safety_documents(employee_id, document_type, is_current);

CREATE INDEX IF NOT EXISTS idx_site_assignments_employee_duid
    ON site_assignments(employee_id, du_id);

COMMIT;
