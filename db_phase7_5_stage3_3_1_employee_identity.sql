-- Phase 7.5 Stage 3.3.1: Employee identity fields
-- Adds an optional middle/other-name field without changing existing names.

ALTER TABLE employees
ADD COLUMN IF NOT EXISTS middle_name TEXT;
