"""Request ids, log formats and Prometheus metrics.

Each HTTP request runs with an id in ``REQUEST_ID``. Work for the request on other threads runs in a ``ContextPool``,
so every log line of the request carries the id, whichever thread writes it.
"""
from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from prometheus_client import Counter, Histogram

REQUEST_ID: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
# an id from a client goes into log lines and a reply header as it is, so it is kept to a safe set of characters
VALID_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")
LOG_FORMATS = ("text", "json")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
# the attributes of every LogRecord; the others on a record came from ``extra``. uvicorn adds color_message, its
# message with terminal colour codes
STANDARD = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime", "request_id",
                                                                             "color_message"}

REQUESTS = Counter("evidencia_requests", "HTTP requests, by route template, method and status",
                   ["route", "method", "status"])
REQUEST_SECONDS = Histogram("evidencia_request_seconds", "Seconds until the response starts, by route template",
                            ["route"])
ABSTENTIONS = Counter("evidencia_abstentions", "Questions that a gate stopped, by gate", ["gate"])
DECISION_CALLS = Counter("evidencia_decision_calls", "Calls to the decision backend, by gate", ["gate"])
DECISION_ERRORS = Counter("evidencia_decision_errors", "Decision backend failures, by gate and kind: "
                          "unavailable, or rejected for one state", ["gate", "error"])
SELECT_BATCH = Histogram("evidencia_select_batch_size", "Passages in one select call to the decision backend",
                         buckets=(1, 2, 4, 8, 16, 32, 64))
SOURCE_FETCHES = Counter("evidencia_source_fetches", "Fetches from a remote source, failed ones included", ["source"])
SOURCE_ERRORS = Counter("evidencia_source_errors", "Failed fetches and list refreshes of a remote source", ["source"])
SOURCE_PAUSES = Counter("evidencia_source_pauses", "Times the circuit breaker paused a remote source", ["source"])
EMBED_CALLS = Counter("evidencia_embed_calls", "Calls to the embedder")
EMBED_TEXTS = Counter("evidencia_embed_texts", "Texts embedded")


def request_id(given: str | None) -> str:
    """The client's id when it is safe to log and echo, else a new one."""
    return given if given and VALID_ID.fullmatch(given) else uuid.uuid4().hex


class ContextPool(ThreadPoolExecutor):
    """A thread pool whose tasks run in a copy of the context of the thread that submits them."""

    def submit(self, fn, /, *args, **kwargs):
        return super().submit(contextvars.copy_context().run, fn, *args, **kwargs)


class RequestIdFilter(logging.Filter):
    """Sets ``record.request_id``, read in the thread that writes the record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = REQUEST_ID.get()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line: time, level, logger, message, request_id, the ``extra`` fields and any traceback."""

    def format(self, record: logging.LogRecord) -> str:
        line = {"time": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
                + f".{int(record.msecs):03d}Z",
                "level": record.levelname, "logger": record.name, "message": record.getMessage(),
                "request_id": getattr(record, "request_id", "-")}
        line.update((k, v) for k, v in vars(record).items() if k not in STANDARD)
        if record.exc_info:
            line["exception"] = self.formatException(record.exc_info)
        return json.dumps(line, ensure_ascii=False, default=str)


def configure_logging(fmt: str = "text", level: str = "INFO") -> None:
    """Send every log line of the process, uvicorn's included, to stderr in ``fmt``."""
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(JsonFormatter() if fmt == "json" else
                         logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
