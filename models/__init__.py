from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

# Import all models so SQLAlchemy registers their metadata before create_all()
from models.user import User  # noqa: E402, F401
from models.ticket import Ticket, TicketCounter, TicketActivity, SupportAssignment  # noqa: E402, F401
from models.conversation import Conversation, Message  # noqa: E402, F401
from models.knowledge_base import KBArticle  # noqa: E402, F401
from models.notification import Notification  # noqa: E402, F401
from models.settings import SystemSetting  # noqa: E402, F401
from models.feedback import EmployeeFeedback  # noqa: E402, F401
from models.login_attempt import LoginAttempt  # noqa: E402, F401
from models.ai_clearing import AiClearingJob, AiClearingJobItem  # noqa: E402, F401

__all__ = [
    "db",
    "User",
    "Ticket",
    "TicketCounter",
    "TicketActivity",
    "SupportAssignment",
    "Conversation",
    "Message",
    "KBArticle",
    "Notification",
    "SystemSetting",
    "EmployeeFeedback",
    "LoginAttempt",
    "AiClearingJob",
    "AiClearingJobItem",
]
