from datetime import datetime
from models import db


class EmployeeFeedback(db.Model):
    __tablename__ = "employee_feedback"

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(
        db.Integer, db.ForeignKey("tickets.id"), unique=True, nullable=False
    )
    employee_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    rating = db.Column(db.Integer, nullable=False)  # 1-5
    comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    ticket = db.relationship(
        "Ticket", backref=db.backref("feedback", uselist=False)
    )
    employee = db.relationship("User")

    def __repr__(self) -> str:
        return f"<EmployeeFeedback ticket_id={self.ticket_id} rating={self.rating}>"
