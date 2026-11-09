from core.database import db
from core.timeutil import utcnow


class SystemSetting(db.Model):
    __tablename__ = "system_settings"

    key = db.Column(db.String(120), primary_key=True)
    value = db.Column(db.Text, nullable=True)
    value_type = db.Column(db.String(16), nullable=False, default="string")
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
