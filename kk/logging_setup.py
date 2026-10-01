from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re


SENSITIVE = re.compile(
    r"(?i)([\"']?(?:password|passwd|token|secret|api[_-]?key|authorization)[\"']?\s*[=:]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|(?:Bearer\s+)?[^\s,;]+)"
)


def redact(text):
    return SENSITIVE.sub(r"\1[REDACTED]", text)


class RedactingFormatter(logging.Formatter):
    def format(self, record):
        # Exception text is appended by Formatter after the record filters run.
        return redact(super().format(record))



class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        rendered = record.getMessage()
        prefix = next((tag for tag in ('[KK]', '[Onyx]') if rendered.startswith(tag+' ')), None)
        if prefix and rendered.startswith(prefix+" User:"):
            rendered = prefix+" User: [REDACTED FROM TECHNICAL LOG]"
        elif prefix:
            rendered = prefix+" Response: [REDACTED FROM TECHNICAL LOG]"
        record.msg = redact(rendered)
        record.args = ()
        return True


def configure_logging(log_dir: Path, verbose: bool = False) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("kk")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(RedactingFormatter("%(message)s"))

    technical = RotatingFileHandler(
        log_dir / "kk.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    technical.setLevel(logging.DEBUG)
    technical.setFormatter(
        RedactingFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    technical.addFilter(RedactingFilter())
    logger.addHandler(console)
    logger.addHandler(technical)
    # A dead daemon worker must leave a traceback, not a healthy-looking Orb.
    import sys, threading
    def thread_failure(args):
        logger.error('Background thread failed: %s', args.thread.name if args.thread else 'unknown',
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
    def process_failure(kind, value, traceback):
        logger.critical('Unhandled process exception', exc_info=(kind, value, traceback))
    threading.excepthook = thread_failure
    sys.excepthook = process_failure
    return logger
