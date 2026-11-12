"""Logging to <DATA_DIR>/logs/nest.log (5 files x 5 MB), with a request id
on every line written while a request is handled."""
from __future__ import annotations

import contextvars
import logging
import os
import uuid
from logging.handlers import RotatingFileHandler

request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
log = logging.getLogger("nest")

MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 4          # nest.log + nest.log.1..4 = 5 files
FORMAT = "%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s: %(message)s"

_handler: RotatingFileHandler | None = None


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = request_id.get()
        return True


def new_request_id() -> str:
    return uuid.uuid4().hex[:10]


def log_path(data_dir: str) -> str:
    return os.path.join(data_dir, "logs", "nest.log")


def setup_logging(data_dir: str, level: int = logging.INFO) -> str:
    """Send the "nest" logger (and uvicorn's errors) to the data folder's log
    file. Calling it again with another folder moves the file handler."""
    global _handler
    path = log_path(data_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if _handler is not None and _handler.baseFilename == os.path.abspath(path):
        return path
    h = RotatingFileHandler(path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8", delay=True)
    h.setFormatter(logging.Formatter(FORMAT))
    h.addFilter(RequestIdFilter())
    for name in ("nest", "uvicorn.error"):
        lg = logging.getLogger(name)
        if _handler is not None:
            lg.removeHandler(_handler)
        lg.addHandler(h)
    if _handler is not None:
        _handler.close()
    _handler = h
    log.setLevel(level)
    return path
