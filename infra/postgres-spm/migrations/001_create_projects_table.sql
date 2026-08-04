-- SPM projects table. Additive-only migration, matching mock-data/spm_manifest.json's shape.
CREATE TABLE IF NOT EXISTS projects (
  code                       TEXT PRIMARY KEY,
  name_ar                    TEXT NOT NULL,
  name_en                    TEXT NOT NULL,
  description_ar             TEXT,
  description_en             TEXT,
  status                     TEXT NOT NULL,
  start_date                 DATE,
  planned_end_date           DATE,
  budget_sar                 NUMERIC,
  sponsoring_department_ar   TEXT,
  sponsoring_department_en   TEXT,
  created_at                 TIMESTAMPTZ NOT NULL DEFAULT now()
);
