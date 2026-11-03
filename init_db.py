"""
init_db.py — Creates all tables and seeds initial data.
Run: python init_db.py

All model modules are imported before db.create_all() to ensure SQLAlchemy
registers all table metadata.
"""

import os
import re
import sys
from datetime import datetime, timedelta

# Ensure the project root is on the path
sys.path.insert(0, os.path.dirname(__file__))

from app import create_app
from models import db

# Explicitly import all models to register their metadata
from models.user import User
from models.ticket import Ticket, TicketCounter, TicketActivity, SupportAssignment, generate_ticket_id
from models.conversation import Conversation, Message
from models.knowledge_base import KBArticle, _slugify
from models.notification import Notification
from models.settings import SystemSetting
from models.feedback import EmployeeFeedback
from sqlalchemy import inspect, text


def seed_data(app):
    with app.app_context():
        # Lightweight forward migration for databases created before chat
        # conversations were linked to tickets.
        inspector = inspect(db.engine)
        if "tickets" in inspector.get_table_names() and "conversation_id" not in {
            column["name"] for column in inspector.get_columns("tickets")
        }:
            with db.engine.begin() as connection:
                connection.execute(text(
                    "ALTER TABLE tickets ADD COLUMN conversation_id INTEGER"
                ))
                connection.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_tickets_conversation_id "
                    "ON tickets (conversation_id)"
                ))
        inspector = inspect(db.engine)
        if "conversations" in inspector.get_table_names() and "active_ticket_id" not in {
            column["name"] for column in inspector.get_columns("conversations")
        }:
            with db.engine.begin() as connection:
                connection.execute(text(
                    "ALTER TABLE conversations ADD COLUMN active_ticket_id VARCHAR(12)"
                ))
        inspector = inspect(db.engine)
        if "tickets" in inspector.get_table_names() and "resolution_method" not in {
            column["name"] for column in inspector.get_columns("tickets")
        }:
            with db.engine.begin() as connection:
                connection.execute(text(
                    "ALTER TABLE tickets ADD COLUMN resolution_method VARCHAR(20)"
                ))
        inspector = inspect(db.engine)
        ticket_columns = (
            {column["name"] for column in inspector.get_columns("tickets")}
            if "tickets" in inspector.get_table_names()
            else set()
        )
        routing_migrations = (
            ("routing_method", "VARCHAR(20) NOT NULL DEFAULT 'manual'"),
            ("routed_at", "TIMESTAMP"),
            ("routing_reason", "TEXT"),
        )
        for column_name, column_type in routing_migrations:
            if "tickets" in inspector.get_table_names() and column_name not in ticket_columns:
                with db.engine.begin() as connection:
                    connection.execute(text(
                        f"ALTER TABLE tickets ADD COLUMN {column_name} {column_type}"
                    ))
        if "tickets" in inspector.get_table_names():
            with db.engine.begin() as connection:
                connection.execute(text(
                    "UPDATE tickets SET routed_at = created_at WHERE routed_at IS NULL"
                ))
                connection.execute(text(
                    "UPDATE tickets SET resolution_method = 'AI' "
                    "WHERE status = 'Resolved' AND lower(resolution_method) = 'ai'"
                ))
                connection.execute(text(
                    "UPDATE tickets SET resolution_method = 'Manual' "
                    "WHERE status = 'Resolved' AND resolution_method IS NULL"
                ))

        # Keep the ID sequence valid when upgrading databases with existing tickets.
        counter = db.session.get(TicketCounter, 1)
        if counter is None:
            highest_id = 0
            for existing_id, in db.session.execute(db.select(Ticket.ticket_id)).all():
                match = re.fullmatch(r"IT-(\d+)", existing_id or "")
                if match:
                    highest_id = max(highest_id, int(match.group(1)))
            db.session.add(TicketCounter(id=1, last_ticket_number=highest_id))
        else:
            highest_id = counter.last_ticket_number
            for existing_id, in db.session.execute(db.select(Ticket.ticket_id)).all():
                match = re.fullmatch(r"IT-(\d+)", existing_id or "")
                if match:
                    highest_id = max(highest_id, int(match.group(1)))
            counter.last_ticket_number = highest_id
        db.session.commit()

        # Idempotency check
        existing_admin = db.session.execute(
            db.select(User).where(User.email == "admin@resolveai.com")
        ).scalar_one_or_none()
        if existing_admin:
            print("Database already seeded. Skipping.")
            return

        print("Seeding users...")
        users = {}

        user_data = [
            ("Admin User", "admin@resolveai.com", "Admin@123", "admin", "IT"),
            ("Alex Engineer", "engineer1@resolveai.com", "Engineer@123", "engineer", "IT Support"),
            ("Sam Engineer", "engineer2@resolveai.com", "Engineer@123", "engineer", "IT Support"),
            ("John Doe", "john.doe@company.com", "Employee@123", "employee", "Engineering"),
            ("Jane Smith", "jane.smith@company.com", "Employee@123", "employee", "Marketing"),
            ("Bob Wilson", "bob.wilson@company.com", "Employee@123", "employee", "Finance"),
        ]
        for name, email, password, role, dept in user_data:
            u = User(name=name, email=email, role=role, department=dept)
            u.set_password(password)
            db.session.add(u)
            users[email] = u

        db.session.flush()

        print("Seeding TicketCounter...")
        # Seed counter to 0 — generate_ticket_id will increment it
        counter = db.session.get(TicketCounter, 1)
        if counter is None:
            counter = TicketCounter(id=1, last_ticket_number=0)
            db.session.add(counter)
            db.session.flush()

        print("Seeding tickets...")
        john = users["john.doe@company.com"]
        jane = users["jane.smith@company.com"]
        bob = users["bob.wilson@company.com"]
        eng1 = users["engineer1@resolveai.com"]
        eng2 = users["engineer2@resolveai.com"]

        tickets_data = [
            # (employee, category, issue, priority, status, confirmed, engineer, days_ago, resolved_ago)
            (john, "login", "Cannot log into the CRM application. Getting 'Invalid credentials' error.", "High", "Open", False, None, 1, None),
            (john, "crashes", "Microsoft Teams crashes every time I try to share my screen.", "Normal", "In Progress", False, eng1, 2, None),
            (jane, "password", "My password expired and the reset email is not arriving.", "High", "Escalated", False, eng1, 3, None),
            (jane, "api_errors", "Getting HTTP 500 error when submitting expense reports.", "Urgent", "Open", False, None, 1, None),
            (bob, "database", "Database query times out after 30 seconds in the finance dashboard.", "High", "In Progress", False, eng2, 4, None),
            (bob, "installation", "Cannot install the latest version of Adobe Acrobat.", "Normal", "Resolved", True, eng2, 10, 5),
            (john, "permissions", "Cannot access the shared network drive Q: — access denied.", "Normal", "Resolved", True, eng1, 12, 7),
            (jane, "web_bugs", "The HR portal freezes when clicking 'Submit' on leave requests.", "Normal", "Waiting for Employee", False, eng2, 2, None),
            (bob, "crashes", "Excel crashes when opening files larger than 50MB.", "Normal", "Open", False, None, 1, None),
            (john, "api_errors", "API integration with Salesforce is returning 503 errors.", "Urgent", "Escalated", False, eng1, 5, None),
        ]

        created_tickets = []
        for (emp, cat, issue, prio, status, confirmed, eng, days_ago, resolved_ago) in tickets_data:
            str_id = generate_ticket_id(db.session)
            t = Ticket(
                ticket_id=str_id,
                employee_id=emp.id,
                employee_name=emp.name,
                employee_email=emp.email,
                issue_description=issue,
                category=cat,
                priority=prio,
                status=status,
                employee_confirmed=confirmed,
                assigned_engineer_id=eng.id if eng else None,
                created_at=datetime.utcnow() - timedelta(days=days_ago),
                updated_at=datetime.utcnow() - timedelta(days=days_ago // 2),
                ai_recommendation=f"Troubleshooting steps for {cat} issue: 1. Check credentials 2. Clear cache 3. Contact IT if persists.",
            )
            if resolved_ago and confirmed:
                t.resolved_at = datetime.utcnow() - timedelta(days=resolved_ago)
            db.session.add(t)
            db.session.flush()

            # Add initial activity
            act = TicketActivity(
                ticket_id=t.id,
                changed_by=str(emp.id),
                old_status=None,
                new_status="Open",
                note="Ticket created",
                is_internal=False,
                timestamp=t.created_at,
            )
            db.session.add(act)

            if status != "Open":
                act2 = TicketActivity(
                    ticket_id=t.id,
                    changed_by=str(eng.id if eng else emp.id),
                    old_status="Open",
                    new_status=status,
                    note=f"Status updated to {status}",
                    is_internal=False,
                    timestamp=t.updated_at,
                )
                db.session.add(act2)

            if confirmed:
                act3 = TicketActivity(
                    ticket_id=t.id,
                    changed_by=str(emp.id),
                    old_status=status,
                    new_status="Resolved",
                    note="Employee confirmed resolution",
                    is_internal=False,
                    timestamp=t.resolved_at,
                )
                db.session.add(act3)

            created_tickets.append(t)

        # Update TicketCounter to match seeded tickets
        final_counter = db.session.execute(
            db.select(TicketCounter).where(TicketCounter.id == 1)
        ).scalar_one()
        # counter already has last_ticket_number = 10 after 10 generate calls
        print(f"TicketCounter at: {final_counter.last_ticket_number}")

        print("Seeding KB articles...")
        admin_user = users["admin@resolveai.com"]

        kb_articles = [
            {
                "title": "Resolving Login Failures",
                "category": "login",
                "symptoms": "Cannot log in, 'Invalid credentials' error, account locked",
                "causes": "Incorrect password, expired session, account lockout after failed attempts",
                "solution": "1. Verify email and password are correct.\n2. Check Caps Lock is off.\n3. Clear browser cookies and cache.\n4. Try incognito/private mode.\n5. Use 'Forgot Password' to reset.\n6. Contact IT if account is locked.",
                "escalation": "Escalate if account remains locked after password reset or if SSO/AD is suspected.",
                "status": "published",
            },
            {
                "title": "Password Reset Guide",
                "category": "password",
                "symptoms": "Forgot password, password expired, not receiving reset email",
                "causes": "Password expiry policy, spam filters blocking reset email, MFA device lost",
                "solution": "1. Click 'Forgot Password' on the login page.\n2. Check spam/junk folder for reset email.\n3. Ensure new password meets complexity requirements.\n4. For MFA issues, use backup codes.\n5. Contact IT to unlock account if needed.",
                "escalation": "Escalate if MFA device is lost or if account cannot be verified.",
                "status": "published",
            },
            {
                "title": "Troubleshooting Application Crashes",
                "category": "crashes",
                "symptoms": "App freezes, crashes on startup, runtime errors, not responding",
                "causes": "Corrupt cache, outdated version, insufficient memory, conflicting software",
                "solution": "1. Close and reopen the application.\n2. Clear application cache.\n3. Update to the latest version.\n4. Run as Administrator.\n5. Check Windows Event Viewer for crash logs.\n6. Reinstall if issue persists.",
                "escalation": "Escalate if crash logs show memory corruption or if multiple users are affected.",
                "status": "published",
            },
            {
                "title": "HTTP 500 Internal Server Error",
                "category": "api_errors",
                "symptoms": "HTTP 500 error, server error page, application throws 500",
                "causes": "Unhandled server exception, database connection failure, misconfiguration",
                "solution": "1. Refresh the page and try again.\n2. Check server logs for the specific error.\n3. Verify database connectivity.\n4. Check recent deployments for breaking changes.\n5. Restart the application service if needed.",
                "escalation": "Escalate immediately if affecting multiple users or production systems.",
                "status": "published",
            },
            {
                "title": "HTTP 503 Service Unavailable",
                "category": "api_errors",
                "symptoms": "HTTP 503 error, service unavailable, server overloaded",
                "causes": "Server overload, maintenance window, upstream service down",
                "solution": "1. Wait 5 minutes and try again — 503 is often temporary.\n2. Check the service status page.\n3. Verify if a maintenance window is scheduled.\n4. Check upstream dependencies (CDN, database).\n5. Contact the service provider if the outage persists.",
                "escalation": "Escalate if the service is down for more than 30 minutes.",
                "status": "published",
            },
            {
                "title": "API Connection Failures",
                "category": "api_errors",
                "symptoms": "API returns errors, connection refused, timeout, 502/504 errors",
                "causes": "Network issues, firewall blocking, API key expired, rate limiting",
                "solution": "1. Check network connectivity.\n2. Verify the API endpoint URL is correct.\n3. Check API key validity and permissions.\n4. Review rate limits — you may be throttled.\n5. Test with a simple curl/Postman request.\n6. Check server-side logs for detailed errors.",
                "escalation": "Escalate if the API is a production dependency and the outage affects business operations.",
                "status": "published",
            },
            {
                "title": "Database Connection Issues",
                "category": "database",
                "symptoms": "Cannot connect to database, connection timeout, too many connections",
                "causes": "Database service down, connection pool exhausted, firewall rules, wrong credentials",
                "solution": "1. Verify the database service is running.\n2. Check connection string settings.\n3. Test connectivity on the database port.\n4. Review connection pool settings (max connections).\n5. Check database server logs.\n6. Verify firewall allows traffic on the DB port.",
                "escalation": "Escalate if the database is inaccessible for more than 15 minutes.",
                "status": "published",
            },
            {
                "title": "Software Update and Installation Failures",
                "category": "installation",
                "symptoms": "Update fails, installation error, installer crashes, rollback on install",
                "causes": "Insufficient permissions, disk space, antivirus interference, corrupt installer",
                "solution": "1. Run installer as Administrator.\n2. Temporarily disable antivirus.\n3. Ensure sufficient disk space.\n4. Download a fresh copy of the installer.\n5. Uninstall the current version before reinstalling.\n6. Check OS compatibility.",
                "escalation": "Escalate if Group Policy prevents installation or if a license issue is suspected.",
                "status": "published",
            },
        ]

        for art_data in kb_articles:
            slug = _slugify(art_data["title"])
            art = KBArticle(
                title=art_data["title"],
                slug=slug,
                category=art_data["category"],
                symptoms=art_data["symptoms"],
                causes=art_data["causes"],
                solution=art_data["solution"],
                escalation=art_data["escalation"],
                status=art_data["status"],
                created_by=admin_user.id,
            )
            db.session.add(art)

        print("Seeding system settings...")
        settings_data = [
            ("site_name", "ResolveAI"),
            ("email_notifications_enabled", "false"),
            ("default_sla_hours", "24"),
            ("support_email", "support@resolveai.com"),
        ]
        for key, value in settings_data:
            setting = SystemSetting(key=key, value=value)
            db.session.add(setting)

        db.session.commit()
        print("Database seeded successfully!")
        print("\nTest accounts:")
        print("  Admin:    admin@resolveai.com / Admin@123")
        print("  Engineer: engineer1@resolveai.com / Engineer@123")
        print("  Employee: john.doe@company.com / Employee@123")


if __name__ == "__main__":
    app = create_app("development")
    with app.app_context():
        db.create_all()
        print("Tables created.")
        seed_data(app)
