from datetime import datetime
from models import db


class Notification(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    type = db.Column(db.String(60))
    message = db.Column(db.Text, nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    related_ticket_id = db.Column(
        db.Integer, db.ForeignKey("tickets.id"), nullable=True
    )

    user = db.relationship("User", backref="notifications")
    related_ticket = db.relationship("Ticket", backref="notifications")

    __table_args__ = (db.Index("ix_notifications_user_id", "user_id"),)

    def __repr__(self) -> str:
        return f"<Notification {self.id} user={self.user_id} read={self.is_read}>"
