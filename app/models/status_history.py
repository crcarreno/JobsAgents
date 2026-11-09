from core.database import db
from core.timeutil import utcnow


class TaskStatusHistory(db.Model):
    __tablename__ = "task_status_history"

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    from_status = db.Column(db.String(32), nullable=True)
    to_status = db.Column(db.String(32), nullable=False)
    reason = db.Column(db.Text, nullable=True)
    actor = db.Column(db.String(120), nullable=False, default="system")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    task = db.relationship("Task", back_populates="status_history")
