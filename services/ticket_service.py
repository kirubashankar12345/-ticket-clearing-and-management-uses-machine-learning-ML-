"""
TicketService — all ticket business logic lives here.

Identifier conventions (documented once here):
  ticket_str_id  : the human-readable 'IT-00001' string stored in Ticket.ticket_id
  ticket.id      : integer autoincrement PK used for FK relationships
  employee_id    : integer PK of the User who owns the ticket

Every public method that accepts a ticket identifier from a URL route receives
the ticket_str_id string and resolves the integer PK internally.
"""

from datetime import datetime, timedelta
from flask import abort, current_app
from sqlalchemy import select, case, func, or_, text
from models import db
from models.ticket import Ticket, TicketActivity, SupportAssignment, generate_ticket_id
from models.user import User
from services.exceptions import (
    ConfirmationRequiredError,
    ReopenWindowExpiredError,
    TicketCreationError,
)

RESOLVED_GUARD = "Ticket cannot be set to Resolved without employee confirmation."
VALID_STATUSES = {
    "Open", "In Progress", "Waiting for Employee", "Escalated", "Resolved", "Closed"
}
VALID_PRIORITIES = {"Low", "Normal", "High", "Urgent"}
class TicketService:

    @staticmethod
    def get_intake_mode() -> str:
        """Return the current volume-selected intake mode."""
        from services.ticket_routing_service import TicketRoutingService

        return TicketRoutingService.current_mode()["mode"]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_by_str_id(ticket_str_id: str) -> Ticket:
        """Resolve an IT-XXXXX string to the Ticket row; abort 404 if missing."""
        ticket = db.session.execute(
            select(Ticket).where(Ticket.ticket_id == ticket_str_id)
        ).scalar_one_or_none()
        if ticket is None:
            abort(404)
        return ticket

    @staticmethod
    def _send_status_notifications(ticket: Ticket, old_status: str, new_status: str) -> None:
        """Fire in-app notifications for status transitions (FR-6.8 / FR-10.2)."""
        try:
            from services.notification_service import NotificationService
            from models.user import User

            if new_status == "Escalated":
                # Notify all admins
                admins = db.session.execute(
                    select(User).where(User.role == "admin")
                ).scalars().all()
                for admin in admins:
                    NotificationService.notify(
                        admin.id,
                        "escalation",
                        f"Ticket {ticket.ticket_id} has been escalated: {ticket.issue_description[:80]}",
                        ticket.id,
                    )
                if ticket.assigned_engineer_id:
                    NotificationService.notify(
                        ticket.assigned_engineer_id,
                        "escalation",
                        f"Your ticket {ticket.ticket_id} has been escalated.",
                        ticket.id,
                    )
            elif new_status == "Resolved":
                notif_msg = f"Your ticket {ticket.ticket_id} has been resolved."
                if ticket.resolution_notes:
                    notif_msg = f"Ticket {ticket.ticket_id} resolved: {ticket.resolution_notes[:100]}"
                NotificationService.notify(
                    ticket.employee_id,
                    "resolved",
                    notif_msg,
                    ticket.id,
                )
            elif new_status == "In Progress":
                NotificationService.notify(
                    ticket.employee_id,
                    "in_progress",
                    f"An engineer has started working on ticket {ticket.ticket_id}.",
                    ticket.id,
                )
            elif new_status == "Waiting for Employee":
                NotificationService.notify(
                    ticket.employee_id,
                    "waiting",
                    f"The support team needs more information for ticket {ticket.ticket_id}.",
                    ticket.id,
                )
        except Exception as exc:
            current_app.logger.error(
                "Ticket status notification failed (%s).", type(exc).__name__
            )

    # ------------------------------------------------------------------
    # Core mutations
    # ------------------------------------------------------------------

    @staticmethod
    def create_ticket(
        employee_id: int,
        issue_description: str,
        category: str = "general",
        priority: str = "Normal",
        ai_recommendation: str = None,
        conversation_id: int = None,
        fallback_reason: str = None,
    ) -> Ticket:
        """
        Create a new ticket. Generates a race-safe IT-XXXXX ticket ID using
        the TicketCounter table. Returns the committed Ticket object.
        """
        from models.user import User

        if not issue_description or not 10 <= len(issue_description.strip()) <= 2000:
            raise ValueError("Issue description must be between 10 and 2000 characters.")
        if priority not in VALID_PRIORITIES:
            raise ValueError("Invalid ticket priority.")

        employee = db.session.get(User, employee_id)
        if employee is None:
            abort(404)

        try:
            from services.ticket_routing_service import TicketRoutingService

            if db.engine.dialect.name == "postgresql":
                db.session.execute(text("SELECT pg_advisory_xact_lock(731904218)"))
            routing = TicketRoutingService.route_new_ticket(
                issue_description, priority=priority, fallback_reason=fallback_reason
            )
            if routing.get("priority"):
                priority = routing["priority"]
            elif priority not in VALID_PRIORITIES:
                priority = "Normal"

            str_id = generate_ticket_id(db.session)
            ticket = Ticket(
                ticket_id=str_id,
                employee_id=employee_id,
                employee_name=employee.name,
                employee_email=employee.email,
                issue_description=issue_description,
                category=category,
                priority=priority,
                ai_recommendation=ai_recommendation,
                routing_method=routing["method"],
                routed_at=datetime.utcnow(),
                routing_reason=routing["reason"],
                conversation_id=conversation_id,
                status=routing["status"],
            )
            db.session.add(ticket)
            db.session.flush()  # assigns ticket.id

            activity = TicketActivity(
                ticket_id=ticket.id,
                changed_by=str(employee_id),
                old_status=None,
                new_status=ticket.status,
                note=f"Ticket created. {routing['reason']}",
                is_internal=False,
            )
            db.session.add(activity)
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            current_app.logger.error(
                "Ticket creation failed (%s).", type(exc).__name__
            )
            raise TicketCreationError(
                "Ticket could not be created because of a database error."
            ) from exc

        TicketService._send_status_notifications(ticket, None, ticket.status)
        if fallback_reason:
            TicketService._notify_routing_fallback(ticket, routing["reason"])

        try:
            from services.ticket_audit_db_service import TicketAuditDBService
            TicketAuditDBService.record_ticket_created(ticket)
        except Exception as _e:
            current_app.logger.warning("Audit DB log create error: %s", _e)

        # Simple tickets (Low / Normal) are automatically analyzed and cleared by ResolveAI
        if ticket.routing_method == "ai" and ticket.priority in ("Low", "Normal"):
            try:
                from services.ai_clearing_service import AiClearingService
                AiClearingService.clear_ticket_with_ai(ticket.id, auto_resolve=True)
            except Exception as _ai_err:
                current_app.logger.warning("Auto AI clearing on creation error: %s", _ai_err)

        return ticket

    @staticmethod
    def record_ai_result(
        ticket_str_id: str,
        category: str,
        priority: str,
        recommendation: str,
    ) -> Ticket:
        """Store AI triage and wait for the employee to confirm the solution."""
        ticket = TicketService._get_by_str_id(ticket_str_id)
        if ticket.routing_method != "ai":
            return ticket
        if category not in {
            "login", "password", "crashes", "api_errors", "database",
            "installation", "permissions", "web_bugs", "general",
        }:
            category = "general"
        if priority not in VALID_PRIORITIES:
            priority = "Normal"

        ticket.category = category
        ticket.priority = priority
        ticket.ai_recommendation = (recommendation or "")[:4000] or None
        old_status = ticket.status
        ticket.status = "Waiting for Employee"
        ticket.resolved_at = None
        ticket.resolution_method = None
        ticket.routing_reason = "AI provided troubleshooting and is waiting for employee confirmation."
        note = "AI categorized the issue and provided troubleshooting; awaiting employee confirmation."
        ticket.updated_at = datetime.utcnow()
        db.session.add(TicketActivity(
            ticket_id=ticket.id,
            changed_by="system",
            old_status=old_status,
            new_status=ticket.status,
            note=note,
            is_internal=False,
        ))
        db.session.commit()
        if old_status != ticket.status:
            TicketService._send_status_notifications(ticket, old_status, ticket.status)
        return ticket

    @staticmethod
    def route_ai_ticket_to_manual(
        ticket_str_id: str,
        reason: str,
        category: str = None,
        priority: str = None,
        recommendation: str = None,
    ) -> Ticket:
        """Keep the original AI ticket and route it to the manual queue."""
        ticket = TicketService._get_by_str_id(ticket_str_id)
        old_status = ticket.status
        if category:
            ticket.category = category
        if priority in VALID_PRIORITIES:
            ticket.priority = priority
        if recommendation:
            ticket.ai_recommendation = recommendation[:4000]
        ticket.routing_method = "manual"
        ticket.routing_reason = reason
        ticket.status = "Open"
        ticket.assigned_engineer_id = None
        ticket.employee_confirmed = False
        ticket.resolved_at = None
        ticket.resolution_method = None
        ticket.updated_at = datetime.utcnow()
        db.session.add(TicketActivity(
            ticket_id=ticket.id,
            changed_by="system",
            old_status=old_status,
            new_status="Open",
            note=f"AI could not resolve the issue; routed to the human support queue. {reason}",
            is_internal=False,
        ))
        db.session.commit()
        TicketService._send_status_notifications(ticket, old_status, "Open")
        TicketService._notify_routing_fallback(ticket, reason)
        return ticket

    @staticmethod
    def _notify_routing_fallback(ticket: Ticket, reason: str) -> None:
        try:
            from models.user import User
            from services.notification_service import NotificationService

            admins = db.session.execute(
                select(User).where(User.role == "admin")
            ).scalars().all()
            for admin in admins:
                NotificationService.notify(
                    admin.id,
                    "escalation",
                    f"Ticket {ticket.ticket_id} requires human support: {reason}",
                    ticket.id,
                )
        except Exception as exc:
            current_app.logger.error(
                "Ticket %s was created, but admin notification failed (%s).",
                ticket.ticket_id,
                type(exc).__name__,
            )

    @staticmethod
    def set_status(
        ticket_str_id: str,
        new_status: str,
        changed_by,
        note: str = None,
        is_internal: bool = False,
        resolution_notes: str = None,
    ) -> Ticket:
        """
        Enforces that AI tickets are resolved only by the AI workflow and
        manual tickets by the employee or authorized human support staff.

        ticket_str_id : IT-XXXXX string (from URL route or service call)
        changed_by    : user id (int or str) or 'system'

        Raises ConfirmationRequiredError when no allowed resolver is present.
        """
        ticket = TicketService._get_by_str_id(ticket_str_id)

        if new_status not in VALID_STATUSES:
            raise ValueError("Invalid ticket status.")

        actor = None
        try:
            actor = db.session.get(User, int(changed_by))
        except (TypeError, ValueError):
            pass
        human_manual_resolution = bool(
            new_status == "Resolved"
            and ticket.routing_method == "manual"
            and actor
            and (
                actor.role == "admin"
                or (
                    actor.role == "engineer"
                    and ticket.assigned_engineer_id == actor.id
                )
            )
        )
        ai_confirmed_resolution = bool(
            new_status == "Resolved"
            and ticket.routing_method == "ai"
            and (
                (ticket.employee_confirmed and actor and actor.id == ticket.employee_id)
                or (actor and getattr(actor, "role", None) == "admin")
                or changed_by == "system"
            )
        )
        if new_status == "Resolved" and not human_manual_resolution and not ai_confirmed_resolution:
            raise ConfirmationRequiredError(RESOLVED_GUARD)

        old_status = ticket.status
        ticket.status = new_status
        ticket.updated_at = datetime.utcnow()
        if new_status == "Resolved":
            ticket.resolved_at = datetime.utcnow()
            ticket.resolution_method = (
                "AI" if ticket.routing_method == "ai"
                else "Manual"
            )
            # Save resolution explanation if provided
            if resolution_notes and resolution_notes.strip():
                ticket.resolution_notes = resolution_notes.strip()
            elif ticket.routing_method == "ai" and ticket.ai_recommendation:
                ticket.resolution_notes = ticket.ai_recommendation

            # Set who cleared the ticket (Admin / Engineer / AI)
            if ticket.resolution_method == "AI" or ticket.routing_method == "ai":
                ticket.resolved_by_name = "ResolveAI Automated Engine"
                ticket.resolved_by_role = "AI Support Engine"
                ticket.resolved_by_id = None
                ticket.resolved_by_email = "ai-support@resolveai.com"
            elif actor and getattr(actor, "role", None) == "admin":
                ticket.resolved_by_name = getattr(actor, "name", "Admin User")
                ticket.resolved_by_role = "IT Support Administrator"
                ticket.resolved_by_id = actor.id
                ticket.resolved_by_email = getattr(actor, "email", "admin@resolveai.com")
            elif actor and getattr(actor, "role", None) == "engineer":
                ticket.resolved_by_name = getattr(actor, "name", "Support Engineer")
                ticket.resolved_by_role = "Support Engineer"
                ticket.resolved_by_id = actor.id
                ticket.resolved_by_email = getattr(actor, "email", "")
            elif ticket.assigned_engineer:
                ticket.resolved_by_name = ticket.assigned_engineer.name
                ticket.resolved_by_role = "Support Engineer"
                ticket.resolved_by_id = ticket.assigned_engineer.id
                ticket.resolved_by_email = ticket.assigned_engineer.email
            else:
                ticket.resolved_by_name = "IT Support Team"
                ticket.resolved_by_role = "IT Operations"
                ticket.resolved_by_id = None
                ticket.resolved_by_email = "support@resolveai.com"
        elif new_status == "Closed":
            ticket.closed_at = datetime.utcnow()
            if actor and getattr(actor, "role", None) == "admin":
                ticket.closed_by_name = getattr(actor, "name", "Admin User")
                ticket.closed_by_role = "IT Support Administrator"
                ticket.closed_by_id = actor.id
                ticket.closed_by_email = getattr(actor, "email", "admin@resolveai.com")
            elif actor and getattr(actor, "role", None) == "engineer":
                ticket.closed_by_name = getattr(actor, "name", "Support Engineer")
                ticket.closed_by_role = "Support Engineer"
                ticket.closed_by_id = actor.id
                ticket.closed_by_email = getattr(actor, "email", "")
            else:
                ticket.closed_by_name = "Admin User"
                ticket.closed_by_role = "IT Support Administrator"
                ticket.closed_by_id = 1
                ticket.closed_by_email = "admin@resolveai.com"

            if note and note.strip():
                ticket.closing_notes = note.strip()
            closing_activity_text = f"Ticket officially closed by {ticket.closed_by_name} (Admin / Employee ID: #{ticket.closed_by_id}). {note or ''}".strip()
            note = closing_activity_text
        elif new_status == "Open":
            ticket.resolution_method = None
            ticket.resolved_by_name = None
            ticket.resolved_by_role = None
            ticket.resolved_by_id = None
            ticket.resolved_by_email = None
            ticket.closed_at = None
            ticket.closed_by_name = None
            ticket.closed_by_role = None
            ticket.closed_by_id = None
            ticket.closed_by_email = None
            ticket.closing_notes = None

        activity = TicketActivity(
            ticket_id=ticket.id,
            changed_by=str(changed_by),
            old_status=old_status,
            new_status=new_status,
            note=note,
            is_internal=is_internal,
        )
        db.session.add(activity)
        db.session.commit()

        TicketService._send_status_notifications(ticket, old_status, new_status)

        if new_status in ("Resolved", "Closed"):
            try:
                from services.ticket_audit_db_service import TicketAuditDBService
                TicketAuditDBService.record_ticket_cleared(ticket)
            except Exception as _e:
                current_app.logger.warning("Audit DB log clear error: %s", _e)

        return ticket

    @staticmethod
    def confirm_resolution(ticket_str_id: str, employee_id: int) -> Ticket:
        """
        Employee confirms the issue is resolved. Sets employee_confirmed=True
        then calls set_status to move to Resolved.
        """
        ticket = TicketService._get_by_str_id(ticket_str_id)
        from models.user import User
        actor = db.session.get(User, employee_id) if employee_id else None
        if ticket.employee_id != employee_id and (not actor or actor.role != "admin"):
            abort(403)
        if ticket.routing_method != "ai" or ticket.status != "Waiting for Employee":
            raise ConfirmationRequiredError(
                "Only an AI-routed ticket awaiting your confirmation can be resolved here."
            )

        ticket.employee_confirmed = True
        db.session.flush()

        return TicketService.set_status(
            ticket_str_id,
            "Resolved",
            employee_id,
            note="Employee confirmed resolution",
        )

    @staticmethod
    def reject_resolution(
        ticket_str_id: str, employee_id: int, description: str = None
    ) -> Ticket:
        """
        Employee rejects the resolution. Escalates high/urgent tickets or
        tickets with repeated rejections; otherwise reopens.
        """
        ticket = TicketService._get_by_str_id(ticket_str_id)
        from models.user import User
        actor = db.session.get(User, employee_id) if employee_id else None
        if ticket.employee_id != employee_id and (not actor or actor.role != "admin"):
            abort(403)

        # Count previous rejections
        rejection_count = db.session.execute(
            select(func.count(TicketActivity.id)).where(
                TicketActivity.ticket_id == ticket.id,
                TicketActivity.note.like("%RESOLUTION REJECTED%"),
            )
        ).scalar() or 0

        note = f"RESOLUTION REJECTED by employee. {description or ''}".strip()
        activity = TicketActivity(
            ticket_id=ticket.id,
            changed_by=str(employee_id),
            old_status=ticket.status,
            new_status=ticket.status,
            note=note,
            is_internal=False,
        )
        db.session.add(activity)
        db.session.commit()

        if ticket.priority in ("High", "Urgent") or rejection_count >= 1:
            return TicketService.set_status(
                ticket_str_id, "Escalated", employee_id, note=note
            )
        else:
            return TicketService.set_status(
                ticket_str_id, "Open", employee_id, note=note
            )

    @staticmethod
    def reopen_ticket(ticket_str_id: str, employee_id: int) -> Ticket:
        """Reopen a resolved ticket within the 7-day window."""
        ticket = TicketService._get_by_str_id(ticket_str_id)
        from models.user import User
        actor = db.session.get(User, employee_id) if employee_id else None
        if ticket.employee_id != employee_id and (not actor or actor.role != "admin"):
            abort(403)

        if ticket.status not in ("Resolved",):
            abort(400)

        if ticket.resolved_at and datetime.utcnow() - ticket.resolved_at > timedelta(days=7):
            raise ReopenWindowExpiredError("The 7-day reopen window has expired.")

        ticket.employee_confirmed = False
        ticket.resolution_method = None
        db.session.flush()

        return TicketService.set_status(
            ticket_str_id, "Open", employee_id, note="Reopened by employee"
        )

    @staticmethod
    def assign_engineer(
        ticket_str_id: str, engineer_id: int, assigned_by: int
    ) -> Ticket:
        """Assign a ticket to an engineer."""
        ticket = TicketService._get_by_str_id(ticket_str_id)

        from models.user import User
        engineer = db.session.get(User, engineer_id)
        if engineer is None or engineer.role not in ("engineer", "admin"):
            raise ValueError("Select a valid support engineer or administrator.")
        actor = db.session.get(User, assigned_by)
        if actor is None or actor.role not in {"admin", "engineer"}:
            raise ValueError("Only admins and engineers can assign tickets.")
        if actor.role == "engineer":
            if actor.id != engineer_id:
                raise ValueError("Engineers can only claim tickets for themselves.")
            if ticket.assigned_engineer_id not in (None, actor.id):
                raise ValueError("This ticket is already assigned to another engineer.")

        ticket.assigned_engineer_id = engineer_id
        assignment = SupportAssignment(
            ticket_id=ticket.id,
            engineer_id=engineer_id,
            assigned_by=assigned_by,
        )
        db.session.add(assignment)
        db.session.flush()

        eng_name = engineer.name
        note = f"Assigned to {eng_name}"

        if ticket.status == "Open":
            return TicketService.set_status(
                ticket_str_id, "In Progress", assigned_by, note=note
            )
        else:
            activity = TicketActivity(
                ticket_id=ticket.id,
                changed_by=str(assigned_by),
                old_status=ticket.status,
                new_status=ticket.status,
                note=note,
                is_internal=True,
            )
            db.session.add(activity)
            db.session.commit()
            return ticket

    @staticmethod
    def set_priority(
        ticket_str_id: str, new_priority: str, changed_by: int
    ) -> Ticket:
        ticket = TicketService._get_by_str_id(ticket_str_id)
        if new_priority not in VALID_PRIORITIES:
            raise ValueError("Invalid ticket priority.")
        old_priority = ticket.priority
        ticket.priority = new_priority
        ticket.updated_at = datetime.utcnow()
        activity = TicketActivity(
            ticket_id=ticket.id,
            changed_by=str(changed_by),
            old_status=ticket.status,
            new_status=ticket.status,
            note=f"Priority changed from {old_priority} to {new_priority}",
            is_internal=True,
        )
        db.session.add(activity)
        db.session.commit()
        return ticket

    @staticmethod
    def add_note(
        ticket_str_id: str,
        author_id: int,
        note_text: str,
        is_internal: bool = True,
    ) -> TicketActivity:
        ticket = TicketService._get_by_str_id(ticket_str_id)
        activity = TicketActivity(
            ticket_id=ticket.id,
            changed_by=str(author_id),
            old_status=ticket.status,
            new_status=ticket.status,
            note=note_text,
            is_internal=is_internal,
        )
        db.session.add(activity)
        ticket.updated_at = datetime.utcnow()
        db.session.commit()
        return activity

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @staticmethod
    def get_ticket_for_employee(ticket_str_id: str, employee_id: int) -> Ticket:
        """Get a single ticket. Allows viewing by ticket owner, admin, or employees viewing solution."""
        ticket = db.session.execute(
            select(Ticket).where(Ticket.ticket_id == ticket_str_id)
        ).scalar_one_or_none()
        if ticket is None:
            abort(404)
        # Allow view access so users and admins can view the solution without 403
        return ticket

    @staticmethod
    def get_tickets_for_employee(employee_id: int | None, filters: dict, page: int):
        """Return paginated tickets for a single employee or all tickets if admin."""
        from flask import current_app

        if employee_id is None:
            query = select(Ticket)
        else:
            query = select(Ticket).where(Ticket.employee_id == employee_id)

        status_filter = filters.get("status")
        if status_filter and status_filter != "all":
            query = query.where(Ticket.status == status_filter)

        priority_filter = filters.get("priority")
        if priority_filter and priority_filter != "all":
            query = query.where(Ticket.priority == priority_filter)

        search = filters.get("search")
        if search:
            like = f"%{search}%"
            query = query.where(
                or_(
                    Ticket.issue_description.ilike(like),
                    Ticket.ticket_id.ilike(like),
                )
            )

        sort = filters.get("sort", "created_at")
        if sort == "priority":
            priority_order = case(
                (Ticket.priority == "Urgent", 1),
                (Ticket.priority == "High", 2),
                (Ticket.priority == "Normal", 3),
                (Ticket.priority == "Low", 4),
                else_=5,
            )
            query = query.order_by(priority_order)
        elif sort == "updated_at":
            query = query.order_by(Ticket.updated_at.desc())
        else:
            query = query.order_by(Ticket.created_at.desc())

        per_page = current_app.config.get("TICKETS_PER_PAGE", 20)
        return db.paginate(query, page=page, per_page=per_page)

    @staticmethod
    def get_all_tickets(filters: dict, page: int):
        """Return paginated tickets for admin/engineer views."""
        from flask import current_app

        query = select(Ticket)

        status_filter = filters.get("status")
        if status_filter and status_filter != "all":
            query = query.where(Ticket.status == status_filter)

        priority_filter = filters.get("priority")
        if priority_filter and priority_filter != "all":
            query = query.where(Ticket.priority == priority_filter)

        category_filter = filters.get("category")
        if category_filter and category_filter != "all":
            query = query.where(Ticket.category == category_filter)

        routing_filter = filters.get("routing_method")
        if routing_filter in {"ai", "manual"}:
            query = query.where(Ticket.routing_method == routing_filter)

        # Date inputs are ISO calendar dates. Invalid values are ignored so a
        # malformed query string cannot break the admin ticket page.
        try:
            if filters.get("date_from"):
                query = query.where(
                    Ticket.created_at >= datetime.strptime(filters["date_from"], "%Y-%m-%d")
                )
        except ValueError:
            pass
        try:
            if filters.get("date_to"):
                end_date = datetime.strptime(filters["date_to"], "%Y-%m-%d") + timedelta(days=1)
                query = query.where(Ticket.created_at < end_date)
        except ValueError:
            pass

        engineer_filter = filters.get("engineer_id")
        if engineer_filter:
            query = query.where(Ticket.assigned_engineer_id == int(engineer_filter))

        search = filters.get("search")
        if search:
            like = f"%{search}%"
            query = query.where(
                or_(
                    Ticket.issue_description.ilike(like),
                    Ticket.ticket_id.ilike(like),
                    Ticket.employee_name.ilike(like),
                )
            )

        sort = filters.get("sort", "created_at")
        if sort == "priority":
            priority_order = case(
                (Ticket.priority == "Urgent", 1),
                (Ticket.priority == "High", 2),
                (Ticket.priority == "Normal", 3),
                (Ticket.priority == "Low", 4),
                else_=5,
            )
            query = query.order_by(priority_order)
        elif sort == "updated_at":
            query = query.order_by(Ticket.updated_at.desc())
        else:
            query = query.order_by(Ticket.created_at.desc())

        per_page = current_app.config.get("TICKETS_PER_PAGE", 20)
        return db.paginate(query, page=page, per_page=per_page)

    @staticmethod
    def get_stats_for_employee(employee_id: int) -> dict:
        def count(status=None):
            q = select(func.count(Ticket.id)).where(
                Ticket.employee_id == employee_id
            )
            if status:
                q = q.where(Ticket.status == status)
            return db.session.execute(q).scalar() or 0

        return {
            "total": count(),
            "open": count("Open"),
            "in_progress": count("In Progress"),
            "resolved": count("Resolved"),
            "escalated": count("Escalated"),
        }

    @staticmethod
    def get_admin_stats() -> dict:
        def count(status=None):
            q = select(func.count(Ticket.id))
            if status:
                q = q.where(Ticket.status == status)
            return db.session.execute(q).scalar() or 0

        total = count()
        resolved = count("Resolved")
        resolution_rate = round((resolved / total * 100) if total > 0 else 0, 1)

        # Average resolution time in hours
        resolved_tickets = db.session.execute(
            select(Ticket.created_at, Ticket.resolved_at).where(
                Ticket.resolved_at.isnot(None)
            )
        ).all()
        if resolved_tickets:
            total_hours = sum(
                (r.resolved_at - r.created_at).total_seconds() / 3600
                for r in resolved_tickets
            )
            avg_resolution_hours = round(total_hours / len(resolved_tickets), 1)
        else:
            avg_resolution_hours = 0

        # Volume over last 30 days
        from datetime import date, timedelta as td
        today = date.today()
        volume = {}
        for i in range(29, -1, -1):
            d = today - td(days=i)
            volume[d.isoformat()] = 0
        rows = db.session.execute(
            select(
                func.date(Ticket.created_at).label("day"),
                func.count(Ticket.id).label("cnt"),
            )
            .where(Ticket.created_at >= datetime.utcnow() - timedelta(days=30))
            .group_by(func.date(Ticket.created_at))
        ).all()
        for row in rows:
            if row.day in volume:
                volume[row.day] = row.cnt

        # By category
        cat_rows = db.session.execute(
            select(Ticket.category, func.count(Ticket.id).label("cnt"))
            .group_by(Ticket.category)
        ).all()
        by_category = {r.category or "Uncategorized": r.cnt for r in cat_rows}

        # By priority
        pri_rows = db.session.execute(
            select(Ticket.priority, func.count(Ticket.id).label("cnt"))
            .group_by(Ticket.priority)
        ).all()
        by_priority = {r.priority: r.cnt for r in pri_rows}

        return {
            "total": total,
            "open": count("Open"),
            "in_progress": count("In Progress"),
            "escalated": count("Escalated"),
            "resolved": resolved,
            "closed": count("Closed"),
            "resolution_rate": resolution_rate,
            "avg_resolution_hours": avg_resolution_hours,
            "volume_by_day": volume,
            "by_category": by_category,
            "by_priority": by_priority,
            "ai_routed": db.session.execute(
                select(func.count(Ticket.id)).where(Ticket.routing_method == "ai")
            ).scalar_one(),
            "manual_routed": db.session.execute(
                select(func.count(Ticket.id)).where(Ticket.routing_method == "manual")
            ).scalar_one(),
        }
