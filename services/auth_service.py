import re
from datetime import datetime, timedelta
from flask import current_app
from sqlalchemy import select
from models import db
from models.user import User
from services.exceptions import EmailAlreadyExistsError


PASSWORD_RE = re.compile(
    r"^(?=.*[A-Z])(?=.*\d).{8,}$"
)


class AuthService:

    @staticmethod
    def register_employee(
        name: str, email: str, password: str, department: str = None
    ) -> User:
        """Register a new employee account."""
        email = email.strip().lower() if email else ""
        if not name or not name.strip():
            raise ValueError("Name is required.")
        if not re.match(r"^[^@]+@[^@]+\.[^@]+$", email):
            raise ValueError("Invalid email address.")
        if not PASSWORD_RE.match(password):
            raise ValueError(
                "Password must be at least 8 characters and contain "
                "at least one uppercase letter and one number."
            )

        existing = db.session.execute(
            select(User).where(User.email == email)
        ).scalar_one_or_none()
        if existing is not None:
            raise EmailAlreadyExistsError(f"Email {email!r} is already registered.")

        user = User(
            name=name.strip(),
            email=email,
            role="employee",
            department=department,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        return user

    @staticmethod
    def authenticate(email: str, password: str) -> User | None:
        """
        Verify credentials. Returns User on success, None on failure.
        Also handles account lockout tracking.
        """
        normalized_email = email.strip().lower() if email else ""
        user = db.session.execute(
            select(User).where(User.email == normalized_email)
        ).scalar_one_or_none()

        if user is None:
            return None

        if user.is_locked():
            return None

        if not user.is_active:
            return None

        if user.check_password(password):
            # Reset failed counter on success
            user.failed_logins = 0
            user.locked_until = None
            db.session.commit()
            return user
        else:
            user.failed_logins = (user.failed_logins or 0) + 1
            if user.failed_logins >= 10:
                user.locked_until = datetime.utcnow() + timedelta(minutes=15)
                current_app.logger.warning(
                    "Account locked: %s after %d failures", email, user.failed_logins
                )
            db.session.commit()
            return None

    @staticmethod
    def load_user(user_id: int) -> User | None:
        return db.session.get(User, int(user_id))
