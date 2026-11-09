from core.database import db
from core.timeutil import utcnow


class PlatformConfig(db.Model):
    __tablename__ = "platform_config"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    slug = db.Column(db.String(80), nullable=False, unique=True, index=True)
    adapter = db.Column(db.String(80), nullable=False)
    enabled = db.Column(db.Boolean, nullable=False, default=False)
    automation_permission = db.Column(db.String(16), nullable=False, default="unknown")
    automation_permission_notes = db.Column(db.Text, nullable=True)
    scan_interval_minutes = db.Column(db.Integer, nullable=False, default=60)
    config = db.Column(db.JSON, nullable=False, default=dict)
    credentials_encrypted = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(24), nullable=False, default="unknown")
    last_scan_at = db.Column(db.DateTime, nullable=True)
    last_scan_status = db.Column(db.String(24), nullable=True)
    last_scan_message = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    tasks = db.relationship("Task", back_populates="platform", lazy="dynamic")

    def config_value(self, key, default=None):
        return (self.config or {}).get(key, default)

    def is_due(self, now=None) -> bool:
        if not self.enabled:
            return False
        if self.last_scan_at is None:
            return True
        now = now or utcnow()
        delta = (now - self.last_scan_at).total_seconds()
        return delta >= max(1, self.scan_interval_minutes) * 60
