"""Structured JSON logger with PHI denylist. Use get_logger() once at module level."""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="local")

_DEFAULT_DENYLIST: frozenset[str] = frozenset(
    {
        "access_token",
        "refresh_token",
        "claimant_name",
        "dob",
        "ssn",
        "phone",
        "email",
        "raw_json",
        "pkce_verifier",
        "client_secret",
        "encryption_key",
    }
)

# Standard LogRecord fields to exclude from the "extra" section of JSON output.
_STDLIB_LOG_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "asctime",
        "function_name",
        "request_id",
    }
)


class _JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        log_entry: dict[str, Any] = {
            "timestamp": datetime.now(tz=UTC).isoformat(),
            "level": record.levelname,
            "function_name": getattr(record, "function_name", "unknown"),
            "request_id": getattr(record, "request_id", "local"),
            "message": message,
        }
        for key, val in record.__dict__.items():
            if key not in _STDLIB_LOG_FIELDS:
                log_entry[key] = val
        if record.exc_info:
            log_entry["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, default=str)


class _RequestIdFilter(logging.Filter):
    def __init__(self, function_name: str) -> None:
        super().__init__()
        self.function_name = function_name

    def filter(self, record: logging.LogRecord) -> bool:
        record.function_name = self.function_name
        record.request_id = _request_id.get()
        return True


def set_request_id(request_id: str) -> None:
    """Call at the start of each Lambda invocation with context.aws_request_id."""
    _request_id.set(request_id)


def get_logger(function_name: str) -> logging.Logger:
    """Return a structured JSON logger. Call once at module level."""
    logger = logging.getLogger(f"roa.{function_name}")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_JSONFormatter())
        logger.addHandler(handler)
        logger.propagate = False
        logger.setLevel(logging.INFO)
    logger.filters.clear()
    logger.addFilter(_RequestIdFilter(function_name))
    return logger


def redact(data: dict[str, Any], denylist: set[str] | None = None) -> dict[str, Any]:
    """Replace PHI keys with '[REDACTED]'. Safe to call on any log metadata dict."""
    effective = _DEFAULT_DENYLIST | (denylist or set())
    return {k: "[REDACTED]" if k.lower() in effective else v for k, v in data.items()}
