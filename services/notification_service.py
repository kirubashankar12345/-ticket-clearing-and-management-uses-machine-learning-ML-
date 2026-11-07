from flask import current_app
from sqlalchemy import select, func
from models import db
from models.notification import Notification


class NotificationService:

    @staticmethod
    def notify(
        user_id: int,
        notification_type: str,
        message: str,
        related_ticket_id: int = None,
    ) -> None:
        """Insert a notification. Non-critical — logs errors without raising."""
        try:
            n = Notification(
                user_id=user_id,
                type=notification_type,
                message=message,
                related_ticket_id=related_ticket_id,
            )
            db.session.add(n)
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            current_app.logger.error("NotificationService.notify failed: %s", exc)

    @staticmethod
    def get_unread_count(user_id: int) -> int:
        try:
            result = db.session.execute(
                select(func.count(Notification.id)).where(
                    Notification.user_id == user_id,
                    Notification.is_read == False,  # noqa: E712
                )
            ).scalar()
            return result or 0
        except Exception:
            return 0

    @staticmethod
    def get_notifications(user_id: int, limit: int = 20) -> list:
        try:
            return db.session.execute(
                select(Notification)
                .where(Notification.user_id == user_id)
                .order_by(Notification.created_at.desc())
                .limit(limit)
            ).scalars().all()
        except Exception:
            return []

    @staticmethod
    def mark_read(notification_id: int, user_id: int) -> None:
        try:
            n = db.session.get(Notification, notification_id)
            if n and n.user_id == user_id:
                n.is_read = True
                db.session.commit()
        except Exception as exc:
            db.session.rollback()
            current_app.logger.error("mark_read failed: %s", exc)

    @staticmethod
    def mark_all_read(user_id: int) -> None:
        try:
            notifications = db.session.execute(
                select(Notification).where(
                    Notification.user_id == user_id,
                    Notification.is_read == False,  # noqa: E712
                )
            ).scalars().all()
            for n in notifications:
                n.is_read = True
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            current_app.logger.error("mark_all_read failed: %s", exc)
