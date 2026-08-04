-- Which users can access which projects. This table IS the access-control
-- boundary (BRD AC09: absolutely no cross-project access) — a project with
-- no row here is invisible to every user, by construction.
CREATE TABLE IF NOT EXISTS project_assignments (
  username        TEXT NOT NULL REFERENCES users(username),
  project_code    TEXT NOT NULL REFERENCES projects(code),
  assigned_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (username, project_code)
);
