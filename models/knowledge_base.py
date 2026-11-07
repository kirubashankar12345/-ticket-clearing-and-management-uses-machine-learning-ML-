import re
from datetime import datetime
from models import db


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:80]


class KBArticle(db.Model):
    __tablename__ = "knowledge_base_articles"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(220), nullable=False, unique=True)
    category = db.Column(db.String(60))
    symptoms = db.Column(db.Text)
    causes = db.Column(db.Text)
    solution = db.Column(db.Text, nullable=False)
    escalation = db.Column(db.Text)
    status = db.Column(db.String(20), default="draft")  # draft | published | archived
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    author = db.relationship("User", backref="kb_articles")

    def __repr__(self) -> str:
        return f"<KBArticle {self.id} '{self.title}'>"
