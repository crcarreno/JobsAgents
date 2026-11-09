import logging
import logging.handlers
import os
import re
from pathlib import Path

from core.config import PROJECT_ROOT

SENSITIVE_PATTERNS = [
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"(?i)(token\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"(?i)(password\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"oc_sk_[A-Za-z0-9_\-]{16,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
]


def redact(value):
    text = str(value)
    for pattern in SENSITIVE_PATTERNS:
        if pattern.groups:
            text = pattern.sub(lambda m: m.group(1) + "***", text)
        else:
            text = pattern.sub("***", text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record):
        try:
            record.msg = redact(record.getMessage())
            record.args = ()
        except Exception:
            pass
        return True


_configured = False


def configure_logging(level: str | None = None) -> logging.Logger:
    global _configured
    logger = logging.getLogger("jobagents")
    if _configured:
        return logger

    log_level = (level or os.getenv("LOG_LEVEL") or "INFO").upper()
    logger.setLevel(getattr(logging, log_level, logging.INFO))
    logger.propagate = False

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s", "%Y-%m-%dT%H:%M:%S%z"
    )

    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    stream.addFilter(RedactingFilter())
    logger.addHandler(stream)

    log_dir = Path(os.getenv("LOG_DIR", PROJECT_ROOT / "var" / "log"))
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / "jobagents.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(RedactingFilter())
        logger.addHandler(file_handler)
    except OSError:
        pass

    _configured = True
    return logger


def get_logger(name: str = "jobagents") -> logging.Logger:
    configure_logging()
    if name == "jobagents":
        return logging.getLogger("jobagents")
    return logging.getLogger(f"jobagents.{name}")
