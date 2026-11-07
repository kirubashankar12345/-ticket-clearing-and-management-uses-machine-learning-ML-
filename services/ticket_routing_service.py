from datetime import datetime, timedelta

from flask import current_app
from sqlalchemy import func, select

from models import db
from models.ticket import Ticket


class TicketRoutingService:
    """Central volume-based routing policy for new support tickets."""

    CRITICAL_TERMS = (
        "security incident",
        "security breach",
        "possible data breach",
        "data breach",
        "account compromised",
        "account hacked",
        "unauthorized access",
        "credentials compromised",
        "phishing attack",
        "data exfiltration",
        "system compromised",
        "ransomware",
        "malware infection",
        "production outage",
        "production down",
        "production is down",
        "prod down",
        "prod is down",
        "entire system is down",
        "service outage",
        "all users are affected",
    )

    @classmethod
    def is_critical_issue(cls, issue_description: str) -> bool:
        description = (issue_description or "").lower()
        return any(term in description for term in cls.CRITICAL_TERMS)

    @classmethod
    def suggested_priority(cls, issue_description: str) -> str:
        description = (issue_description or "").lower()
        if cls.is_critical_issue(description):
            return "Urgent"
        if any(term in description for term in ("cannot work", "can't work", "all users", "system down", "blocked")):
            return "High"
        if any(term in description for term in ("slow", "minor", "intermittent")):
            return "Low"
        return "Normal"

    @classmethod
    def route_new_ticket(
        cls,
        issue_description: str,
        priority: str = None,
        fallback_reason: str = None
    ) -> dict:
        """Priority-based and policy routing:
        - High and Urgent priorities -> Manual ticket (Manual clearing only by human support)
        - Low and Normal priorities -> Simple ticket (Routed to AI, cleared automatically by ResolveAI)
        """
        recent_count = cls.recent_ticket_count()

        # If priority is not specified or invalid, infer from description
        if not priority or priority not in ("Low", "Normal", "High", "Urgent"):
            priority = cls.suggested_priority(issue_description)

        # 1. Critical issue keywords or High / Urgent priority -> MANUAL TICKET
        if cls.is_critical_issue(issue_description) or priority in ("High", "Urgent"):
            status = "Escalated" if (priority == "Urgent" or cls.is_critical_issue(issue_description)) else "Open"
            return {
                "method": "manual",
                "status": status,
                "priority": priority,
                "reason": (
                    f"{priority} priority ticket routed to manual human support. "
                    f"High and Urgent issues require manual clearance by an Administrator or Engineer."
                ),
                "recent_count": recent_count,
            }

        # 2. Explicit Fallback reason -> Manual
        if fallback_reason:
            return {
                "method": "manual",
                "status": "Open",
                "priority": priority,
                "reason": fallback_reason,
                "recent_count": recent_count,
            }

        # 3. Simple tickets (Low / Normal) -> AI ROUTED & AI CLEARED
        return {
            "method": "ai",
            "status": "Open",
            "priority": priority,
            "reason": (
                f"Simple ticket ({priority} priority) routed to ResolveAI Automated Engine "
                f"for automated AI clearing."
            ),
            "recent_count": recent_count,
        }

    @staticmethod
    def threshold() -> int:
        value = int(current_app.config.get("AI_ROUTING_THRESHOLD", 5))
        if value < 1:
            raise ValueError("AI_ROUTING_THRESHOLD must be at least 1.")
        return value

    @staticmethod
    def window_minutes() -> int:
        value = int(current_app.config.get("AI_ROUTING_WINDOW_MINUTES", 60))
        if value < 1:
            raise ValueError("AI_ROUTING_WINDOW_MINUTES must be at least 1.")
        return value

    @classmethod
    def recent_ticket_count(cls) -> int:
        cutoff = datetime.utcnow() - timedelta(minutes=cls.window_minutes())
        return db.session.execute(
            select(func.count(Ticket.id)).where(Ticket.created_at >= cutoff)
        ).scalar_one()

    @classmethod
    def current_mode(cls) -> dict:
        count = cls.recent_ticket_count()
        threshold = cls.threshold()
        return {
            "mode": "ai" if count >= threshold else "manual",
            "recent_count": count,
            "threshold": threshold,
            "window_minutes": cls.window_minutes(),
        }

    @classmethod
    def admin_overview(cls) -> dict:
        count = lambda *criteria: db.session.execute(
            select(func.count(Ticket.id)).where(*criteria)
        ).scalar_one()
        mode = cls.current_mode()
        ai_routed = count(Ticket.routing_method == "ai")
        manual_routed = count(Ticket.routing_method == "manual")
        total_routed = ai_routed + manual_routed

        ai_open = count(
            Ticket.routing_method == "ai",
            Ticket.status.notin_(("Resolved", "Closed")),
        )
        manual_open = count(
            Ticket.routing_method == "manual",
            Ticket.status.notin_(("Resolved", "Closed")),
        )
        total_open = ai_open + manual_open

        ai_share_pct = round((ai_routed / total_routed * 100), 1) if total_routed > 0 else 50.0
        manual_share_pct = round((manual_routed / total_routed * 100), 1) if total_routed > 0 else 50.0

        ai_open_pct = round((ai_open / total_open * 100), 1) if total_open > 0 else 50.0
        manual_open_pct = round((manual_open / total_open * 100), 1) if total_open > 0 else 50.0

        recent_count = mode["recent_count"]
        threshold = mode["threshold"]
        load_pct = min(round((recent_count / threshold * 100), 1) if threshold > 0 else 100, 100)

        return {
            "eligible_for_ai": count(
                Ticket.status.notin_(("Resolved", "Closed")),
                Ticket.priority.in_(("Low", "Normal")),
                Ticket.assigned_engineer_id.is_(None),
            ),
            "current_mode": mode["mode"],
            "recent_ticket_count": recent_count,
            "routing_threshold": threshold,
            "routing_window_minutes": mode["window_minutes"],
            "ai_routed": ai_routed,
            "manual_routed": manual_routed,
            "total_routed": total_routed,
            "ai_open": ai_open,
            "manual_open": manual_open,
            "total_open": total_open,
            "ai_share_pct": ai_share_pct,
            "manual_share_pct": manual_share_pct,
            "ai_open_pct": ai_open_pct,
            "manual_open_pct": manual_open_pct,
            "load_pct": load_pct,
            "is_surge": recent_count >= threshold,
            "unassigned": count(
                Ticket.assigned_engineer_id.is_(None),
                Ticket.status.notin_(("Resolved", "Closed")),
            ),
            "ai_resolved": count(
                Ticket.resolution_method == "AI",
                Ticket.status == "Resolved",
            ),
            "manual_resolved": count(
                Ticket.resolution_method == "Manual",
                Ticket.status == "Resolved",
            ),
            "awaiting_confirmation": count(
                Ticket.status == "Waiting for Employee",
                Ticket.routing_method == "ai",
            ),
            "escalated": count(Ticket.status == "Escalated"),
            "open_unresolved": count(
                Ticket.status.notin_(("Resolved", "Closed"))
            ),
            "resolved": count(Ticket.status == "Resolved"),
            "unresolved": count(Ticket.status.notin_(("Resolved", "Closed"))),
            "pending_manual": count(
                Ticket.routing_method == "manual",
                Ticket.status == "Open",
            ),
            "in_progress_manual": count(
                Ticket.routing_method == "manual",
                Ticket.status == "In Progress",
            ),
            "escalated_manual": count(
                Ticket.routing_method == "manual",
                Ticket.status == "Escalated",
            ),
        }

    @staticmethod
    def manual_queue_overview() -> dict:
        count = lambda *criteria: db.session.execute(
            select(func.count(Ticket.id)).where(*criteria)
        ).scalar_one()
        manual = Ticket.routing_method == "manual"
        return {
            "pending": count(manual, Ticket.status == "Open"),
            "in_progress": count(manual, Ticket.status == "In Progress"),
            "waiting_for_employee": count(
                manual, Ticket.status == "Waiting for Employee"
            ),
            "escalated": count(manual, Ticket.status == "Escalated"),
            "resolved": count(manual, Ticket.status == "Resolved"),
        }
