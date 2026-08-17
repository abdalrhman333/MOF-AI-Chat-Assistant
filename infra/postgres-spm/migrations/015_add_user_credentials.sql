-- Demo sign-in credentials for the four mock users.
--
-- ⚠️ THIS IS NOT AUTHENTICATION. Real AD SSO and OTP are external
-- dependencies that do not exist in this environment, and the BRD's own
-- practical note for a demo build says to simulate what can be simulated
-- honestly and flag the rest. A password column lets the demo show a real
-- credential check and a real "who am I" flow; it does not make this a
-- secure identity system, and nothing here should be carried into
-- production.
--
-- Stored as PBKDF2-HMAC-SHA256, 240,000 iterations, per-user random salt,
-- in a self-describing format so the verifier never has to assume
-- parameters:
--
--     pbkdf2_sha256$<iterations>$<base64 salt>$<base64 hash>
--
-- Chosen because it needs nothing but `hashlib` from the standard library
-- — the same constraint that keeps `apply_migrations.py` dependency-free —
-- and because storing a plaintext password, even a demo one, teaches the
-- wrong shape to whoever reads this next.
--
-- The demo password is the same for all four users and is recorded in
-- Documentation/INFRASTRUCTURE.md. That is deliberate: these are fictional
-- users in a mock dataset on the owner's own machine, and a demo nobody can
-- sign in to is not a demo. It is NOT a secret in the sense of
-- infra/.env, and it must never become one.

ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash text;

-- Recorded so a later real-AD integration can tell a seeded demo account
-- from a provisioned one without guessing from the hash format.
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_demo_account boolean NOT NULL DEFAULT false;

UPDATE users SET password_hash = v.hash, is_demo_account = true
FROM (VALUES
  ('fahad.alqahtani',   'pbkdf2_sha256$240000$Cvr6Oh95/M/wNbVKBwyf5Q==$nvxFX4vdFVp7eNP4qMjhAfYdZBkmJ8GpUwStSCJTfdo='),
  ('noura.alharbi',     'pbkdf2_sha256$240000$c5GqjFPqRBG7XqFrueZAfg==$mK9Nv6/YJ5W5Nsp6vz/etd4DDBbd/g6o9khjsQ/hgp0='),
  ('abdullah.alshehri', 'pbkdf2_sha256$240000$RxnpqoCdqQQPrUyE571ZjQ==$2FgcBuZkSNlBZIz2d2sWKr5ZkkVMVUbpoUq+KAbNid8='),
  ('reem.aldosari',     'pbkdf2_sha256$240000$1KL0oZLtmez1rQ14GQLDJA==$g2MNQ+o49CYsRzaw9PVoDfv23xg9C8RqtJZOArx651s=')
) AS v(username, hash)
WHERE users.username = v.username;

-- The application looks users up by email, because that is what the sign-in
-- screen asks for. Already UNIQUE; this makes the lookup an index scan.
CREATE INDEX IF NOT EXISTS users_email_lower_idx ON users (lower(email));
