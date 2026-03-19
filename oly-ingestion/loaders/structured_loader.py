# loaders/structured_loader.py
"""
Loads structured data into Postgres tables.

Handles: sources, exercises, percentage_schemes, programming_principles,
program_templates, exercise_substitutions, exercise_complexes.
"""

import hashlib
import json
import logging
import time
from pathlib import Path

import psycopg2
from psycopg2.extras import Json

logger = logging.getLogger(__name__)


class StructuredLoader:
    def __init__(self, settings):
        self.conn = psycopg2.connect(settings.database_url)

    # ── Sources ───────────────────────────────────────────────

    def upsert_source(self, title: str, author: str, source_type: str) -> int | None:
        """Insert or retrieve a source record. Returns the source ID.

        Returns the existing ID if the source already exists,
        or the new ID after insertion.
        """
        cursor = self.conn.cursor()

        cursor.execute(
            "SELECT id FROM sources WHERE title = %s AND author = %s",
            (title, author),
        )
        existing = cursor.fetchone()
        if existing:
            cursor.close()
            return existing[0]

        # Map doc_type string to source_type enum
        type_map = {
            "book": "book",
            "article": "article",
            "program": "manual",
            "structured": "manual",
            "website": "website",
        }
        db_type = type_map.get(source_type, "book")

        cursor.execute(
            """
            INSERT INTO sources (title, author, source_type)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (title, author, db_type),
        )
        source_id = cursor.fetchone()[0]
        self.conn.commit()
        cursor.close()
        return source_id

    # ── Principles ────────────────────────────────────────────

    def load_principles(self, principles: list, source_id: int) -> int:
        """Load extracted principles into programming_principles table."""
        cursor = self.conn.cursor()
        loaded = 0

        for p in principles:
            try:
                cursor.execute(
                    """
                    INSERT INTO programming_principles
                        (principle_name, source_id, category, rule_type,
                         condition, recommendation, rationale, priority)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        p.principle_name,
                        source_id,
                        p.category,
                        p.rule_type,
                        Json(p.condition),
                        Json(p.recommendation),
                        p.rationale,
                        p.priority,
                    ),
                )
                loaded += 1
            except Exception as e:
                logger.error(f"Failed to load principle '{p.principle_name}': {e}")
                self.conn.rollback()
                continue

        self.conn.commit()
        cursor.close()
        logger.info(f"  Loaded {loaded} principles")
        return loaded

    # ── Programs ──────────────────────────────────────────────

    def load_program(self, program: dict) -> int | None:
        """Load a program template into program_templates table."""
        duration_weeks = program.get("duration_weeks", 0)
        sessions_per_week = program.get("sessions_per_week", 0)
        if duration_weeks < 1 or not (1 <= sessions_per_week <= 14):
            logger.warning(
                f"Skipping program '{program.get('name')}': "
                f"duration_weeks={duration_weeks}, sessions_per_week={sessions_per_week} "
                f"(must have duration_weeks>=1 and sessions_per_week 1-14)"
            )
            return None
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO program_templates
                    (name, source_id, athlete_level, goal,
                     duration_weeks, sessions_per_week, program_structure)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    program["name"],
                    program["source_id"],
                    program.get("athlete_level", "any"),
                    program.get("goal", "general_strength"),
                    program.get("duration_weeks", 0),
                    program.get("sessions_per_week", 0),
                    Json(program["program_structure"]),
                ),
            )
            program_id = cursor.fetchone()[0]
            self.conn.commit()
            cursor.close()
            logger.info(f"  Loaded program template: {program['name']} (id={program_id})")
            return program_id
        except Exception as e:
            logger.error(f"Failed to load program '{program.get('name')}': {e}")
            self.conn.rollback()
            cursor.close()
            return None

    # ── Exercises ─────────────────────────────────────────────

    def load_exercise(self, exercise: dict) -> int | None:
        """Load an exercise into the exercises table."""
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO exercises
                    (name, category, movement_family, primary_purpose,
                     faults_addressed, source_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (name) DO UPDATE
                    SET primary_purpose = EXCLUDED.primary_purpose,
                        faults_addressed = EXCLUDED.faults_addressed
                RETURNING id
                """,
                (
                    exercise["name"],
                    exercise.get("category", "competition_variant"),
                    exercise.get("movement_family", "snatch"),
                    exercise.get("primary_purpose", ""),
                    exercise.get("faults_addressed", []),
                    exercise.get("source_id"),
                ),
            )
            exercise_id = cursor.fetchone()[0]
            self.conn.commit()
            cursor.close()
            return exercise_id
        except Exception as e:
            logger.error(f"Failed to load exercise '{exercise.get('name')}': {e}")
            self.conn.rollback()
            cursor.close()
            return None

    # ── Percentage Schemes ────────────────────────────────────

    def load_percentage_schemes(self, rows: list[dict], source_id: int) -> int:
        """Load percentage scheme rows parsed from tables."""
        cursor = self.conn.cursor()
        loaded = 0
        for row in rows:
            try:
                cursor.execute(
                    """
                    INSERT INTO percentage_schemes
                        (scheme_name, source_id, phase, week_number, day_number,
                         exercise_order, sets, reps, intensity_pct, intensity_reference)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        row.get("scheme_name", "Unknown"),
                        source_id,
                        row.get("phase", "accumulation"),
                        row.get("week_number", 1),
                        row.get("day_number", 1),
                        row.get("exercise_order", 1),
                        row["sets"],
                        row["reps"],
                        row["intensity_pct"],
                        row.get("intensity_reference", "competition_lift"),
                    ),
                )
                loaded += 1
            except Exception as e:
                logger.error(f"Failed to load percentage scheme row: {e}")
                self.conn.rollback()
                continue
        self.conn.commit()
        cursor.close()
        return loaded

    def load_prilepin_rows(self, rows: list[dict]) -> int:
        """Load rows into prilepin_chart table."""
        cursor = self.conn.cursor()
        loaded = 0
        for row in rows:
            try:
                cursor.execute(
                    """
                    INSERT INTO prilepin_chart
                        (intensity_range_low, intensity_range_high,
                         reps_per_set_low, reps_per_set_high,
                         optimal_total_reps, total_reps_range_low, total_reps_range_high,
                         movement_type, notes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        row["intensity_range_low"],
                        row["intensity_range_high"],
                        row["reps_per_set_low"],
                        row["reps_per_set_high"],
                        row["optimal_total_reps"],
                        row["total_reps_range_low"],
                        row["total_reps_range_high"],
                        row.get("movement_type", "competition_lifts"),
                        row.get("notes", ""),
                    ),
                )
                loaded += 1
            except Exception as e:
                logger.error(f"Failed to load Prilepin row: {e}")
                self.conn.rollback()
                continue
        self.conn.commit()
        cursor.close()
        return loaded

    # ── JSON import (for pre-structured seed data) ────────────

    def load_json(self, path: Path, source_id: int) -> int:
        """Load pre-structured JSON data.

        Expected JSON format:
        {
            "target_table": "exercises" | "percentage_schemes" | "prilepin_chart",
            "records": [ { ... }, { ... } ]
        }
        """
        with open(path) as f:
            data = json.load(f)

        target = data.get("target_table", "")
        records = data.get("records", [])

        if target == "exercises":
            for rec in records:
                rec["source_id"] = source_id
                self.load_exercise(rec)
        elif target == "percentage_schemes":
            self.load_percentage_schemes(records, source_id)
        elif target == "prilepin_chart":
            self.load_prilepin_rows(records)
        else:
            logger.warning(f"Unknown target_table in JSON: {target}")
            return 0

        return len(records)

    # ── Ingestion run tracking ────────────────────────────────

    def create_run(
        self,
        source_id: int,
        file_path: str,
        file_hash: str | None,
        config_snapshot: dict | None = None,
    ) -> int:
        """Create an ingestion_runs row. Returns the run ID."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO ingestion_runs
                (source_id, status, file_path, file_hash, config_snapshot)
            VALUES (%s, 'started', %s, %s, %s)
            RETURNING id
            """,
            (source_id, file_path, file_hash, Json(config_snapshot or {})),
        )
        run_id = cursor.fetchone()[0]
        self.conn.commit()
        cursor.close()
        logger.info(f"  Created ingestion run #{run_id} for source_id={source_id}")
        return run_id

    def update_run_status(self, run_id: int, status: str) -> None:
        """Update the status field of an ingestion run."""
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE ingestion_runs SET status = %s WHERE id = %s",
            (status, run_id),
        )
        self.conn.commit()
        cursor.close()

    def update_run_progress(
        self, run_id: int, pages_processed: int, last_processed_page: int
    ) -> None:
        """Checkpoint progress so a failed run can be resumed."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE ingestion_runs
               SET pages_processed = %s,
                   last_processed_page = %s,
                   status = 'processing'
             WHERE id = %s
            """,
            (pages_processed, last_processed_page, run_id),
        )
        self.conn.commit()
        cursor.close()

    def complete_run(self, run_id: int, stats: dict) -> None:
        """Mark a run as completed and record final stats + duration."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE ingestion_runs
               SET status = 'completed',
                   completed_at = NOW(),
                   duration_seconds = EXTRACT(EPOCH FROM (NOW() - started_at)),
                   chunks_created          = %s,
                   chunks_skipped_dedup    = %s,
                   chunks_quarantined      = %s,
                   principles_extracted    = %s,
                   programs_parsed         = %s,
                   exercises_created       = %s,
                   tables_parsed           = %s
             WHERE id = %s
            """,
            (
                stats.get("chunks_loaded", stats.get("prose_chunks_valid", 0)),
                stats.get("prose_chunks_valid", 0) - stats.get("chunks_loaded", stats.get("prose_chunks_valid", 0)),
                stats.get("prose_chunks_quarantined", 0),
                stats.get("principles", 0),
                stats.get("programs", 0),
                stats.get("exercises", 0),
                stats.get("tables_parsed", 0),
                run_id,
            ),
        )
        self.conn.commit()
        cursor.close()
        logger.info(f"  Ingestion run #{run_id} marked completed")

    def fail_run(self, run_id: int, error_message: str, error_details: dict | None = None) -> None:
        """Mark a run as failed, storing the error for debugging."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE ingestion_runs
               SET status = 'failed',
                   completed_at = NOW(),
                   duration_seconds = EXTRACT(EPOCH FROM (NOW() - started_at)),
                   error_message = %s,
                   error_details = %s
             WHERE id = %s
            """,
            (error_message, Json(error_details or {}), run_id),
        )
        self.conn.commit()
        cursor.close()
        logger.error(f"  Ingestion run #{run_id} marked failed: {error_message}")

    def find_resumable_run(self, file_hash: str) -> int | None:
        """Find a previously failed run for the same file that can be resumed."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, last_processed_page
              FROM ingestion_runs
             WHERE file_hash = %s AND status = 'failed'
             ORDER BY started_at DESC
             LIMIT 1
            """,
            (file_hash,),
        )
        row = cursor.fetchone()
        cursor.close()
        if row:
            logger.info(f"  Found resumable run #{row[0]} at page {row[1]}")
            return row[0]
        return None

    def log_chunk(self, run_id: int, chunk_id: int, page_number: int | None,
                  section_title: str | None, classification: str | None) -> None:
        """Link a chunk to its ingestion run for rollback support."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO ingestion_chunk_log
                (ingestion_run_id, chunk_id, page_number, section_title, classification)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (run_id, chunk_id, page_number, section_title, classification),
        )
        self.conn.commit()
        cursor.close()

    def close(self):
        self.conn.close()
