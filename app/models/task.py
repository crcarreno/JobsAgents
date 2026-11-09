from core.database import db
from core.timeutil import utcnow
from app.models.states import TaskStatus


class Task(db.Model):
    __tablename__ = "tasks"
    __table_args__ = (
        db.UniqueConstraint("platform_id", "external_id", name="uq_tasks_platform_external"),
        db.Index("ix_tasks_status_created", "status", "created_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    platform_id = db.Column(
        db.Integer, db.ForeignKey("platform_config.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_id = db.Column(db.String(255), nullable=False)
    dedupe_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    title = db.Column(db.String(512), nullable=False)
    description = db.Column(db.Text, nullable=False, default="")
    url = db.Column(db.String(1024), nullable=False, default="")
    category = db.Column(db.String(120), nullable=True)
    currency = db.Column(db.String(8), nullable=False, default="USD")
    reward_amount = db.Column(db.Numeric(12, 4), nullable=False, default=0)
    platform_fee = db.Column(db.Numeric(12, 4), nullable=False, default=0)
    ai_cost_estimate = db.Column(db.Numeric(12, 6), nullable=False, default=0)
    infrastructure_cost = db.Column(db.Numeric(12, 6), nullable=False, default=0)
    net_profit = db.Column(db.Numeric(12, 4), nullable=False, default=0)
    hourly_rate = db.Column(db.Numeric(12, 4), nullable=False, default=0)
    estimated_minutes = db.Column(db.Integer, nullable=False, default=0)
    complexity = db.Column(db.Integer, nullable=False, default=3)
    success_probability = db.Column(db.Float, nullable=False, default=0.0)
    risk_score = db.Column(db.Float, nullable=False, default=0.0)
    automation_allowed = db.Column(db.Boolean, nullable=True)
    automation_notes = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(32), nullable=False, default=TaskStatus.PENDING, index=True)
    last_reason = db.Column(db.Text, nullable=True)
    evaluation = db.Column(db.JSON, nullable=True)
    raw_payload = db.Column(db.JSON, nullable=True)
    deadline_at = db.Column(db.DateTime, nullable=True)
    claimed_at = db.Column(db.DateTime, nullable=True)
    submitted_at = db.Column(db.DateTime, nullable=True)
    accepted_at = db.Column(db.DateTime, nullable=True)
    delivery_reference = db.Column(db.String(1024), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    platform = db.relationship("PlatformConfig", back_populates="tasks")
    executions = db.relationship(
        "TaskExecution", back_populates="task", cascade="all, delete-orphan", lazy="dynamic"
    )
    status_history = db.relationship(
        "TaskStatusHistory", back_populates="task", cascade="all, delete-orphan", lazy="dynamic"
    )
    earnings = db.relationship("Earning", back_populates="task", lazy="dynamic")

    def is_executable(self) -> bool:
        return (
            self.automation_allowed is True
            and self.status in {TaskStatus.APPROVED, TaskStatus.MANUAL_REVIEW}
        )

    def as_public_dict(self) -> dict:
        return {
            "id": self.id,
            "external_id": self.external_id,
            "title": self.title,
            "url": self.url,
            "status": self.status,
            "reward_amount": float(self.reward_amount or 0),
            "net_profit": float(self.net_profit or 0),
            "hourly_rate": float(self.hourly_rate or 0),
            "estimated_minutes": self.estimated_minutes,
            "success_probability": self.success_probability,
        }
