-- Row-level security: make SQL isolation STRUCTURAL rather than dependent
-- on every query remembering its WHERE clause (BRD AC09, worded as an
-- absolute prohibition on cross-project access).
--
-- Before this, a forgotten filter silently returned all 9 projects:
--     SELECT * FROM document_links;          -- 58 rows, no error
-- After it, the same careless query returns only the session's project.
--
-- WHY A SEPARATE ROLE: a table's owner bypasses RLS. The chat application
-- therefore cannot connect as spm_admin. This is also what keeps the
-- ingestion pipeline working unchanged — it keeps using spm_admin and
-- legitimately needs cross-project access to process every document.
--
-- WHY SELECT-ONLY: the BRD states the assistant is purely informational
-- and performs no write operations against any system. Granting only
-- SELECT moves that from a convention someone could break into something
-- the database enforces.

-- ---------------------------------------------------------------------
-- 1. The restricted application role
-- ---------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'spm_app') THEN
    CREATE ROLE spm_app LOGIN PASSWORD '5e5d79d8149db9624345f7cc';
  END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO spm_app;
GRANT SELECT ON projects, users, project_assignments, document_links, document_content TO spm_app;

-- ---------------------------------------------------------------------
-- 2. Enable RLS on the project-scoped content tables
-- ---------------------------------------------------------------------
ALTER TABLE projects          ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_links    ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_content  ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_assignments ENABLE ROW LEVEL SECURITY;

-- ---------------------------------------------------------------------
-- 3. Policies
--
-- current_setting(..., true) returns NULL when the variable is unset, and
-- `code = NULL` is NULL, which is not true — so a session that never sets
-- a project sees NOTHING. Fail-closed by construction: forgetting to scope
-- the session yields zero rows rather than every row.
-- ---------------------------------------------------------------------
DROP POLICY IF EXISTS project_scope ON projects;
CREATE POLICY project_scope ON projects FOR SELECT TO spm_app
  USING (code = current_setting('app.current_project', true));

DROP POLICY IF EXISTS document_links_scope ON document_links;
CREATE POLICY document_links_scope ON document_links FOR SELECT TO spm_app
  USING (project_code = current_setting('app.current_project', true));

-- document_content has no project_code of its own, so it inherits scope
-- through its parent link rather than duplicating the column.
DROP POLICY IF EXISTS document_content_scope ON document_content;
CREATE POLICY document_content_scope ON document_content FOR SELECT TO spm_app
  USING (EXISTS (
    SELECT 1 FROM document_links dl
    WHERE dl.id = document_content.document_link_id
      AND dl.project_code = current_setting('app.current_project', true)
  ));

-- Assignments are scoped by USER, not project: a project manager may read
-- their own assignments (needed to populate the project switcher) but not
-- enumerate anyone else's.
DROP POLICY IF EXISTS assignments_own_only ON project_assignments;
CREATE POLICY assignments_own_only ON project_assignments FOR SELECT TO spm_app
  USING (username = current_setting('app.current_username', true));
