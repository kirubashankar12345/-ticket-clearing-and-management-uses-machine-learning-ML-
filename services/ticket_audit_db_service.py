import os
import sqlite3
from datetime import datetime

AUDIT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "instance",
    "ticket_audit.db"
)


class TicketAuditDBService:
    """Manages the separate audit database specifically tracking:
    1. Who created the ticket (Employee Name, ID, Email, Problem Raised, Timestamp)
    2. Who cleared the ticket (Resolver Name, Role, AI vs Human, Solution Statement, Timestamp)
    """

    @classmethod
    def get_connection(cls):
        os.makedirs(os.path.dirname(AUDIT_DB_PATH), exist_ok=True)
        conn = sqlite3.connect(AUDIT_DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    @classmethod
    def init_database(cls):
        """Creates the separate ticket_clearance_audit table if it doesn't exist."""
        with cls.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ticket_clearance_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id VARCHAR(12) NOT NULL UNIQUE,
                    
                    -- WHO CREATED THE TICKET
                    creator_employee_id INTEGER NOT NULL,
                    creator_employee_name VARCHAR(120) NOT NULL,
                    creator_employee_email VARCHAR(254) NOT NULL,
                    problem_statement TEXT NOT NULL,
                    category VARCHAR(60),
                    priority VARCHAR(20),
                    created_at TIMESTAMP NOT NULL,
                    
                    -- WHO CLEARED THE TICKET
                    status VARCHAR(30) NOT NULL,
                    cleared_by_id INTEGER,
                    cleared_by_name VARCHAR(120),
                    cleared_by_email VARCHAR(254),
                    cleared_by_role VARCHAR(60),
                    resolution_method VARCHAR(30),
                    solution_statement TEXT,
                    cleared_at TIMESTAMP,
                    duration_minutes INTEGER,
                    
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

    @classmethod
    def record_ticket_created(cls, ticket):
        """Logs initial creation to the separate database."""
        cls.init_database()
        with cls.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO ticket_clearance_audit (
                    ticket_id,
                    creator_employee_id,
                    creator_employee_name,
                    creator_employee_email,
                    problem_statement,
                    category,
                    priority,
                    created_at,
                    status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticket_id) DO UPDATE SET
                    creator_employee_name=excluded.creator_employee_name,
                    creator_employee_email=excluded.creator_employee_email,
                    problem_statement=excluded.problem_statement,
                    category=excluded.category,
                    priority=excluded.priority,
                    updated_at=CURRENT_TIMESTAMP
            """, (
                ticket.ticket_id,
                ticket.employee_id,
                ticket.employee_name or f"Employee #{ticket.employee_id}",
                ticket.employee_email or "unknown@company.com",
                ticket.issue_description or "",
                ticket.category or "general",
                ticket.priority or "Normal",
                ticket.created_at or datetime.utcnow(),
                ticket.status or "Open"
            ))
            conn.commit()

    @classmethod
    def record_ticket_cleared(
        cls,
        ticket,
        cleared_by_name: str = None,
        cleared_by_role: str = None,
        solution_statement: str = None,
    ):
        """Records the resolution and who cleared the ticket into the separate database."""
        cls.init_database()

        # Determine who cleared
        resolver_name = cleared_by_name or getattr(ticket, "resolved_by_name", None)
        resolver_role = cleared_by_role or getattr(ticket, "resolved_by_role", None)
        method = ticket.resolution_method or ("AI" if ticket.routing_method == "ai" else "Manual")

        if not resolver_name:
            if method == "AI":
                resolver_name = "ResolveAI Automated Engine"
                resolver_role = "AI Support Engine"
            else:
                resolver_name = "System Administrator"
                resolver_role = "IT Support Administrator"

        solution = (
            solution_statement
            or ticket.resolution_notes
            or ticket.ai_recommendation
            or "Issue investigated and resolved successfully."
        )

        resolved_time = ticket.resolved_at or datetime.utcnow()
        duration_mins = None
        if ticket.created_at and resolved_time:
            duration_mins = int((resolved_time - ticket.created_at).total_seconds() / 60)

        resolver_id = getattr(ticket, "resolved_by_id", None)
        resolver_email = getattr(ticket, "resolved_by_email", None)
        if method == "Manual" and not resolver_id:
            resolver_id = 1
            resolver_name = resolver_name or "Admin User"
            resolver_email = resolver_email or "admin@resolveai.com"
            resolver_role = resolver_role or "IT Support Administrator"
        elif method == "AI":
            resolver_email = resolver_email or "ai-support@resolveai.com"

        with cls.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO ticket_clearance_audit (
                    ticket_id,
                    creator_employee_id,
                    creator_employee_name,
                    creator_employee_email,
                    problem_statement,
                    category,
                    priority,
                    created_at,
                    status,
                    cleared_by_id,
                    cleared_by_name,
                    cleared_by_email,
                    cleared_by_role,
                    resolution_method,
                    solution_statement,
                    cleared_at,
                    duration_minutes,
                    closed_at,
                    closed_by_id,
                    closed_by_name,
                    closed_by_email,
                    closed_by_role,
                    closing_notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticket_id) DO UPDATE SET
                    status=excluded.status,
                    cleared_by_id=excluded.cleared_by_id,
                    cleared_by_name=excluded.cleared_by_name,
                    cleared_by_email=excluded.cleared_by_email,
                    cleared_by_role=excluded.cleared_by_role,
                    resolution_method=excluded.resolution_method,
                    solution_statement=excluded.solution_statement,
                    cleared_at=excluded.cleared_at,
                    duration_minutes=excluded.duration_minutes,
                    closed_at=excluded.closed_at,
                    closed_by_id=excluded.closed_by_id,
                    closed_by_name=excluded.closed_by_name,
                    closed_by_email=excluded.closed_by_email,
                    closed_by_role=excluded.closed_by_role,
                    closing_notes=excluded.closing_notes,
                    updated_at=CURRENT_TIMESTAMP
            """, (
                ticket.ticket_id,
                ticket.employee_id,
                ticket.employee_name or f"Employee #{ticket.employee_id}",
                ticket.employee_email or "unknown@company.com",
                ticket.issue_description or "",
                ticket.category or "general",
                ticket.priority or "Normal",
                ticket.created_at or datetime.utcnow(),
                ticket.status or "Resolved",
                resolver_id,
                resolver_name,
                resolver_email,
                resolver_role,
                method,
                solution,
                resolved_time,
                duration_mins,
                getattr(ticket, "closed_at", None),
                getattr(ticket, "closed_by_id", None),
                getattr(ticket, "closed_by_name", None),
                getattr(ticket, "closed_by_email", None),
                getattr(ticket, "closed_by_role", None),
                getattr(ticket, "closing_notes", None)
            ))
            conn.commit()

    @classmethod
    def sync_from_main_db(cls):
        """Scans all tickets in the main resolveai.db and ensures the separate
        ticket_audit.db has every ticket with who created and who cleared it.
        """
        cls.init_database()
        from models import db
        from models.ticket import Ticket

        tickets = db.session.execute(db.select(Ticket)).scalars().all()
        for t in tickets:
            cls.record_ticket_created(t)
            if t.status in ("Resolved", "Closed"):
                cls.record_ticket_cleared(t)

    @classmethod
    def get_all_records(cls, status_filter="all", method_filter="all"):
        """Returns all records from the separate database."""
        cls.init_database()
        with cls.get_connection() as conn:
            cursor = conn.cursor()
            query = "SELECT * FROM ticket_clearance_audit WHERE 1=1"
            params = []
            if status_filter == "resolved":
                query += " AND status = 'Resolved'"
            elif status_filter == "closed":
                query += " AND status = 'Closed'"
            elif status_filter == "open":
                query += " AND status NOT IN ('Resolved', 'Closed')"

            if method_filter == "ai":
                query += " AND resolution_method = 'AI'"
            elif method_filter == "manual":
                query += " AND resolution_method = 'Manual'"

            query += " ORDER BY id DESC"
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    @classmethod
    def get_audit_stats(cls):
        """Summary metrics from the separate audit database."""
        cls.init_database()
        with cls.get_connection() as conn:
            cursor = conn.cursor()
            total = cursor.execute("SELECT COUNT(*) FROM ticket_clearance_audit").fetchone()[0]
            resolved = cursor.execute("SELECT COUNT(*) FROM ticket_clearance_audit WHERE status = 'Resolved'").fetchone()[0]
            closed = cursor.execute("SELECT COUNT(*) FROM ticket_clearance_audit WHERE status = 'Closed'").fetchone()[0]
            ai_cleared = cursor.execute("SELECT COUNT(*) FROM ticket_clearance_audit WHERE status IN ('Resolved', 'Closed') AND resolution_method = 'AI'").fetchone()[0]
            manual_cleared = cursor.execute("SELECT COUNT(*) FROM ticket_clearance_audit WHERE status IN ('Resolved', 'Closed') AND resolution_method = 'Manual'").fetchone()[0]
            return {
                "total": total,
                "resolved": resolved,
                "closed": closed,
                "ai_cleared": ai_cleared,
                "manual_cleared": manual_cleared,
                "db_file": AUDIT_DB_PATH
            }
