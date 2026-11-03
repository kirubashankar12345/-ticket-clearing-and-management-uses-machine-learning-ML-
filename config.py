import os
from dotenv import load_dotenv
from sqlalchemy.engine import make_url

load_dotenv()

basedir = os.path.abspath(os.path.dirname(__file__))


def resolve_database_url() -> str:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        return "sqlite:///" + os.path.join(basedir, "instance", "resolveai.db")
    if database_url.startswith("sqlite:///"):
        relative_path = database_url[len("sqlite:///") :]
        if relative_path and not os.path.isabs(relative_path):
            return "sqlite:///" + os.path.abspath(os.path.join(basedir, relative_path))
    return database_url


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    WTF_CSRF_ENABLED = True
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
    OPENAI_API_BASE = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")
    OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    MAIL_ENABLED = os.environ.get("MAIL_ENABLED", "false").lower() == "true"
    MAIL_SERVER = os.environ.get("MAIL_SERVER", "localhost")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", 587))
    MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "true").lower() == "true"
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER", "noreply@resolveai.com")
    TICKETS_PER_PAGE = 20
    AI_ROUTING_THRESHOLD = int(os.environ.get("AI_ROUTING_THRESHOLD", "5"))
    AI_ROUTING_WINDOW_MINUTES = int(os.environ.get("AI_ROUTING_WINDOW_MINUTES", "60"))


class DevelopmentConfig(Config):
    DEBUG = True
    SESSION_COOKIE_SECURE = False
    SQLALCHEMY_DATABASE_URI = resolve_database_url()


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")

    def __init__(self):
        if not os.environ.get("SECRET_KEY"):
            raise RuntimeError(
                "SECRET_KEY environment variable must be set in production."
            )
        if not self.SQLALCHEMY_DATABASE_URI:
            raise RuntimeError(
                "DATABASE_URL environment variable must be set in production."
            )


class TestingConfig(Config):
    TESTING = True
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    if os.environ.get("TEST_DATABASE_URL"):
        test_database_url = os.environ["TEST_DATABASE_URL"]
        test_database = make_url(test_database_url)
        if (
            test_database.database != "resolveai_test"
            or test_database.host not in {"localhost", "127.0.0.1", "::1"}
        ):
            raise RuntimeError(
                "TEST_DATABASE_URL must use the local, dedicated resolveai_test database."
            )
        SQLALCHEMY_DATABASE_URI = test_database_url
    WTF_CSRF_ENABLED = False
    SESSION_COOKIE_SECURE = False
    SERVER_NAME = None


config_map = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}
