-- The four registers BRD v1.1 §5.3.6 names as inputs: risks, issues,
-- change requests and deliverables. Until now the assistant could only
-- reach them as prose, because the corpus happens to contain Risk Register
-- *documents*. That answers "what does the risk register say"; it cannot
-- answer "which risks are open and overdue", because a sentence has no
-- due date to compare.
--
-- Field names and types follow the SPM input data schema v0.3, sheet
-- "UC3 Enterprise Assistant", section 3 — snake_cased to match the rest of
-- this database. The canonical camelCase name is in a comment where the two
-- differ enough to matter.
--
-- ⚠️ CHOICE FIELDS ARE FREE TEXT, deliberately, exactly as document_links
-- .doc_type is. The sheet marks status, priority, riskLevel and the rest as
-- SharePoint `choice`, but the actual value lists are still TBC (open
-- question 2 on the sheet). A CHECK constraint guessed now would reject
-- real SPM data later, and a document that cannot be stored is worse than
-- one stored with an unfamiliar status.
--
-- ⚠️ No RLS here. Migration 019 adds it. A register created without a
-- policy is readable across every project, so the two steps are kept
-- separate and visible rather than buried together.

-- ---------------------------------------------------------------------
-- Deliverables (DEL)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS deliverables (
  deliverable_id        INTEGER PRIMARY KEY,
  project_code          TEXT NOT NULL REFERENCES projects(code),
  deliverable_name      TEXT NOT NULL,
  deliverable_type      TEXT,
  -- FK to milestones once that table exists; the schedule entities (SCH,
  -- MIL) are a separate piece of work, so this is a plain column for now
  -- rather than a constraint pointing at nothing.
  milestone_id          INTEGER,
  weight_pct            NUMERIC,          -- fraction 0-1, per the sheet
  committed_date        DATE,
  delivered             BOOLEAN NOT NULL DEFAULT false,
  actual_delivery_date  DATE,
  status                TEXT NOT NULL,
  provider_requirements TEXT,
  acceptance_status     TEXT,
  reviewer_notes        TEXT,
  modified_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  deletion_status       TEXT NOT NULL DEFAULT 'Active',
  CHECK (weight_pct IS NULL OR (weight_pct >= 0 AND weight_pct <= 1))
);
CREATE INDEX IF NOT EXISTS deliverables_project_idx
  ON deliverables (project_code);

-- ---------------------------------------------------------------------
-- Risks (RSK)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS risks (
  risk_id          INTEGER PRIMARY KEY,
  project_code     TEXT NOT NULL REFERENCES projects(code),
  risk_title       TEXT NOT NULL,
  description      TEXT NOT NULL,
  category         TEXT,
  probability      TEXT,
  impact           TEXT,
  risk_level       TEXT NOT NULL,
  risk_owner       TEXT NOT NULL,        -- person: display name in this mock
  due_date         DATE,
  status           TEXT NOT NULL,
  mitigation_plan  TEXT NOT NULL,
  closure_notes    TEXT,
  -- 'Manual' or 'AI'. Named on the sheet so an AI-created risk can always
  -- be told from one a person entered.
  source           TEXT NOT NULL DEFAULT 'Manual'
);
CREATE INDEX IF NOT EXISTS risks_project_idx ON risks (project_code);

-- ---------------------------------------------------------------------
-- Issues (ISS)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS issues (
  issue_id       INTEGER PRIMARY KEY,
  project_code   TEXT NOT NULL REFERENCES projects(code),
  issue_title    TEXT NOT NULL,
  description    TEXT NOT NULL,
  priority       TEXT NOT NULL,
  owner          TEXT NOT NULL,          -- person: display name in this mock
  status         TEXT NOT NULL,
  resolution     TEXT,
  raised_date    DATE,
  resolved_date  DATE
);
CREATE INDEX IF NOT EXISTS issues_project_idx ON issues (project_code);

-- ---------------------------------------------------------------------
-- Change requests (CR)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS change_requests (
  cr_id                 INTEGER PRIMARY KEY,
  project_code          TEXT NOT NULL REFERENCES projects(code),
  cr_title              TEXT NOT NULL,
  cr_type               TEXT NOT NULL,   -- Scope / Schedule / Budget
  justification         TEXT NOT NULL,
  impact_description    TEXT,
  schedule_impact_days  INTEGER,
  status                TEXT NOT NULL,
  requested_date        DATE,
  decision_date         DATE
);
CREATE INDEX IF NOT EXISTS change_requests_project_idx
  ON change_requests (project_code);
