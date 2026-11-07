from datetime import datetime

from models import db


class AiClearingJob(db.Model):
    __tablename__ = "ai_clearing_jobs"

    id = db.Column(db.Integer, primary_key=True)
    status = db.Column(db.String(20), nullable=False, default="queued")
    started_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    total = db.Column(db.Integer, nullable=False, default=0)
    processed = db.Column(db.Integer, nullable=False, default=0)
    started_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    items = db.relationship(
        "AiClearingJobItem",
        backref="job",
        cascade="all, delete-orphan",
        lazy="dynamic",
    )


class AiClearingJobItem(db.Model):
    __tablename__ = "ai_clearing_job_items"

    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(
        db.Integer, db.ForeignKey("ai_clearing_jobs.id"), nullable=False
    )
    ticket_id = db.Column(db.Integer, db.ForeignKey("tickets.id"), nullable=False)
    status = db.Column(db.String(30), nullable=False, default="pending")
    message = db.Column(db.String(500), nullable=True)
    started_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)

    ticket = db.relationship("Ticket")

    __table_args__ = (
        db.UniqueConstraint("job_id", "ticket_id", name="uq_ai_clearing_job_ticket"),
        db.Index("ix_ai_clearing_job_item_status", "job_id", "status"),
    )
