from flask import (
    Blueprint, render_template, redirect, url_for,
    flash, request, abort, jsonify, current_app
)
from sqlalchemy import select, func
from models import db
from models.user import User
from models.ticket import Ticket, TicketActivity
from models.conversation import Message
from models.knowledge_base import KBArticle
from routes import require_role
from werkzeug.exceptions import HTTPException
from services.ticket_service import TicketService
from services.settings_service import SettingsService
from services.exceptions import ConfirmationRequiredError
import re

admin_bp = Blueprint("admin", __name__)

VALID_STATUSES = ["Open", "In Progress", "Waiting for Employee", "Escalated", "Resolved", "Closed"]
VALID_PRIORITIES = ["Low", "Normal", "High", "Urgent"]


@admin_bp.route("/")
def admin_root():
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/login")
def admin_login_redirect():
    return redirect(url_for("auth.admin_login"))


@admin_bp.route("/dashboard")
@require_role("admin")
def dashboard():
    from services.ticket_service import TicketService
    from services.ticket_routing_service import TicketRoutingService
    from services.ai_clearing_service import AiClearingService

    stats = TicketService.get_admin_stats()
    routing = TicketRoutingService.admin_overview()
    job = AiClearingService.latest_job()
    job_progress = AiClearingService.job_status(job.id) if job else None

    # Get received tickets for the admin solve queue
    page = request.args.get("page", 1, type=int)
    filters = {
        "status": request.args.get("status", "all"),
        "priority": request.args.get("priority", "all"),
        "category": request.args.get("category", "all"),
        "routing_method": request.args.get("routing_method", "all"),
        "search": request.args.get("search", ""),
        "sort": request.args.get("sort", "created_at"),
        "engineer_id": request.args.get("engineer_id", "", type=int) or "",
        "date_from": request.args.get("date_from", ""),
        "date_to": request.args.get("date_to", ""),
    }
    pagination = TicketService.get_all_tickets(filters, page)
    engineers = db.session.execute(
        select(User).where(User.role.in_(["engineer", "admin"])).order_by(User.name)
    ).scalars().all()

    return render_template(
        "admin/dashboard.html",
        stats=stats,
        routing=routing,
        job_progress=job_progress,
        pagination=pagination,
        filters=filters,
        engineers=engineers,
        active_routing_mode=(
            "AI Automatic Mode"
            if routing["current_mode"] == "ai"
            else "Manual Support Mode"
        ),
    )


@admin_bp.route("/tickets/<ticket_id>/solve", methods=["POST"])
@require_role("admin")
def solve_ticket(ticket_id):
    from flask_login import current_user
    resolution_notes = request.form.get("resolution_notes", "").strip()
    ticket = db.session.execute(
        select(Ticket).where(Ticket.ticket_id == ticket_id)
    ).scalar_one_or_none()
    if not ticket:
        abort(404)

    if not resolution_notes:
        flash("Please provide a resolution explanation before marking the ticket as resolved.", "danger")
        return redirect(url_for("admin.ticket_detail", ticket_id=ticket_id))

    try:
        # If it was in AI mode, convert routing_method to manual so admin can resolve it directly
        if ticket.routing_method == "ai":
            ticket.routing_method = "manual"
            ticket.routing_reason = f"Admin resolved ticket directly."
            db.session.commit()

        TicketService.set_status(
            ticket_id, "Resolved", current_user.id,
            note=resolution_notes,
            resolution_notes=resolution_notes,
        )
        flash(f"Ticket #{ticket_id} has been resolved. Your explanation has been sent to the user.", "success")
    except Exception as exc:
        flash(f"Could not resolve ticket #{ticket_id}: {exc}", "danger")

    return redirect(request.referrer or url_for("admin.dashboard"))


@admin_bp.route("/tickets/<ticket_id>/clear-ai", methods=["POST"])
@require_role("admin")
def clear_ticket_with_ai(ticket_id):
    from services.ai_clearing_service import AiClearingService
    try:
        ticket = TicketService._get_by_str_id(ticket_id)
        success, msg = AiClearingService.clear_ticket_with_ai(ticket.id, auto_resolve=True)
        if success:
            flash(f"Ticket #{ticket.ticket_id} has been analyzed and cleared by ResolveAI! Problem and solution are updated.", "success")
        else:
            flash(f"Could not clear ticket #{ticket.ticket_id} with AI: {msg}", "warning")
    except Exception as exc:
        flash(f"AI clearing error for ticket #{ticket_id}: {exc}", "danger")

    return redirect(request.referrer or url_for("admin.dashboard"))


