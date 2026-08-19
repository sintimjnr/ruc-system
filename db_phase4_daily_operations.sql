BEGIN;

CREATE TABLE IF NOT EXISTS daily_site_logs (
    id SERIAL PRIMARY KEY,
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    duid TEXT NOT NULL,
    report_date DATE NOT NULL,
    current_stage VARCHAR(50) NOT NULL DEFAULT 'Planning',
    progress_before INTEGER NOT NULL DEFAULT 0,
    progress_after INTEGER NOT NULL DEFAULT 0,
    work_completed TEXT,
    blocker_category VARCHAR(100),
    blockers TEXT,
    next_day_plan TEXT,
    general_notes TEXT,
    weather_notes TEXT,
    submitted_by TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT daily_site_logs_unique_duid_date UNIQUE (duid, report_date),
    CONSTRAINT daily_site_logs_progress_before_range CHECK (progress_before >= 0 AND progress_before <= 100),
    CONSTRAINT daily_site_logs_progress_after_range CHECK (progress_after >= 0 AND progress_after <= 100)
);

CREATE TABLE IF NOT EXISTS daily_attendance (
    id SERIAL PRIMARY KEY,
    daily_log_id INTEGER NOT NULL REFERENCES daily_site_logs(id) ON DELETE CASCADE,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    attendance_status VARCHAR(30) NOT NULL DEFAULT 'Present',
    time_in TIME,
    time_out TIME,
    role_at_site VARCHAR(100),
    safety_status_snapshot VARCHAR(30),
    remarks TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT daily_attendance_unique_log_employee UNIQUE (daily_log_id, employee_id),
    CONSTRAINT daily_attendance_status_check CHECK (attendance_status IN ('Present','Absent','Late','Excused')),
    CONSTRAINT daily_attendance_time_order CHECK (time_in IS NULL OR time_out IS NULL OR time_out >= time_in)
);

CREATE TABLE IF NOT EXISTS daily_log_files (
    id SERIAL PRIMARY KEY,
    daily_log_id INTEGER NOT NULL REFERENCES daily_site_logs(id) ON DELETE CASCADE,
    file_type VARCHAR(30) NOT NULL DEFAULT 'PHOTO',
    file_path TEXT NOT NULL,
    original_filename TEXT,
    caption TEXT,
    uploaded_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT daily_log_files_type_check CHECK (file_type IN ('PHOTO','DOCUMENT'))
);

CREATE INDEX IF NOT EXISTS idx_daily_site_logs_duid
    ON daily_site_logs(duid);

CREATE INDEX IF NOT EXISTS idx_daily_site_logs_report_date
    ON daily_site_logs(report_date);

CREATE INDEX IF NOT EXISTS idx_daily_site_logs_project_id
    ON daily_site_logs(project_id);

CREATE INDEX IF NOT EXISTS idx_daily_site_logs_stage
    ON daily_site_logs(current_stage);

CREATE INDEX IF NOT EXISTS idx_daily_site_logs_submitted_by
    ON daily_site_logs(submitted_by);

CREATE INDEX IF NOT EXISTS idx_daily_attendance_daily_log_id
    ON daily_attendance(daily_log_id);

CREATE INDEX IF NOT EXISTS idx_daily_attendance_employee_id
    ON daily_attendance(employee_id);

CREATE INDEX IF NOT EXISTS idx_daily_attendance_status
    ON daily_attendance(attendance_status);

CREATE INDEX IF NOT EXISTS idx_daily_log_files_daily_log_id
    ON daily_log_files(daily_log_id);

COMMIT;
