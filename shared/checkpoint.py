# shared/checkpoint.py
"""
Append-only progress file for runs that pay per item (RESUME-1).

The model-comparison and eval harnesses used to hold every result in memory
and write one JSON at the end, so an abort — Ctrl-C, a crash, a 402, a
declined standard-tier fallback (`FlexUnavailable`) — lost every paid call
(2026-10-12: an OCR comparison held $2.85 of results through an 8-hour
stall). A `Checkpoint` sits beside the result file as
`<out>.partial.jsonl`: one JSON line per finished item, flushed and fsynced
as it lands. Re-running with `--resume` reads it back and the harness skips
the items already done; the final write calls `finish()`, which removes it.

Only successful items should be `put` — a failed item is simply re-run on
resume. Keys are strings the caller builds from whatever identifies the work
(model, reasoning mode, item label). `put` is thread-safe.
"""

import json
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)


class CheckpointMismatch(SystemExit):
    """--resume against a partial file written for different settings."""


class Checkpoint:
    def __init__(self, out: Path, *, resume: bool, signature: dict | None = None):
        self.path = Path(out).with_name(Path(out).name + ".partial.jsonl")
        self.signature = signature or {}
        self._lock = threading.Lock()
        self._done: dict[str, object] = {}
        if resume and self.path.exists():
            self._load()
            logger.info(f"Resuming: {len(self._done)} item(s) already done in {self.path}")
        else:
            if self.path.exists():
                logger.warning(f"Discarding the previous partial run {self.path} (pass --resume to continue it)")
            elif resume:
                logger.info(f"--resume: no partial run at {self.path} — starting fresh")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                f.write(json.dumps({"_signature": self.signature}, ensure_ascii=False) + "\n")

    def _load(self) -> None:
        with open(self.path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        for n, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                # a line cut off by the abort: everything before it is intact
                logger.warning(f"{self.path}:{n + 1}: unreadable line skipped (cut off mid-write?)")
                continue
            if "_signature" in row:
                if row["_signature"] != self.signature:
                    raise CheckpointMismatch(
                        f"{self.path} was written with {row['_signature']}, this run is {self.signature} — "
                        f"re-run without --resume (or with the same settings)")
                continue
            self._done[row["key"]] = row["value"]

    def __contains__(self, key: str) -> bool:
        return key in self._done

    def __len__(self) -> int:
        return len(self._done)

    def get(self, key: str, default=None):
        return self._done.get(key, default)

    def put(self, key: str, value) -> None:
        line = json.dumps({"key": key, "value": value}, ensure_ascii=False) + "\n"
        with self._lock:
            self._done[key] = value
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())

    def finish(self) -> None:
        """The final result is written — the partial file is no longer needed."""
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
