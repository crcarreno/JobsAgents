from core.database import db
from core.timeutil import utcnow
from app.models.states import EarningStatus


class Earning(db.Model):
    __tablename__ = "earnings"

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True)
    platform_id = db.Column(
        db.Integer, db.ForeignKey("platform_config.id", ondelete="SET NULL"), nullable=True, index=True
    )
    amount = db.Column(db.Numeric(12, 4), nullable=False, default=0)
    currency = db.Column(db.String(8), nullable=False, default="USD")
    status = db.Column(db.String(16), nullable=False, default=EarningStatus.PENDING, index=True)
    reference = db.Column(db.String(255), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    paid_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    task = db.relationship("Task", back_populates="earnings")
