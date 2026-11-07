from sqlalchemy import select, or_
from models import db
from models.knowledge_base import KBArticle


class KBService:

    @staticmethod
    def search(query: str, limit: int = 20) -> list:
        """Search published KB articles by keyword."""
        if not query or not query.strip():
            return KBService.get_published(limit=limit)
        q = f"%{query.strip()[:100]}%"
        return db.session.execute(
            select(KBArticle)
            .where(
                KBArticle.status == "published",
                or_(
                    KBArticle.title.ilike(q),
                    KBArticle.symptoms.ilike(q),
                    KBArticle.solution.ilike(q),
                    KBArticle.causes.ilike(q),
                ),
            )
            .order_by(KBArticle.updated_at.desc())
            .limit(limit)
        ).scalars().all()

    @staticmethod
    def get_published(category: str = None, limit: int = 50) -> list:
        q = select(KBArticle).where(KBArticle.status == "published")
        if category:
            q = q.where(KBArticle.category == category)
        q = q.order_by(KBArticle.updated_at.desc()).limit(limit)
        return db.session.execute(q).scalars().all()

    @staticmethod
    def get_by_slug(slug: str) -> KBArticle | None:
        return db.session.execute(
            select(KBArticle).where(KBArticle.slug == slug)
        ).scalar_one_or_none()

    @staticmethod
    def get_all(page: int = 1, per_page: int = 20):
        return db.paginate(
            select(KBArticle).order_by(KBArticle.updated_at.desc()),
            page=page,
            per_page=per_page,
        )

    @staticmethod
    def get_context_for_ai(user_message: str) -> list:
        """Return up to 3 matching published KB articles for AI context injection."""
        articles = KBService.search(user_message, limit=3)
        return [
            {
                "title": a.title,
                "solution": (a.solution or "")[:300],
                "category": a.category,
            }
            for a in articles
        ]
