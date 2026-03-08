# tests/test_feedback.py
"""
Unit tests for feedback.py — compute_outcome() and save_outcome().

Requires a live DB (uses real program_sessions + session_exercises from
program_id=4). All inserts are rolled back after each test — no permanent
changes to the DB.

Run: PYTHONUTF8=1 uv run python tests/test_feedback.py
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # repo root → shared
sys.path.insert(0, str(Path(__file__).parent.parent))          # oly-agent  → models, feedback

import psycopg2
from shared.config import Settings
from feedback import compute_outcome, save_outcome, _compute_trend

# ── Constants pulled from the live DB ─────────────────────────────────────────
# program_id=4, athlete_id=1 (David), W1D1 session_id=41
# session_exercise ids 144 (Snatch warmup, rpe_target=5.5, ref=snatch)
#                      147 (Snatch working, rpe_target=8.0, ref=snatch)
PROGRAM_ID   = 4
ATHLETE_ID   = 1
SESSION_ID   = 41          # W1D1
SE_WARMUP_ID = 144         # rpe_target=5.5, intensity_reference=snatch
SE_WORKING_ID = 147        # rpe_target=8.0, intensity_reference=snatch


def _seed_log(conn, *, overall_rpe=7.0, notes=None) -> int:
    """Insert a training_log row for SESSION_ID and return its id."""
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO training_logs (athlete_id, session_id, log_date, overall_rpe, athlete_notes)
        VALUES (%s, %s, CURRENT_DATE, %s, %s)
        RETURNING id
        """,
        (ATHLETE_ID, SESSION_ID, overall_rpe, notes),
    )
    log_id = cur.fetchone()[0]
    cur.close()
    return log_id


