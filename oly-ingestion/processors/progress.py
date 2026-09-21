# processors/progress.py
"""
Progress reporting for long ingestion runs (a scanned book is 1–2 hours of
OCR, classification and principle extraction, and the log used to go quiet
for most of it).

    with Stage("Extract text", logger) as stage:          # "── Extract text …"
        ...
    # on exit: "── Extract text: done in 13m 02s"

    progress = Progress(total, logger, label="section", every=5)
    for i, item in enumerate(items):
        ...
        progress.tick(i + 1, f"{kind} chunks={n}")        # "[section 12/48 · 25%] … · elapsed 4m 10s · ETA ~12m"

`tick` logs every `every` items and always on the last one; ETA is the running
average per item, which is honest enough for LLM-bound loops.
"""

import logging
import time


def fmt_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


class Stage:
    def __init__(self, name: str, logger: logging.Logger, detail: str = ""):
        self.name, self.logger, self.detail = name, logger, detail
        self.started = 0.0

    def __enter__(self) -> "Stage":
        self.started = time.perf_counter()
        self.logger.info(f"── {self.name}" + (f" — {self.detail}" if self.detail else "") + " …")
        return self

    @property
    def elapsed(self) -> float:
        return time.perf_counter() - self.started

    def __exit__(self, exc_type, exc, tb) -> None:
        status = "failed" if exc_type else "done"
        self.logger.log(logging.ERROR if exc_type else logging.INFO,
                        f"── {self.name}: {status} in {fmt_duration(self.elapsed)}")


class Progress:
    def __init__(self, total: int, logger: logging.Logger, label: str = "item", every: int = 1):
        self.total, self.logger, self.label, self.every = max(total, 0), logger, label, max(every, 1)
        self.started = time.perf_counter()
        self.done = 0

    def tick(self, done: int, note: str = "") -> None:
        self.done = done
        if self.total and done % self.every and done != self.total:
            return
        elapsed = time.perf_counter() - self.started
        pct = f" · {100 * done // self.total}%" if self.total else ""
        eta = ""
        if self.total and 0 < done < self.total:
            eta = f" · ETA ~{fmt_duration(elapsed / done * (self.total - done))}"
        self.logger.info(
            f"[{self.label} {done}/{self.total}{pct}]" + (f" {note}" if note else "")
            + f" · elapsed {fmt_duration(elapsed)}{eta}"
        )
