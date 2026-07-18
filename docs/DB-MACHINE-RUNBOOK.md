# DB-Machine Runbook — migrations 0006/0007 + Catalyst re-ingest + Charniga ingest

Verified 2026-07-18 by rehearsal on the dev machine (audit 4): the migration
round-trip was executed against corpus-shaped seed data (16 distinct
same-named Takano-style templates + 2 exact duplicates → all 16 distinct
survived, dupes collapsed), the Catalyst crawl was dry-run live (428 URLs
collected), and the Charniga CDX enumeration was probed live (215 unique
essay URLs). Steps that need API keys could not be rehearsed and are marked.

## 1. Preflight

```bash
git status                      # expect clean
cd oly-ingestion && docker compose up -d --wait
docker exec oly-postgres psql -U oly -d oly_programming -c "select 1"
```

## 2. Backup (do not skip — migration + bulk delete ahead)

```bash
docker exec oly-postgres pg_dump -U oly -d oly_programming -Fc -f /tmp/pre_0007.dump
docker cp oly-postgres:/tmp/pre_0007.dump ./pre_0007.dump
```

## 3. Branch fixup (this clone still tracks the deleted `master`)

```bash
git fetch --prune origin        # --prune removes the stale origin/master ref
git branch -m master main
git branch -u origin/main main
git remote set-head origin -a
git pull
```

Expected: `git branch -a` shows only `main` + `remotes/origin/main`.
If `origin/main` is missing after the fetch, the clone is single-branch — fix
the refspec and re-fetch:

```bash
git config remote.origin.fetch '+refs/heads/*:refs/remotes/origin/*'
git fetch --prune origin
```

Then sync deps: `make sync`.

## 4. Migrate (0005 → 0007)

```bash
cd oly-agent
uv run alembic current          # expect 0005_athlete_timezone
uv run alembic upgrade head     # runs 0006 then 0007
uv run alembic current          # expect 0007_training_log_unique_session (head)
```

Verify nothing was lost (rehearsed: only byte-exact duplicate templates are
removed; all distinct structures survive):

```sql
SELECT count(*) FROM program_templates WHERE source_id = 2;   -- expect 16 (Takano)
```

0007 merges any legacy double-submit `training_logs` (exercises repointed to
the earliest log) — by design.

## 5. Delete old Catalyst chunks (inspect counts BEFORE COMMIT)

Old Catalyst sources are exactly `source_type='website' AND url IS NULL`
(URL population is newer than the original ingest). Run this BEFORE any
Charniga ingest so the predicate stays precise.

```sql
BEGIN;
CREATE TEMP TABLE catalyst_src AS
  SELECT id FROM sources WHERE source_type = 'website' AND url IS NULL;
SELECT count(*) FROM catalyst_src;                                 -- expect ~418
SELECT count(*) FROM knowledge_chunks
 WHERE source_id IN (SELECT id FROM catalyst_src);                 -- expect ~446
DELETE FROM knowledge_chunks
 WHERE source_id IN (SELECT id FROM catalyst_src);                 -- cascades to ingestion_chunk_log
COMMIT;
```

Do NOT delete the `sources` rows (they get url-backfilled on re-ingest) or
`programming_principles` (retained; re-extraction dedups on
UNIQUE(source_id, principle_name)).

## 6. Clear the progress file (required, or the re-run is a no-op)

```bash
rm oly-ingestion/sources/catalyst_progress.json
```

## 7. Re-ingest Catalyst  ⚠ needs OPENAI_API_KEY + ANTHROPIC_API_KEY (~$1–2)

```bash
cd oly-ingestion
PYTHONUTF8=1 uv run python ingest_web.py --limit 5    # smoke test first
PYTHONUTF8=1 uv run python ingest_web.py              # full run (~428 articles)
```

Expected: multi-chunk articles (thousands of chunks total, vs the old
446 ≈ 1/article). Transient fetch failures stay pending and retry on re-run.
Sanity check:

```sql
SELECT count(*) FROM knowledge_chunks k
JOIN sources s ON s.id = k.source_id WHERE s.source_type = 'website';
-- expect avg chunks/article well above 1
```

Optional residue cleanup — articles whose extracted title drifted create new
source rows, orphaning old ones:

```sql
SELECT id, title FROM sources s
WHERE source_type = 'website' AND url IS NULL
  AND NOT EXISTS (SELECT 1 FROM knowledge_chunks k WHERE k.source_id = s.id);
-- review before deleting; check attached principles first
```

## 8. Charniga ingest  ⚠ needs both keys

```bash
PYTHONUTF8=1 uv run python ingest_web.py --site charniga --dry-run   # expect ~215 URLs
PYTHONUTF8=1 uv run python ingest_web.py --site charniga --limit 5   # smoke
PYTHONUTF8=1 uv run python ingest_web.py --site charniga             # full run
```

CDX enumeration now retries transient Wayback 503s and a 0-URL collection
aborts loudly instead of reporting "nothing to ingest". The content selectors
are confirmed against a live snapshot (`div.entry-content` + `h1.entry-title`).
Watch for "kept pending" warnings; re-run until the pending count stabilizes.
Expected: ~215 sources with `author='Andrew Charniga'` and urls set.

## 9. Post

- Re-run the retrieval eval (both keys): `PYTHONUTF8=1 uv run python tests/test_retrieval_eval.py`
- Update the baseline table in `docs/RETRIEVAL_EVAL.md`.
- Update CLAUDE.md's corpus totals (source list + chunk counts).
- `retag_chunks.py` only if `KEYWORD_TO_TOPIC` changed.

## Not rehearsable from the dev machine (verify here)

- The embedding/principle-extraction load path at runtime (needs keys).
- Real corpus states: actual Takano rows, real duplicate training_logs,
  actual chunk counts.
- This clone's git configuration (refspec contingency above).
