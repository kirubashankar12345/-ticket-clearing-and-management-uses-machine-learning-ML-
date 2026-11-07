from sqlalchemy import select
from models import db
from models.settings import SystemSetting


class SettingsService:

    @staticmethod
    def get(key: str, default=None):
        """Retrieve a setting value by key."""
        row = db.session.execute(
            select(SystemSetting).where(SystemSetting.key == key)
        ).scalar_one_or_none()
        if row is None:
            return default
        return row.value

    @staticmethod
    def set(key: str, value: str) -> None:
        """Create or update a setting."""
        row = db.session.execute(
            select(SystemSetting).where(SystemSetting.key == key)
        ).scalar_one_or_none()
        if row is None:
            row = SystemSetting(key=key, value=value)
            db.session.add(row)
        else:
            row.value = value
        db.session.commit()

    @staticmethod
    def get_all() -> dict:
        rows = db.session.execute(select(SystemSetting)).scalars().all()
        return {r.key: r.value for r in rows}

    @staticmethod
    def set_many(settings: dict) -> None:
        for key, value in settings.items():
            SettingsService.set(key, value)
