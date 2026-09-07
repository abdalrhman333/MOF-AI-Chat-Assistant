-- Give `projects` a real last-updated timestamp.
--
-- §3.3 requires the assistant to attribute each source WITH ITS DATE:
-- "SPM, updated 2026-08-01" against "Weekly Status Report, 2026-07-12" is
-- often the whole answer a project manager needs when the two disagree.
--
-- The table only had `created_at`, so the record was attributed as "record
-- created …" — honest, but the wrong fact. §16b recorded the gap and
-- deliberately refused to invent a timestamp: this is a *mock* SPM, and
-- manufacturing provenance the real system never supplied would have made
-- the dated comparison look trustworthy while being fiction.
--
-- What changed is that the column can now be maintained honestly. A trigger
-- sets it on every UPDATE, so the value describes something that actually
-- happened to the row rather than being written once and left to rot.
--
-- ⚠️ **Backfilled to `created_at`, not to now().** A row nobody has touched
-- since it was created was last updated when it was created. Stamping every
-- row with today's date would assert that all nine projects were revised
-- this morning — the exact kind of invented provenance §16b refused.
--
-- ⚠️ **A real SPM integration must still carry its own value.** This makes
-- OUR copy truthful about when we changed it; it cannot know when the
-- system of record changed upstream. When a live SPM API arrives, that
-- field replaces this one rather than sitting beside it.

ALTER TABLE projects ADD COLUMN IF NOT EXISTS updated_at timestamptz;

UPDATE projects SET updated_at = created_at WHERE updated_at IS NULL;

ALTER TABLE projects ALTER COLUMN updated_at SET DEFAULT now();
ALTER TABLE projects ALTER COLUMN updated_at SET NOT NULL;

CREATE OR REPLACE FUNCTION set_projects_updated_at() RETURNS trigger AS $$
BEGIN
    -- `IS DISTINCT FROM` rather than `<>` so a NULL-to-NULL no-op update
    -- does not count as a change. An UPDATE that changes nothing should not
    -- move a timestamp the assistant then quotes as provenance.
    IF NEW IS DISTINCT FROM OLD THEN
        NEW.updated_at = now();
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS projects_set_updated_at ON projects;
CREATE TRIGGER projects_set_updated_at
    BEFORE UPDATE ON projects
    FOR EACH ROW EXECUTE FUNCTION set_projects_updated_at();
