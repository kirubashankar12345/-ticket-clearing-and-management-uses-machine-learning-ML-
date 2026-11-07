from datetime import datetime
from sqlalchemy import select
from models import db


class TicketCounter(db.Model):
    """Single-row table used to generate race-safe IT-XXXXX ticket IDs."""
    __tablename__ = "ticket_counter"

    id = db.Column(db.Integer, primary_key=True)  # always 1
    last_ticket_number = db.Column(db.Integer, nullable=False, default=0)


def generate_ticket_id(db_session) -> str:
    """
    Generate the next IT-XXXXX ticket ID.

    ticket_str_id = 'IT-00001' string used in URLs and human-readable fields.
    ticket.id     = integer PK (autoincrement) used for FK relationships.

    Uses SELECT ... FOR UPDATE equivalent via with_for_update() to prevent
    duplicate IDs under concurrent requests.
    """
    counter = db_session.execute(
        select(TicketCounter).with_for_update()
    ).scalar_one_or_none()
    if counter is None:
        counter = TicketCounter(id=1, last_ticket_number=0)
        db_session.add(counter)
        db_session.flush()
    counter.last_ticket_number += 1
    db_session.flush()
    return f"IT-{counter.last_ticket_number:05d}"


class Ticket(db.Model):
    __tablename__ = "tickets"

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.String(12), nullable=False, unique=True)  # IT-XXXXX
    employee_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    employee_name = db.Column(db.String(120), nullable=False)
    employee_email = db.Column(db.String(254), nullable=False)
    issue_description = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(60))
    ai_recommendation = db.Column(db.Text)
    routing_method = db.Column(db.String(20), nullable=False, default="manual")
    routed_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    routing_reason = db.Column(db.Text)
    priority = db.Column(db.String(20), nullable=False, default="Normal")
    status = db.Column(db.String(30), nullable=False, default="Open")
    assigned_engineer_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=True
    )
    conversation_id = db.Column(
        db.Integer, db.ForeignKey("conversations.id"), nullable=True, index=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    resolved_at = db.Column(db.DateTime, nullable=True)
    employee_confirmed = db.Column(db.Boolean, default=False)
    resolution_method = db.Column(db.String(20), nullable=True)
    resolution_notes = db.Column(db.Text)
    resolved_by_name = db.Column(db.String(120), nullable=True)
    resolved_by_role = db.Column(db.String(60), nullable=True)
    resolved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    resolved_by_email = db.Column(db.String(254), nullable=True)
    closed_at = db.Column(db.DateTime, nullable=True)
    closed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    closed_by_name = db.Column(db.String(120), nullable=True)
    closed_by_email = db.Column(db.String(254), nullable=True)
    closed_by_role = db.Column(db.String(60), nullable=True)
    closing_notes = db.Column(db.Text, nullable=True)

    # Relationships
    employee = db.relationship("User", foreign_keys=[employee_id], backref="tickets")
    assigned_engineer = db.relationship(
        "User", foreign_keys=[assigned_engineer_id], backref="assigned_tickets"
    )
    resolved_by_user = db.relationship("User", foreign_keys=[resolved_by_id])
    closed_by_user = db.relationship("User", foreign_keys=[closed_by_id])
    conversation = db.relationship("Conversation", foreign_keys=[conversation_id])
    activities = db.relationship(
        "TicketActivity",
        backref="ticket",
        cascade="all, delete-orphan",
        lazy="dynamic",
        foreign_keys="TicketActivity.ticket_id",
    )

    __table_args__ = (
        db.Index("ix_tickets_employee_id", "employee_id"),
        db.Index("ix_tickets_status", "status"),
        db.Index("ix_tickets_priority", "priority"),
        db.Index("ix_tickets_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<Ticket {self.ticket_id} [{self.status}]>"


class TicketActivity(db.Model):
    """Append-only audit log. Never UPDATE or DELETE rows in this table."""
    __tablename__ = "ticket_activity"

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(
        db.Integer, db.ForeignKey("tickets.id"), nullable=False
    )  # integer PK FK
    changed_by = db.Column(db.String(60), nullable=False)  # user id string or 'system'
    old_status = db.Column(db.String(30))
    new_status = db.Column(db.String(30))
    note = db.Column(db.Text)
    is_internal = db.Column(db.Boolean, default=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.Index("ix_ticket_activity_ticket_id", "ticket_id"),)

    def __repr__(self) -> str:
        return f"<TicketActivity ticket_id={self.ticket_id} {self.old_status}->{self.new_status}>"


class SupportAssignment(db.Model):
    __tablename__ = "support_assignments"

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("tickets.id"), nullable=False)
    engineer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    assigned_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    assigned_at = db.Column(db.DateTime, default=datetime.utcnow)

    ticket = db.relationship("Ticket", backref="assignments")
    engineer = db.relationship("User", foreign_keys=[engineer_id])
    assigner = db.relationship("User", foreign_keys=[assigned_by])
