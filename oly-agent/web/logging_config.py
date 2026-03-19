# web/logging_config.py
"""Structured logging configuration for the web app and ARQ worker.

Two output formats, selected via the LOG_FORMAT environment variable:
  text  (default) — human-readable, good for local dev
  json            — structured JSON, for log aggregators in production
                    (CloudWatch, Datadog, Loki, etc.)

Request ID is injected into every log record via a contextvars.ContextVar
set by RequestIDMiddleware for each incoming HTTP request, and by the ARQ
worker at job start. This allows correlating logs end-to-end:

    browser  →  web server logs (request_id=abc123)
             →  ARQ worker logs (request_id=abc123)
"""

import contextvars
import json
import logging
import sys

# Set per-request (RequestIDMiddleware) or per-job (worker.run_generation).
# All formatters read from here so request_id appears in every log line.
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class _RequestIDFilter(logging.Filter):
    """Injects request_id from the contextvar into every LogRecord."""
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get("-")  # type: ignore[attr-defined]
        return True


_BUILTIN_LOG_ATTRS = frozenset({
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "id", "levelname", "levelno", "lineno", "message",
    "module", "msecs", "msg", "name", "pathname", "process",
    "processName", "relativeCreated", "request_id", "stack_info",
    "taskName", "thread", "threadName",
})


class _JsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects.

    Any fields passed via logger.info(..., extra={...}) are included in the
    JSON output alongside the standard fields, enabling structured logging
    for program_id, step, duration_seconds, etc.
    """
    def format(self, record: logging.LogRecord) -> str:
        out: dict = {
            "ts":         self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level":      record.levelname,
            "logger":     record.name,
            "msg":        record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        # Include caller-supplied extra= fields
        for key, val in record.__dict__.items():
            if key not in _BUILTIN_LOG_ATTRS:
                out[key] = val
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


_TEXT_FMT = "%(asctime)s %(levelname)-8s [%(request_id)s] %(name)s  %(message)s"
_DATE_FMT = "%H:%M:%S"


def configure_logging(log_format: str = "text", log_level: str = "INFO") -> None:
    """Configure the root logger. Call once at app / worker startup."""
    level = getattr(logging, log_level.upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_RequestIDFilter())

    if log_format == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(_TEXT_FMT, datefmt=_DATE_FMT))

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)
