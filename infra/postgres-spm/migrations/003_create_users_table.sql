-- Mock AD users. Real AD is an external dependency not available in dev
-- (see SOLUTION_SCOPE.md's "Practical note for a demo/development build").
CREATE TABLE IF NOT EXISTS users (
  username        TEXT PRIMARY KEY,   -- AD-style, e.g. "fahad.alqahtani"
  full_name_ar    TEXT NOT NULL,
  full_name_en    TEXT NOT NULL,
  email           TEXT NOT NULL UNIQUE,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