@admin_bp.route("/ai-clearing/start", methods=["POST"])
@require_role("admin")
def start_ai_clearing():
    from flask_login import current_user
    from services.ai_clearing_service import AiClearingService

    try:
        job, created = AiClearingService.start_job(current_user.id)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    return jsonify({
        "created": created,
        "job": AiClearingService.job_status(job.id),
    }), 202 if created else 200


@admin_bp.route("/ai-clearing/<int:job_id>")
@require_role("admin")
def ai_clearing_status(job_id):
    from services.ai_clearing_service import AiClearingService

    status = AiClearingService.job_status(job_id)
    if status is None:
        abort(404)
    return jsonify(status)


@admin_bp.route("/ai-clearing/latest")
@require_role("admin")
def latest_ai_clearing_status():
    from services.ai_clearing_service import AiClearingService

    job = AiClearingService.latest_job()
    return jsonify({
        "job": AiClearingService.job_status(job.id) if job else None,
    })


@admin_bp.route("/clearance-database")
@require_role("admin", "employee")
def clearance_database():
    from services.ticket_audit_db_service import TicketAuditDBService
    TicketAuditDBService.sync_from_main_db()
    status_filter = request.args.get("status", "all")
    method_filter = request.args.get("method", "all")
    records = TicketAuditDBService.get_all_records(status_filter=status_filter, method_filter=method_filter)
    stats = TicketAuditDBService.get_audit_stats()
    return render_template(
        "admin/clearance_database.html",
        records=records,
        stats=stats,
        status_filter=status_filter,
        method_filter=method_filter,
    )


@admin_bp.route("/manual-clearing")
@require_role("admin")
def manual_clearing():
    from services.ticket_routing_service import TicketRoutingService

    queue_statuses = ("Open", "In Progress", "Waiting for Employee", "Escalated")
    page = request.args.get("page", 1, type=int)
    pagination = db.paginate(
        select(Ticket)
        .where(
            Ticket.routing_method == "manual",
            Ticket.status.in_(queue_statuses),
        )
        .order_by(Ticket.created_at.asc(), Ticket.id.asc()),
        page=page,
        per_page=50,
    )
    queue_counts = TicketRoutingService.manual_queue_overview()
    return render_template(
        "admin/manual_clearing.html",
        pagination=pagination,
        queue=queue_counts,
    )


@admin_bp.route("/tickets")
@require_role("admin")
def tickets():
    page = request.args.get("page", 1, type=int)
    filters = {
        "status": request.args.get("status", "all"),
        "priority": request.args.get("priority", "all"),
        "category": request.args.get("category", "all"),
        "routing_method": request.args.get("routing_method", "all"),
        "search": request.args.get("search", ""),
        "sort": request.args.get("sort", "created_at"),
        "engineer_id": request.args.get("engineer_id", "", type=int) or "",
        "date_from": request.args.get("date_from", ""),
        "date_to": request.args.get("date_to", ""),
    }
    pagination = TicketService.get_all_tickets(filters, page)
    engineers = db.session.execute(
        select(User).where(User.role.in_(["engineer", "admin"])).order_by(User.name)
    ).scalars().all()
    return render_template(
        "admin/tickets.html",
        pagination=pagination,
        filters=filters,
        engineers=engineers,
    )


@admin_bp.route("/tickets/<ticket_id>")
@require_role("admin")
def ticket_detail(ticket_id):
    ticket = db.session.execute(
        select(Ticket).where(Ticket.ticket_id == ticket_id)
    ).scalar_one_or_none()
    if ticket is None:
        abort(404)

    activities = db.session.execute(
        select(TicketActivity)
        .where(TicketActivity.ticket_id == ticket.id)
        .order_by(TicketActivity.timestamp.asc())
    ).scalars().all()
    conversation_messages = []
    if ticket.conversation_id:
        conversation_messages = db.session.execute(
            select(Message)
            .where(Message.conversation_id == ticket.conversation_id)
            .order_by(Message.created_at.asc())
        ).scalars().all()

    engineers = db.session.execute(
        select(User).where(User.role.in_(["engineer", "admin"])).order_by(User.name)
    ).scalars().all()

    return render_template(
        "admin/ticket_detail.html",
        ticket=ticket,
        activities=activities,
        conversation_messages=conversation_messages,
        engineers=engineers,
        valid_statuses=VALID_STATUSES,
        valid_priorities=VALID_PRIORITIES,
    )


