from core.database import db
from core.timeutil import utcnow


class WorkerState(db.Model):
    __tablename__ = "worker_state"

    id = db.Column(db.Integer, primary_key=True, default=1)
    status = db.Column(db.String(24), nullable=False, default="stopped")
    pid = db.Column(db.Integer, nullable=True)
    last_heartbeat = db.Column(db.DateTime, nullable=True)
    last_scan_started_at = db.Column(db.DateTime, nullable=True)
    last_scan_finished_at = db.Column(db.DateTime, nullable=True)
    next_scan_at = db.Column(db.DateTime, nullable=True)
    current_task_id = db.Column(db.Integer, nullable=True)
    message = db.Column(db.Text, nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
