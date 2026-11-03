-- Row-level security for the four registers added by migration 018.
--
-- ⚠️ RLS IS GRANTED PER TABLE, NOT PER DATABASE. Migration 013 enabled it on
-- the tables that existed then; a table created afterwards starts with no
-- policy and is readable across every project by any role holding SELECT.
-- Nothing warns. This migration is the other half of 018, and the pair must
-- never be split.
--
-- The policies are identical in shape to 013's: scoped to the session's
-- project through `app.current_project`, and fail-closed — an unscoped
-- session reads NULL, `project_code = NULL` is NULL rather than true, and
-- the result is zero rows instead of every row.

GRANT SELECT ON deliverables, risks, issues, change_requests TO spm_app;

ALTER TABLE deliverables     ENABLE ROW LEVEL SECURITY;
ALTER TABLE risks            ENABLE ROW LEVEL SECURITY;
ALTER TABLE issues           ENABLE ROW LEVEL SECURITY;
ALTER TABLE change_requests  ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS deliverables_scope ON deliverables;
CREATE POLICY deliverables_scope ON deliverables FOR SELECT TO spm_app
  USING (project_code = current_setting('app.current_project', true));

DROP POLICY IF EXISTS risks_scope ON risks;
CREATE POLICY risks_scope ON risks FOR SELECT TO spm_app
  USING (project_code = current_setting('app.current_project', true));

DROP POLICY IF EXISTS issues_scope ON issues;
CREATE POLICY issues_scope ON issues FOR SELECT TO spm_app
  USING (project_code = current_setting('app.current_project', true));

DROP POLICY IF EXISTS change_requests_scope ON change_requests;
CREATE POLICY change_requests_scope ON change_requests FOR SELECT TO spm_app
  USING (project_code = current_setting('app.current_project', true));