@admin_bp.route("/tickets/<ticket_id>/assign", methods=["POST"])
@require_role("admin")
def assign_ticket(ticket_id):
    engineer_id = request.form.get("engineer_id", type=int)
    if not engineer_id:
        flash("Please select an engineer.", "danger")
        return redirect(url_for("admin.ticket_detail", ticket_id=ticket_id))
    try:
        from flask_login import current_user
        TicketService.assign_engineer(ticket_id, engineer_id, current_user.id)
        flash("Ticket assigned successfully.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    except HTTPException:
        raise
    except Exception as exc:
        current_app.logger.error(
            "Ticket assignment failed (%s).", type(exc).__name__
        )
        flash("Ticket assignment failed. Please try again.", "danger")
    return redirect(url_for("admin.ticket_detail", ticket_id=ticket_id))


@admin_bp.route("/tickets/<ticket_id>/priority", methods=["POST"])
@require_role("admin")
def set_priority(ticket_id):
    from flask_login import current_user
    priority = request.form.get("priority")
    if priority not in VALID_PRIORITIES:
        flash("Invalid priority.", "danger")
        return redirect(url_for("admin.ticket_detail", ticket_id=ticket_id))
    TicketService.set_priority(ticket_id, priority, current_user.id)
    flash(f"Priority set to {priority}.", "success")
    return redirect(url_for("admin.ticket_detail", ticket_id=ticket_id))


@admin_bp.route("/tickets/<ticket_id>/status", methods=["POST"])
@require_role("admin")
def set_status(ticket_id):
    from flask_login import current_user
    new_status = request.form.get("status")
    note = request.form.get("note", "")
    resolution_notes = request.form.get("resolution_notes", "").strip()
    if new_status not in VALID_STATUSES:
        flash("Invalid status.", "danger")
        return redirect(url_for("admin.ticket_detail", ticket_id=ticket_id))
    try:
        TicketService.set_status(
            ticket_id, new_status, current_user.id,
            note=note,
            resolution_notes=resolution_notes if new_status == "Resolved" else None,
        )
        if new_status == "Closed":
            flash(f"Ticket #{ticket_id} has been officially CLOSED by {current_user.name} (Admin / Employee ID: #{current_user.id}).", "success")
        elif new_status == "Resolved":
            flash(f"Ticket #{ticket_id} has been RESOLVED by {current_user.name} (Admin / Employee ID: #{current_user.id}).", "success")
        else:
            flash(f"Status updated to {new_status}.", "success")
    except ConfirmationRequiredError:
        flash("Cannot set status to Resolved without employee confirmation.", "danger")
        return redirect(url_for("admin.ticket_detail", ticket_id=ticket_id)), 409
    return redirect(url_for("admin.ticket_detail", ticket_id=ticket_id))


@admin_bp.route("/tickets/<ticket_id>/close", methods=["POST"])
@require_role("admin")
def close_ticket(ticket_id):
    from flask_login import current_user
    closing_notes = request.form.get("closing_notes", "").strip()
    try:
        TicketService.set_status(
            ticket_id, "Closed", current_user.id,
            note=closing_notes or f"Ticket officially closed by {current_user.name} (Admin / Employee ID: #{current_user.id}).",
        )
        flash(
            f"Ticket #{ticket_id} has been officially CLOSED by {current_user.name} (Admin / Employee ID: #{current_user.id}).",
            "success"
        )
    except Exception as exc:
        flash(f"Failed to close ticket: {exc}", "danger")
    return redirect(url_for("admin.ticket_detail", ticket_id=ticket_id))


@admin_bp.route("/tickets/<ticket_id>/escalate", methods=["POST"])
@require_role("admin")
def escalate_ticket(ticket_id):
    from flask_login import current_user
    note = request.form.get("note", "Escalated by admin")
    try:
        TicketService.set_status(ticket_id, "Escalated", current_user.id, note=note)
        flash("Ticket escalated.", "warning")
    except ConfirmationRequiredError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("admin.ticket_detail", ticket_id=ticket_id))