def _seed_exercise(conn, log_id: int, se_id: int, *, rpe=8.0, make_rate=0.90) -> None:
    """Insert a training_log_exercises row linked to a session_exercise."""
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO training_log_exercises
            (log_id, session_exercise_id, exercise_name, sets_completed,
             reps_per_set, weight_kg, rpe, make_rate)
        VALUES (%s, %s, 'Snatch', 3, ARRAY[2,2,2], 70.0, %s, %s)
        """,
        (log_id, se_id, rpe, make_rate),
    )
    cur.close()


class TestComputeTrend(unittest.TestCase):
    """Pure-logic tests — no DB needed."""

    def test_ascending(self):
        self.assertEqual(_compute_trend([1.0, 1.0, 1.0, 2.5, 2.5, 2.5]), "ascending")

    def test_descending(self):
        self.assertEqual(_compute_trend([3.0, 3.0, 3.0, 1.0, 1.0, 1.0]), "descending")

    def test_stable(self):
        self.assertEqual(_compute_trend([2.0, 2.1, 1.9, 2.0, 2.0, 2.1]), "stable")

    def test_too_short_returns_stable(self):
        self.assertEqual(_compute_trend([1.0, 5.0]), "stable")

    def test_empty_returns_stable(self):
        self.assertEqual(_compute_trend([]), "stable")

    def test_invert_flag(self):
        # Higher RPE deviation in second half is BAD → ascending when not inverted,
        # but descending (improving) when inverted
        self.assertEqual(
            _compute_trend([0.0, 0.0, 0.0, 2.0, 2.0, 2.0], invert=True),
            "descending",
        )


class TestComputeOutcomeNoLogs(unittest.TestCase):
    """Outcome with zero logged sessions — all metrics default gracefully."""

    @classmethod
    def setUpClass(cls):
        settings = Settings()
        cls.conn = psycopg2.connect(settings.database_url)
        cls.conn.autocommit = False

    @classmethod
    def tearDownClass(cls):
        cls.conn.rollback()
        cls.conn.close()

    def test_sessions_prescribed(self):
        outcome = compute_outcome(PROGRAM_ID, ATHLETE_ID, self.conn)
        self.assertEqual(outcome.sessions_prescribed, 16)

    def test_zero_adherence(self):
        outcome = compute_outcome(PROGRAM_ID, ATHLETE_ID, self.conn)
        self.assertEqual(outcome.sessions_completed, 0)
        self.assertEqual(outcome.adherence_pct, 0.0)

    def test_zero_rpe_deviation(self):
        outcome = compute_outcome(PROGRAM_ID, ATHLETE_ID, self.conn)
        self.assertEqual(outcome.avg_rpe_deviation, 0.0)

    def test_zero_make_rate(self):
        outcome = compute_outcome(PROGRAM_ID, ATHLETE_ID, self.conn)
        self.assertEqual(outcome.avg_make_rate, 0.0)

    def test_stable_trends_with_no_data(self):
        outcome = compute_outcome(PROGRAM_ID, ATHLETE_ID, self.conn)
        self.assertEqual(outcome.rpe_trend, "stable")
        self.assertEqual(outcome.make_rate_trend, "stable")

    def test_no_feedback(self):
        outcome = compute_outcome(PROGRAM_ID, ATHLETE_ID, self.conn)
        self.assertIsNone(outcome.athlete_feedback)


class TestComputeOutcomeWithLogs(unittest.TestCase):
    """Outcome with seeded training logs — verifies metric computation."""

    @classmethod
    def setUpClass(cls):
        settings = Settings()
        cls.conn = psycopg2.connect(settings.database_url)
        cls.conn.autocommit = False
        # Seed one logged session with two exercises
        cls.log_id = _seed_log(cls.conn, overall_rpe=8.0, notes="Felt strong today")
        # Working set: rpe=8.0 vs target=8.0 → deviation=0
        _seed_exercise(cls.conn, cls.log_id, SE_WORKING_ID, rpe=8.0, make_rate=0.90)
        # Warmup set: rpe=6.5 vs target=5.5 → deviation=+1.0 (warmup has snatch ref)
        _seed_exercise(cls.conn, cls.log_id, SE_WARMUP_ID, rpe=6.5, make_rate=1.00)

    @classmethod
    def tearDownClass(cls):
        cls.conn.rollback()
        cls.conn.close()

    def test_one_session_completed(self):
        outcome = compute_outcome(PROGRAM_ID, ATHLETE_ID, self.conn)
        self.assertEqual(outcome.sessions_completed, 1)

    def test_adherence_pct(self):
        outcome = compute_outcome(PROGRAM_ID, ATHLETE_ID, self.conn)
        # 1 of 16 sessions = 6.25%
        self.assertAlmostEqual(outcome.adherence_pct, 6.25, places=1)

    def test_avg_rpe_deviation(self):
        outcome = compute_outcome(PROGRAM_ID, ATHLETE_ID, self.conn)
        # working: 8.0-8.0=0.0, warmup: 6.5-5.5=1.0 → avg=0.5
        self.assertAlmostEqual(outcome.avg_rpe_deviation, 0.5, places=2)

    def test_avg_make_rate(self):
        outcome = compute_outcome(PROGRAM_ID, ATHLETE_ID, self.conn)
        # both exercises have intensity_reference=snatch → avg of 0.90 and 1.00 = 0.95
        self.assertAlmostEqual(float(outcome.avg_make_rate), 0.95, places=2)

    def test_athlete_feedback_collected(self):
        outcome = compute_outcome(PROGRAM_ID, ATHLETE_ID, self.conn)
        self.assertIsNotNone(outcome.athlete_feedback)
        self.assertIn("Felt strong today", outcome.athlete_feedback)

    def test_weekly_reps_counted(self):
        outcome = compute_outcome(PROGRAM_ID, ATHLETE_ID, self.conn)
        # 2 exercises × 3 sets × 2 reps = 12 reps in week 1
        self.assertGreater(outcome.avg_weekly_reps, 0)


class TestSaveOutcome(unittest.TestCase):
    """save_outcome() persists to generated_programs and sets status=completed."""

    @classmethod
    def setUpClass(cls):
        settings = Settings()
        cls.conn = psycopg2.connect(settings.database_url)
        cls.conn.autocommit = False

    @classmethod
    def tearDownClass(cls):
        cls.conn.rollback()
        cls.conn.close()

    def test_save_and_read_back(self):
        import json
        outcome = compute_outcome(PROGRAM_ID, ATHLETE_ID, self.conn)
        save_outcome(outcome, self.conn)

        cur = self.conn.cursor()
        cur.execute(
            "SELECT status, outcome_summary FROM generated_programs WHERE id = %s",
            (PROGRAM_ID,),
        )
        row = cur.fetchone()
        cur.close()

        self.assertEqual(row[0], "completed")
        # psycopg2 auto-parses jsonb columns into dicts
        summary = row[1] if isinstance(row[1], dict) else __import__("json").loads(row[1])
        self.assertIn("adherence_pct", summary)
        self.assertIn("avg_make_rate", summary)
        self.assertIn("maxes_delta", summary)


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestComputeTrend))
    suite.addTests(loader.loadTestsFromTestCase(TestComputeOutcomeNoLogs))
    suite.addTests(loader.loadTestsFromTestCase(TestComputeOutcomeWithLogs))
    suite.addTests(loader.loadTestsFromTestCase(TestSaveOutcome))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
