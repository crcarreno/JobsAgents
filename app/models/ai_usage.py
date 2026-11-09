from core.database import db
from core.timeutil import utcnow


class AIUsage(db.Model):
    __tablename__ = "ai_usage"

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True)
    provider = db.Column(db.String(60), nullable=False, default="opencode_go")
    model = db.Column(db.String(120), nullable=False)
    session_id = db.Column(db.String(80), nullable=True, index=True)
    request_type = db.Column(db.String(40), nullable=False)
    prompt_tokens = db.Column(db.Integer, nullable=False, default=0)
    completion_tokens = db.Column(db.Integer, nullable=False, default=0)
    cost = db.Column(db.Numeric(12, 6), nullable=False, default=0)
    allowance = db.Column(db.Numeric(12, 6), nullable=True)
    billing_source = db.Column(db.String(24), nullable=True)
    duration_ms = db.Column(db.Integer, nullable=False, default=0)
    success = db.Column(db.Boolean, nullable=False, default=True)
    error = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
