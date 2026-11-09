from core.database import db
from core.timeutil import utcnow


class TaskExecution(db.Model):
    __tablename__ = "task_executions"
    __table_args__ = (
        db.UniqueConstraint("task_id", "attempt", name="uq_task_executions_task_attempt"),
    )

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    attempt = db.Column(db.Integer, nullable=False, default=1)
    status = db.Column(db.String(24), nullable=False, default="running")
    started_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    finished_at = db.Column(db.DateTime, nullable=True)
    workspace_path = db.Column(db.String(1024), nullable=True)
    model = db.Column(db.String(120), nullable=True)
    session_id = db.Column(db.String(80), nullable=True)
    ai_requests = db.Column(db.Integer, nullable=False, default=0)
    ai_cost = db.Column(db.Numeric(12, 6), nullable=False, default=0)
    output_summary = db.Column(db.Text, nullable=True)
    artifacts = db.Column(db.JSON, nullable=True)
    error = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    task = db.relationship("Task", back_populates="executions")
