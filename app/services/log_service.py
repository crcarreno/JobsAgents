from core.database import db
from core.logger import get_logger
from app.models import SystemLog


class LogService:
    def __init__(self):
        self.logger = get_logger()

    def log(self, level: str, message: str, context: dict | None = None, commit: bool = True) -> None:
        level = (level or "INFO").upper()
        log_method = getattr(self.logger, level.lower(), self.logger.info)
        log_method(message)
        try:
            entry = SystemLog(level=level, logger="jobagents", message=message[:8000], context=context)
            db.session.add(entry)
            if commit:
                db.session.commit()
        except Exception:
            db.session.rollback()

    def info(self, message: str, **context) -> None:
        self.log("INFO", message, context or None)

    def warning(self, message: str, **context) -> None:
        self.log("WARNING", message, context or None)

    def error(self, message: str, **context) -> None:
        self.log("ERROR", message, context or None)
