from flask import Blueprint, request, jsonify, abort
from flask import current_app
from flask_login import login_required, current_user
from sqlalchemy import select
from datetime import datetime
import re
from werkzeug.exceptions import HTTPException
from models import db
from models.conversation import Conversation, Message
from models.ticket import Ticket
from routes import require_role
from services.ai_service import AIService, CATEGORIES
from services.kb_service import KBService
from services.ticket_service import TicketService, VALID_PRIORITIES
from services.notification_service import NotificationService
from services.exceptions import (
    ConfirmationRequiredError,
    AIServiceUnavailableError,
    TicketCreationError,
)

api_bp = Blueprint("api", __name__)


def _get_csrf_token():
    return request.headers.get("X-CSRFToken") or request.headers.get("X-CSRF-Token")


@api_bp.route("/chat/message", methods=["POST"])
@login_required
def chat_message():
    data = request.get_json(force=True, silent=True) or {}
    user_message = (data.get("message") or "").strip()
    conversation_id = data.get("conversation_id")
    active_ticket_id = (data.get("active_ticket_id") or "").strip().upper()

    if not user_message:
        return jsonify({"error": "Message cannot be empty."}), 400
    if len(user_message) > 2000:
        return jsonify({"error": "Message too long."}), 400

    # Load or create conversation
    if conversation_id:
        try:
            conv = db.session.get(Conversation, int(conversation_id))
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid conversation_id."}), 400
        if conv is None or conv.employee_id != current_user.id:
            abort(403)
    else:
        conv = Conversation(employee_id=current_user.id)
        db.session.add(conv)
        db.session.flush()

    if active_ticket_id:
        linked_ticket = db.session.execute(
            select(Ticket).where(
                Ticket.ticket_id == active_ticket_id,
                Ticket.employee_id == current_user.id,
            )
        ).scalar_one_or_none()
        if linked_ticket is None:
            abort(404)
        conv.active_ticket_id = linked_ticket.ticket_id

    # Persist user message
    user_msg = Message(
        conversation_id=conv.id,
        role="user",
        content=user_message,
    )
    db.session.add(user_msg)
    db.session.flush()

    # Build history from prior turns. The current message is passed separately
    # to AIService below, so including it here would send it twice upstream.
    history_rows = db.session.execute(
        select(Message)
        .where(Message.conversation_id == conv.id, Message.id != user_msg.id)
        .order_by(Message.created_at.asc())
        .limit(20)
    ).scalars().all()
    history = [{"role": m.role, "content": m.content} for m in history_rows]

    previous_assistant = next(
        (item for item in reversed(history) if item.get("role") == "assistant"), None
    )
    prior_prompted_confirmation = bool(
        previous_assistant
        and re.search(
            r"is your issue resolved|confirm below and i.?ll mark this ticket resolved|"
            r"if it (?:does not|doesn't|did not|didn't) (?:fix|work)|"
            r"if the issue (?:continues|persists|is still happening)",
            previous_assistant.get("content", ""),
            re.IGNORECASE,
        )
    )
    affirmative = bool(re.fullmatch(
        r"\s*(?:(?:yes|yeah|yep)(?:[, ]+(?:it's fixed|it is fixed|working now|resolved))?|"
        r"it's fixed|it is fixed|working now|resolved)[.! ]*\s*",
        user_message,
        re.IGNORECASE,
    ))
    negative = bool(re.fullmatch(
        r"\s*(?:(?:no|nope)(?:[, ]+(?:still having issues|not fixed|not yet|still broken))?|"
        r"still having issues|not fixed|not yet|still broken)[.! ]*\s*",
        user_message,
        re.IGNORECASE,
    ))
    if prior_prompted_confirmation and (affirmative or negative):
        tracked_ticket = None
        if conv.active_ticket_id:
            tracked_ticket = db.session.execute(
                select(Ticket).where(
                    Ticket.ticket_id == conv.active_ticket_id,
                    Ticket.employee_id == current_user.id,
                )
            ).scalar_one_or_none()
        if tracked_ticket and tracked_ticket.status != "Resolved":
            if affirmative:
                if (
                    tracked_ticket.routing_method == "ai"
                    and tracked_ticket.status == "Waiting for Employee"
                ):
                    TicketService.confirm_resolution(tracked_ticket.ticket_id, current_user.id)
                    reply = f"Thanks for confirming. Ticket **{tracked_ticket.ticket_id}** is now resolved."
                elif tracked_ticket.routing_method == "ai":
                    reply = (
                        f"Ticket **{tracked_ticket.ticket_id}** is still being handled by AI. "
                        "I can only mark it resolved after troubleshooting and your confirmation."
                    )
                else:
                    reply = (
                        f"Ticket **{tracked_ticket.ticket_id}** is in the manual support queue. "
                        "The assigned support engineer will update its status when the issue is fixed."
                    )
            elif tracked_ticket.routing_method == "ai":
                TicketService.route_ai_ticket_to_manual(
                    tracked_ticket.ticket_id,
                    "Employee confirmed that AI troubleshooting did not resolve the issue.",
                )
                reply = (
                    f"Understood. Ticket **{tracked_ticket.ticket_id}** is now in the "
                    "human IT support queue."
                )
            else:
                TicketService.reject_resolution(
                    tracked_ticket.ticket_id,
                    current_user.id,
                    "Employee reports the issue persists.",
                )
                db.session.refresh(tracked_ticket)
                reply = (
                    f"Understood. Ticket **{tracked_ticket.ticket_id}** remains "
                    f"**{tracked_ticket.status}** so support can continue."
                )
            ai_msg = Message(conversation_id=conv.id, role="assistant", content=reply)
            db.session.add(ai_msg)
            conv.updated_at = datetime.utcnow()
            db.session.commit()
            return jsonify({"reply": reply, "conversation_id": conv.id,
                            "suggested_ticket": False, "category": tracked_ticket.category or "general",
                            "priority": tracked_ticket.priority, "ask_confirmation": False,
                            "ticket_id": tracked_ticket.ticket_id})
        if tracked_ticket and tracked_ticket.status == "Resolved":
            if negative and tracked_ticket.routing_method == "ai":
                TicketService.route_ai_ticket_to_manual(
                    tracked_ticket.ticket_id,
                    "Employee reported that the AI troubleshooting did not resolve the issue.",
                )
                reply = (
                    f"Sorry that did not fix it. Ticket **{tracked_ticket.ticket_id}** "
                    "has been reopened and sent to the human IT support queue."
                )
            elif affirmative:
                reply = f"Thanks. Ticket **{tracked_ticket.ticket_id}** remains resolved."
            else:
                reply = (
                    f"Ticket **{tracked_ticket.ticket_id}** is already resolved. "
                    "If the issue has returned, contact human IT support to reopen it."
                )
            ai_msg = Message(conversation_id=conv.id, role="assistant", content=reply)
            db.session.add(ai_msg)
            conv.updated_at = datetime.utcnow()
            db.session.commit()
            return jsonify({
                "reply": reply,
                "conversation_id": conv.id,
                "suggested_ticket": False,
                "category": tracked_ticket.category or "general",
                "priority": tracked_ticket.priority,
                "ask_confirmation": False,
                "ticket_id": tracked_ticket.ticket_id,
            })
        if tracked_ticket is None:
            category = data.get("category")
            priority = data.get("priority")
            if category not in CATEGORIES:
                category = "general"
            if priority not in VALID_PRIORITIES:
                priority = "Normal"
            reply = (
                "I'm glad the troubleshooting steps worked. No support ticket was created."
                if affirmative
                else "Thanks for letting me know. I can create a support ticket so the IT team can help."
            )
            ai_msg = Message(conversation_id=conv.id, role="assistant", content=reply)
            db.session.add(ai_msg)
            conv.updated_at = datetime.utcnow()
            db.session.commit()
            return jsonify({
                "reply": reply,
                "conversation_id": conv.id,
                "suggested_ticket": not affirmative,
                "category": category,
                "priority": priority,
                "ask_confirmation": False,
            })

    # Ticket tracking is backed by the database, so the assistant reports the
    # real status instead of guessing. A tracked ticket becomes the target for
    # the explicit employee resolution confirmation buttons in the chat UI.
    ticket_match = re.search(r"\bIT-\d{5}\b", user_message, re.IGNORECASE)
    tracking_request = bool(re.search(
        r"\b(track|status|progress|where is|update on|show me|clear|resolve|close|fixed|solved)\b", user_message, re.IGNORECASE
    ))
    clearing_request = bool(re.search(
        r"\b(clear|resolve|close|fixed|solved|issue is done)\b", user_message, re.IGNORECASE
    ))
    ticket = None
    if ticket_match:
        ticket_id = ticket_match.group(0).upper()
        ticket = db.session.execute(
            select(Ticket).where(
                Ticket.ticket_id == ticket_id,
                Ticket.employee_id == current_user.id,
            )
        ).scalar_one_or_none()
        if ticket is None:
            # Do not reveal whether an ID belongs to another employee.
            reply = f"I couldn't find **{ticket_id}** in your tickets. Check the ID or open My Tickets to see your requests."
            tracking_request = True
    elif tracking_request:
        # For follow-up phrases such as “clear it”, use the ticket the
        # employee explicitly selected earlier in this conversation.
        if clearing_request and conv.active_ticket_id:
            ticket = db.session.execute(
                select(Ticket).where(
                    Ticket.ticket_id == conv.active_ticket_id,
                    Ticket.employee_id == current_user.id,
                )
            ).scalar_one_or_none()

    if ticket is not None:
        conv.active_ticket_id = ticket.ticket_id
        assigned_to = ticket.assigned_engineer.name if ticket.assigned_engineer else "Not assigned yet"
        if ticket.status == "Resolved":
            reply = f"Ticket **{ticket.ticket_id}** is already resolved. It was last updated {ticket.updated_at.strftime('%b %d, %Y %H:%M') if ticket.updated_at else 'recently'}."
            ask_confirmation = False
        elif clearing_request:
            if ticket.routing_method == "ai" and ticket.status == "Waiting for Employee":
                reply = (
                    f"Is your issue resolved? Confirm below and I'll mark ticket "
                    f"**{ticket.ticket_id}** resolved. I will keep it open until you confirm."
                )
                ask_confirmation = True
            elif ticket.routing_method == "manual":
                reply = (
                    f"Ticket **{ticket.ticket_id}** is in the manual support queue. "
                    "An admin or assigned engineer will resolve it and update its status."
                )
                ask_confirmation = False
            else:
                reply = (
                    f"Ticket **{ticket.ticket_id}** is still being handled by AI. "
                    "I will ask you to confirm after troubleshooting is complete."
                )
                ask_confirmation = False
        else:
            reply = (
                f"Ticket **{ticket.ticket_id}** is **{ticket.status}** with **{ticket.priority}** priority. "
                f"Assigned to: **{assigned_to}**. Last updated: "
                f"{ticket.updated_at.strftime('%b %d, %Y %H:%M') if ticket.updated_at else 'not available'}."
            )
            ask_confirmation = False

        ai_msg = Message(conversation_id=conv.id, role="assistant", content=reply)
        db.session.add(ai_msg)
        conv.updated_at = datetime.utcnow()
        db.session.commit()
        return jsonify({
            "reply": reply,
            "conversation_id": conv.id,
            "suggested_ticket": False,
            "category": ticket.category or "general",
            "priority": ticket.priority,
            "ask_confirmation": ask_confirmation,
            "ticket_id": ticket.ticket_id,
        })
    elif ticket_match:
        reply = f"I couldn't find **{ticket_match.group(0).upper()}** in your tickets. Check the ID or open My Tickets to see your requests."
        ai_msg = Message(conversation_id=conv.id, role="assistant", content=reply)
        db.session.add(ai_msg)
        conv.updated_at = datetime.utcnow()
        db.session.commit()
        return jsonify({"reply": reply, "conversation_id": conv.id, "suggested_ticket": False,
                        "category": "general", "priority": "Normal", "ask_confirmation": False})
    elif tracking_request and not ticket_match:
        tickets = db.session.execute(
            select(Ticket)
            .where(Ticket.employee_id == current_user.id)
            .order_by(Ticket.updated_at.desc())
            .limit(5)
        ).scalars().all()
        if tickets:
            reply = "Here are your latest tickets:\n\n" + "\n".join(
                f"- **{item.ticket_id}** — {item.status} ({item.priority})"
                for item in tickets
            ) + "\n\nSend a ticket ID if you want details or to confirm it’s resolved."
        else:
            reply = "You don't have any support tickets yet. Tell me what’s going wrong and I can help troubleshoot or create one."
        ai_msg = Message(conversation_id=conv.id, role="assistant", content=reply)
        db.session.add(ai_msg)
        conv.updated_at = datetime.utcnow()
        db.session.commit()
        return jsonify({
            "reply": reply,
            "conversation_id": conv.id,
            "suggested_ticket": False,
            "category": "general",
            "priority": "Normal",
            "ask_confirmation": False,
        })

    from services.ticket_routing_service import TicketRoutingService

    if (
        TicketRoutingService.is_critical_issue(user_message)
        and not conv.active_ticket_id
        and len(user_message) >= 10
    ):
        ticket = TicketService.create_ticket(
            employee_id=current_user.id,
            issue_description=user_message[:2000],
            category="general",
            priority="Urgent",
            conversation_id=conv.id,
        )
        conv.active_ticket_id = ticket.ticket_id
        reply = (
            f"This appears to be a critical security or production incident. "
            f"I escalated it immediately to human IT support as **{ticket.ticket_id}**."
        )
        db.session.add(Message(conversation_id=conv.id, role="assistant", content=reply))
        conv.updated_at = datetime.utcnow()
        db.session.commit()
        return jsonify({
            "reply": reply,
            "conversation_id": conv.id,
            "suggested_ticket": False,
            "category": ticket.category,
            "priority": ticket.priority,
            "ask_confirmation": False,
            "ticket_id": ticket.ticket_id,
        })

    ai_ticket = None
    if conv.active_ticket_id:
        ai_ticket = db.session.execute(
            select(Ticket).where(
                Ticket.ticket_id == conv.active_ticket_id,
                Ticket.employee_id == current_user.id,
                Ticket.routing_method == "ai",
                Ticket.status != "Resolved",
            )
        ).scalar_one_or_none()

    if (
        ai_ticket is None
        and not conv.active_ticket_id
        and len(user_message) >= 10
        and not history
    ):
        issue_description = "\n".join(
            item["content"] for item in history + [{"role": "user", "content": user_message}]
            if item.get("role") == "user"
        )[-2000:]
        ai_ticket = TicketService.create_ticket(
            employee_id=current_user.id,
            issue_description=issue_description,
            conversation_id=conv.id,
        )
        conv.active_ticket_id = ai_ticket.ticket_id
        db.session.commit()
        if ai_ticket.routing_method != "ai":
            reply = (
                f"I sent your issue to the manual support queue as "
                f"**{ai_ticket.ticket_id}**. An administrator may choose to run AI "
                "troubleshooting on the open-ticket queue."
            )
            db.session.add(Message(conversation_id=conv.id, role="assistant", content=reply))
            db.session.commit()
            return jsonify({
                "reply": reply,
                "conversation_id": conv.id,
                "suggested_ticket": False,
                "category": ai_ticket.category,
                "priority": ai_ticket.priority,
                "ask_confirmation": False,
                "ticket_id": ai_ticket.ticket_id,
            })

    # Get KB context
    kb_context = KBService.get_context_for_ai(user_message)

    try:
        ai_response = AIService.get_response(history, user_message, kb_context)
    except AIServiceUnavailableError as exc:
        current_app.logger.warning(
            "AI support unavailable (%s); routing request to human support.",
            type(exc).__name__,
        )
        issue_description = "\n".join(
            item["content"] for item in history + [{"role": "user", "content": user_message}]
            if item.get("role") == "user"
        )[-2000:]
        if len(issue_description.strip()) >= 10:
            ticket = ai_ticket
            fallback_reason = "AI troubleshooting was unavailable; routed to human IT support."
            if ticket:
                ticket = TicketService.route_ai_ticket_to_manual(
                    ticket.ticket_id, fallback_reason
                )
            elif conv.active_ticket_id:
                ticket = db.session.execute(
                    select(Ticket).where(
                        Ticket.ticket_id == conv.active_ticket_id,
                        Ticket.employee_id == current_user.id,
                    )
                ).scalar_one_or_none()
                if ticket and ticket.routing_method == "ai":
                    ticket = TicketService.route_ai_ticket_to_manual(
                        ticket.ticket_id, fallback_reason
                    )
            if ticket is None:
                ticket = TicketService.create_ticket(
                    employee_id=current_user.id,
                    issue_description=issue_description.strip(),
                    priority=TicketRoutingService.suggested_priority(issue_description),
                    conversation_id=conv.id,
                    fallback_reason=fallback_reason,
                )
            conv.active_ticket_id = ticket.ticket_id
            reply = (
                "AI troubleshooting is temporarily unavailable, so I preserved this "
                f"conversation and sent your request to human IT support as **{ticket.ticket_id}**."
            )
            db.session.add(Message(conversation_id=conv.id, role="assistant", content=reply))
            conv.updated_at = datetime.utcnow()
            db.session.commit()
            return jsonify({
                "reply": reply,
                "conversation_id": conv.id,
                "suggested_ticket": False,
                "category": "general",
                "priority": ticket.priority,
                "ask_confirmation": False,
                "ticket_id": ticket.ticket_id,
                "fallback": True,
            })
        db.session.add(Message(
            conversation_id=conv.id,
            role="assistant",
            content="AI troubleshooting is temporarily unavailable. Please describe the issue in more detail so I can route it to human support.",
        ))
        conv.updated_at = datetime.utcnow()
        db.session.commit()
        return jsonify({
            "error": "AI support is temporarily unavailable. Your message has been saved.",
            "fallback": True,
            "conversation_id": conv.id,
        }), 503

    if ai_response.get("category") not in CATEGORIES:
        ai_response["category"] = "general"
    if ai_response.get("priority") not in VALID_PRIORITIES:
        ai_response["priority"] = "Normal"

    # AI-mode tickets must be explicitly confirmed by the employee. Otherwise,
    # preserve the ticket and wait for that confirmation or route it to a person.
    if ai_ticket and (
        ai_response.get("suggested_ticket")
        or not (ai_response.get("content") or "").strip()
    ):
        TicketService.route_ai_ticket_to_manual(
            ai_ticket.ticket_id,
            "AI could not provide a useful resolution; routed to human IT support.",
            category=ai_response.get("category"),
            priority=ai_response.get("priority"),
            recommendation=ai_response.get("content"),
        )
        ai_response["content"] += (
            f"\n\nI couldn't resolve this automatically, so ticket "
            f"**{ai_ticket.ticket_id}** has been sent to human IT support."
        )
        ai_response["ask_confirmation"] = False
        ai_response["suggested_ticket"] = False
    elif ai_ticket:
        ai_ticket = TicketService.record_ai_result(
            ai_ticket.ticket_id,
            ai_response.get("category", "general"),
            ai_response.get("priority", "Normal"),
            ai_response.get("content", ""),
        )
        if "is your issue resolved?" not in ai_response["content"].lower():
            ai_response["content"] += "\n\nIs your issue resolved?"
        ai_response["content"] += (
            f"\n\nTicket **{ai_ticket.ticket_id}** is waiting for your confirmation. "
            "Reply **Yes, it's fixed** to resolve it, or **No, still having issues** "
            "to send it to human IT support."
        )
        ai_response["ask_confirmation"] = True

    ai_msg = Message(
        conversation_id=conv.id,
        role="assistant",
        content=ai_response["content"],
    )
    db.session.add(ai_msg)
    conv.updated_at = datetime.utcnow()
    db.session.commit()

    return jsonify({
        "reply": ai_response["content"],
        "conversation_id": conv.id,
        "suggested_ticket": ai_response.get("suggested_ticket", False),
        "category": ai_response.get("category", "general"),
        "priority": ai_response.get("priority", "Normal"),
        "ask_confirmation": ai_response.get("ask_confirmation", False),
        "ticket_id": ai_ticket.ticket_id if ai_ticket else None,
    })


