-- Let a user see the projects they are assigned to, by name.
--
-- WHY THIS EXISTS
--
-- Migration 013 scoped `projects` by `app.current_project`, which is exactly
-- right for reads inside a session. It leaves one thing impossible: the
-- project switcher. Before a session exists there is no current project, so
-- a user with only `app.current_username` set sees ZERO rows in `projects` —
-- verified live on 2026-08-09. `project_assignments` can list their project
-- CODES, but it holds no names, so the switcher could only offer
-- "PRJ-02 / PRJ-05 / PRJ-06" with no idea what those are.
--
-- The alternative was to loop one scoped transaction per assigned project
-- and read each name separately. That works, and it is a workaround: it
-- moves an access rule into application code, which is precisely the
-- weakness 013 was written to remove. A second policy states the rule where
-- the other access rules already live.
--
-- WHAT THIS DOES *NOT* DO — read this before assuming it closes anything
--
-- Postgres combines multiple permissive policies with OR. `project_scope`
-- from 013 still applies, so a session scoped to a project it was never
-- assigned still sees that project. That is not a regression and this
-- migration does not change it: RLS faithfully serves whatever scope the
-- session declares, and validating the requested project against
-- `project_assignments` before opening a session remains APPLICATION code
-- (the session gate). See NEXT_BUILD_CHAT_BACKEND.md section 2.
--
-- What this policy adds is strictly narrower than that: with a username and
-- NO project set, a user can read the rows for their own assigned projects.
--
-- FAIL-CLOSED, like everything else here: `current_setting(..., true)`
-- returns NULL when unset, `username = NULL` is never true, so an unscoped
-- session still sees nothing. And because `project_assignments` carries its
-- own RLS policy scoped to the same username, the subquery below is itself
-- constrained — a user cannot reach this policy through someone else's
-- assignments.

DROP POLICY IF EXISTS projects_assigned_to_user ON projects;
CREATE POLICY projects_assigned_to_user ON projects FOR SELECT TO spm_app
  USING (EXISTS (
    SELECT 1 FROM project_assignments pa
    WHERE pa.project_code = projects.code
      AND pa.username = current_setting('app.current_username', true)
  ));