@admin_bp.route("/tickets/<ticket_id>/note", methods=["POST"])
@require_role("admin")
def add_note(ticket_id):
    from flask_login import current_user
    note_text = request.form.get("note", "").strip()
    is_internal = request.form.get("is_internal", "true").lower() == "true"
    if not note_text:
        flash("Note cannot be empty.", "danger")
        return redirect(url_for("admin.ticket_detail", ticket_id=ticket_id))
    TicketService.add_note(ticket_id, current_user.id, note_text, is_internal=is_internal)
    flash("Note added.", "success")
    return redirect(url_for("admin.ticket_detail", ticket_id=ticket_id))


@admin_bp.route("/engineers")
@require_role("admin")
def engineers():
    engineer_rows = db.session.execute(
        select(User).where(User.role.in_(["engineer", "admin"])).order_by(User.role.desc(), User.name.asc())
    ).scalars().all()
    engineer_stats = []
    for eng in engineer_rows:
        assigned = db.session.execute(
            select(func.count(Ticket.id)).where(
                Ticket.assigned_engineer_id == eng.id,
                Ticket.status.in_(["Open", "In Progress", "Escalated"]),
            )
        ).scalar() or 0
        engineer_stats.append({"engineer": eng, "active_tickets": assigned})
    return render_template("admin/engineers.html", engineer_stats=engineer_stats)


@admin_bp.route("/reports")
@require_role("admin")
def reports():
    return render_template("admin/reports.html")


@admin_bp.route("/settings", methods=["GET", "POST"])
@require_role("admin")
def settings():
    if request.method == "POST":
        SettingsService.set_many({
            "site_name": request.form.get("site_name", "ResolveAI"),
            "email_notifications_enabled": request.form.get("email_notifications_enabled", "false"),
            "default_sla_hours": request.form.get("default_sla_hours", "24"),
            "support_email": request.form.get("support_email", ""),
        })
        flash("Settings saved.", "success")
        return redirect(url_for("admin.settings"))
    current_settings = SettingsService.get_all()
    return render_template("admin/settings.html", settings=current_settings)


@admin_bp.route("/knowledge-base")
@require_role("admin")
def knowledge_base():
    from services.kb_service import KBService
    page = request.args.get("page", 1, type=int)
    pagination = KBService.get_all(page=page)
    return render_template("admin/knowledge_base.html", pagination=pagination)


@admin_bp.route("/knowledge-base/new", methods=["GET", "POST"])
@require_role("admin")
def knowledge_base_new():
    from flask_login import current_user
    from models.knowledge_base import KBArticle, _slugify
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        if not title:
            flash("Title is required.", "danger")
            return render_template("admin/knowledge_base_article.html", article=None)
        slug = _slugify(title)
        # Ensure unique slug
        existing = db.session.execute(
            select(KBArticle).where(KBArticle.slug == slug)
        ).scalar_one_or_none()
        if existing:
            slug = slug + f"-{db.session.execute(select(func.count(KBArticle.id))).scalar()}"
        article = KBArticle(
            title=title,
            slug=slug,
            category=request.form.get("category", "general"),
            symptoms=request.form.get("symptoms", ""),
            causes=request.form.get("causes", ""),
            solution=request.form.get("solution", ""),
            escalation=request.form.get("escalation", ""),
            status=request.form.get("status", "draft"),
            created_by=current_user.id,
        )
        db.session.add(article)
        db.session.commit()
        flash("Article created.", "success")
        return redirect(url_for("admin.knowledge_base"))
    return render_template("admin/knowledge_base_article.html", article=None)


@admin_bp.route("/knowledge-base/<int:article_id>", methods=["GET", "POST"])
@require_role("admin")
def knowledge_base_article(article_id):
    from models.knowledge_base import KBArticle
    article = db.session.get(KBArticle, article_id)
    if article is None:
        abort(404)
    if request.method == "POST":
        article.title = request.form.get("title", article.title).strip()
        article.category = request.form.get("category", article.category)
        article.symptoms = request.form.get("symptoms", "")
        article.causes = request.form.get("causes", "")
        article.solution = request.form.get("solution", "")
        article.escalation = request.form.get("escalation", "")
        article.status = request.form.get("status", article.status)
        db.session.commit()
        flash("Article updated.", "success")
        return redirect(url_for("admin.knowledge_base"))
    return render_template("admin/knowledge_base_article.html", article=article)