@api_bp.route("/chat/create-ticket", methods=["POST"])
@login_required
def create_ticket():
    data = request.get_json(force=True, silent=True) or {}
    conversation_id = data.get("conversation_id")
    issue_description = (data.get("issue_description") or "").strip()
    category = data.get("category", "general")
    priority = data.get("priority", "Normal")
    support_mode = data.get("support_mode", "ai")
    if support_mode not in {"ai", "manual"}:
        return jsonify({"error": "Invalid support mode."}), 400
    if not issue_description or len(issue_description) < 10:
        return jsonify({
            "error": "validation_error",
            "field": "issue_description",
            "message": "Must be 10–2000 characters",
        }), 422
    if len(issue_description) > 2000:
        return jsonify({
            "error": "validation_error",
            "field": "issue_description",
            "message": "Must be 10–2000 characters",
        }), 422

    if category not in CATEGORIES:
        category = "general"

    if priority not in VALID_PRIORITIES:
        priority = "Normal"

    # Get AI recommendation from conversation if available
    ai_recommendation = None
    conv = None
    if conversation_id and support_mode == "ai":
        try:
            conv = db.session.get(Conversation, int(conversation_id))
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid conversation_id."}), 400
        if conv is None or conv.employee_id != current_user.id:
            abort(403)
        if conv.active_ticket_id:
            existing_ticket = db.session.execute(
                select(Ticket).where(
                    Ticket.ticket_id == conv.active_ticket_id,
                    Ticket.employee_id == current_user.id,
                )
            ).scalar_one_or_none()
            if existing_ticket:
                return jsonify({
                    "ticket_id": existing_ticket.ticket_id,
                    "id": existing_ticket.id,
                })
        last_ai = db.session.execute(
            select(Message)
            .where(
                Message.conversation_id == conv.id,
                Message.role == "assistant",
            )
            .order_by(Message.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if last_ai:
            ai_recommendation = last_ai.content[:500]

    try:
        from services.ticket_routing_service import TicketRoutingService
        ticket = TicketService.create_ticket(
            employee_id=current_user.id,
            issue_description=issue_description,
            category=category,
            priority=TicketRoutingService.suggested_priority(issue_description)
            if TicketRoutingService.is_critical_issue(issue_description)
            else priority,
            ai_recommendation=ai_recommendation,
            conversation_id=conv.id if conv else None,
        )
        if conv:
            conv.active_ticket_id = ticket.ticket_id
            conv.updated_at = datetime.utcnow()
            db.session.commit()
        if ticket.routing_method == "ai":
            from services.ai_clearing_service import AiClearingService

            try:
                AiClearingService.start_ticket_job(current_user.id, ticket.id)
            except RuntimeError:
                current_app.logger.exception(
                    "Automatic AI processing could not start for ticket %s.",
                    ticket.ticket_id,
                )
                ticket = TicketService.route_ai_ticket_to_manual(
                    ticket.ticket_id,
                    "Automatic AI processing could not be started; routed to human IT support.",
                )
        from services.ticket_routing_service import TicketRoutingService

        return jsonify({
            "ticket_id": ticket.ticket_id,
            "id": ticket.id,
            "routing_method": ticket.routing_method,
            "current_mode": TicketRoutingService.current_mode()["mode"],
        })
    except TicketCreationError as exc:
        return jsonify({"error": str(exc)}), 500
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 422


@api_bp.route("/chat/confirm-resolution", methods=["POST"])
@login_required
def confirm_resolution():
    """
    Body: {"ticket_id": "IT-00001", "confirmed": bool}
    ticket_id is the IT-XXXXX string; the service resolves the integer PK internally.
    """
    data = request.get_json(force=True, silent=True) or {}
    ticket_str_id = (data.get("ticket_id") or "").strip()
    confirmed = data.get("confirmed", False)

    if not ticket_str_id:
        return jsonify({"error": "ticket_id is required"}), 400

    try:
        if confirmed:
            ticket = TicketService.confirm_resolution(ticket_str_id, current_user.id)
        else:
            description = data.get("description", "")
            ticket = TicketService.reject_resolution(ticket_str_id, current_user.id, description)
        return jsonify({"ticket_id": ticket.ticket_id, "status": ticket.status})
    except ConfirmationRequiredError as exc:
        return jsonify({"error": str(exc)}), 409
    except HTTPException:
        raise
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@api_bp.route("/notifications")
@login_required
def notifications():
    unread_count = NotificationService.get_unread_count(current_user.id)
    notifs = NotificationService.get_notifications(current_user.id, limit=10)
    return jsonify({
        "unread_count": unread_count,
        "notifications": [
            {
                "id": n.id,
                "type": n.type,
                "message": n.message,
                "is_read": n.is_read,
                "created_at": n.created_at.isoformat() if n.created_at else None,
                "related_ticket_id": n.related_ticket_id,
            }
            for n in notifs
        ],
    })


@api_bp.route("/notifications/<int:notification_id>/read", methods=["POST"])
@login_required
def mark_notification_read(notification_id):
    NotificationService.mark_read(notification_id, current_user.id)
    return jsonify({"ok": True})


@api_bp.route("/notifications/read-all", methods=["POST"])
@login_required
def mark_all_notifications_read():
    NotificationService.mark_all_read(current_user.id)
    return jsonify({"ok": True})


@api_bp.route("/admin/stats")
@require_role("admin")
def admin_stats():
    stats = TicketService.get_admin_stats()
    return jsonify(stats)


@api_bp.route("/tickets/<ticket_id>/status", methods=["POST"])
@login_required
def update_ticket_status(ticket_id):
    """
    Used by tests: POST /api/tickets/<ticket_id>/status {"status": "Resolved"}
    Returns 409 if employee_confirmed is False and status is Resolved.
    """
    data = request.get_json(force=True, silent=True) or {}
    new_status = data.get("status", "")
    note = data.get("note", "")

    # Status changes are reserved for admins and the engineer assigned to the
    # ticket. Employees use the explicit confirmation/reopen flows instead.
    from models.ticket import Ticket
    ticket = db.session.execute(
        select(Ticket).where(Ticket.ticket_id == ticket_id)
    ).scalar_one_or_none()
    if ticket is None:
        abort(404)
    if current_user.role == "employee":
        if ticket.employee_id != current_user.id:
            abort(403)
        if new_status == "Resolved" and not ticket.employee_confirmed:
            return jsonify({"error": "Employee confirmation is required."}), 409
        abort(403)
    elif current_user.role == "engineer":
        if ticket.assigned_engineer_id != current_user.id:
            abort(403)
    elif current_user.role != "admin":
        abort(403)

    try:
        ticket = TicketService.set_status(ticket_id, new_status, current_user.id, note=note)
        return jsonify({"ticket_id": ticket.ticket_id, "status": ticket.status})
    except ConfirmationRequiredError as exc:
        return jsonify({"error": str(exc)}), 409
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 422
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
