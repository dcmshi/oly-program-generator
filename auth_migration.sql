-- ============================================================
-- Authentication migration — run AFTER schema.sql + athlete_schema.sql
-- ============================================================
-- Adds username / password_hash to the athletes table.
-- After applying, set credentials with:
--   cd oly-agent
--   PYTHONUTF8=1 uv run python setup_auth.py --athlete-id 1 --username david --password <pw>
-- ============================================================

ALTER TABLE athletes ADD COLUMN IF NOT EXISTS username VARCHAR(100) UNIQUE;
ALTER TABLE athletes ADD COLUMN IF NOT EXISTS password_hash VARCHAR(200);
