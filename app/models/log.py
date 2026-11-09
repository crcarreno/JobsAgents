from core.database import db
from core.timeutil import utcnow


class SystemLog(db.Model):
    __tablename__ = "system_logs"

    id = db.Column(db.Integer, primary_key=True)
    level = db.Column(db.String(16), nullable=False, default="INFO", index=True)
    logger = db.Column(db.String(120), nullable=False, default="jobagents")
    message = db.Column(db.Text, nullable=False)
    context = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
