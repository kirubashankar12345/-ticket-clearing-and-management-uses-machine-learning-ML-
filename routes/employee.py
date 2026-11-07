from flask import (
    Blueprint, render_template, redirect, url_for,
    flash, request, abort
)
from flask_login import current_user
from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, IntegerField
from wtforms.validators import DataRequired, Length, NumberRange, Optional
from sqlalchemy import select
from models import db
from models.conversation import Conversation, Message
from models.feedback import EmployeeFeedback
from routes import require_role
from services.ticket_service import TicketService
from services.exceptions import ConfirmationRequiredError, ReopenWindowExpiredError

employee_bp = Blueprint("employee", __name__)


class CommentForm(FlaskForm):
    comment = TextAreaField("Comment", validators=[DataRequired(), Length(1, 2000)])


class ProfileForm(FlaskForm):
    name = StringField("Full Name", validators=[DataRequired(), Length(2, 120)])
    department = StringField("Department", validators=[Length(0, 120)])


class FeedbackForm(FlaskForm):
    rating = IntegerField("Rating", validators=[DataRequired(), NumberRange(1, 5)])
    comment = TextAreaField("Comment", validators=[Optional(), Length(0, 1000)])


@employee_bp.route("/")
@employee_bp.route("/raise-ticket", methods=["GET", "POST"])
@require_role("employee", "admin")
def raise_ticket():
    from services.ticket_routing_service import TicketRoutingService

    if request.method == "POST":
        issue_desc = request.form.get("issue_description", "").strip()
        category = request.form.get("category", "general").strip()
        priority = request.form.get("priority", "Normal").strip()

        if len(issue_desc) < 10:
            flash("Please provide at least 10 characters describing the issue.", "danger")
            return redirect(url_for("employee.raise_ticket"))

        try:
            chosen_priority = priority if priority in {"Low", "Normal", "High", "Urgent"} else "Normal"
            ticket = TicketService.create_ticket(
                employee_id=current_user.id,
                issue_description=issue_desc,
                category=category,
                priority=chosen_priority,
            )
            if ticket.status == "Resolved" and ticket.resolution_method == "AI":
                flash(f"Simple ticket #{ticket.ticket_id} ({ticket.priority} priority) was automatically analyzed and CLEARED by ResolveAI!", "success")
            elif ticket.routing_method == "manual":
                flash(f"Ticket #{ticket.ticket_id} ({ticket.priority} priority) is a manual ticket. High/Urgent issues require manual clearance by an Administrator or Engineer.", "warning" if ticket.priority in ("High", "Urgent") else "info")
            else:
                flash(f"Ticket #{ticket.ticket_id} has been successfully raised!", "success")
            return redirect(url_for("employee.ticket_detail", ticket_id=ticket.ticket_id))
        except Exception as exc:
            flash(f"Could not raise ticket: {exc}", "danger")
            return redirect(url_for("employee.raise_ticket"))

    categories = [
        "general",
        "software_crash",
        "login_auth",
        "network_vpn",
        "database_access",
        "permissions",
        "email_client",
        "other",
    ]
    category_labels = {
        "general": "General Software Issue",
        "software_crash": "Application Crash / Freeze",
        "login_auth": "Login & Authentication",
        "network_vpn": "VPN & Internal Network",
        "database_access": "Database & Query Timeouts",
        "permissions": "Role / Access Permissions",
        "email_client": "Email & Messaging Client",
        "other": "Other Technical Problem",
    }
    stats = TicketService.get_stats_for_employee(current_user.id)
    recent = TicketService.get_tickets_for_employee(
        current_user.id,
        filters={},
        page=1,
    )
    routing = TicketRoutingService.current_mode()

    return render_template(
        "employee/raise_ticket.html",
        categories=categories,
        category_labels=category_labels,
        stats=stats,
        recent=recent,
        routing=routing,
    )


@employee_bp.route("/dashboard")
@require_role("employee", "admin")
def dashboard():
    stats = TicketService.get_stats_for_employee(current_user.id)
    recent = TicketService.get_tickets_for_employee(
        current_user.id,
        filters={},
        page=1,
    )
    return render_template("employee/dashboard.html", stats=stats, recent=recent)


@employee_bp.route("/chat")
@require_role("employee", "admin")
def chat():
    linked_ticket_id = request.args.get("ticket_id", "").strip().upper()
    linked_ticket = (
        TicketService.get_ticket_for_employee(linked_ticket_id, current_user.id)
        if linked_ticket_id else None
    )
    conv = db.session.execute(
        select(Conversation).where(
            Conversation.employee_id == current_user.id
        ).order_by(Conversation.updated_at.desc(), Conversation.id.desc())
    ).scalars().first()

    messages = []
    conv_id = None
    if conv:
        if linked_ticket:
            conv.active_ticket_id = linked_ticket.ticket_id
            db.session.commit()
        conv_id = conv.id
        messages = db.session.execute(
            select(Message)
            .where(Message.conversation_id == conv.id)
            .order_by(Message.created_at.asc())
        ).scalars().all()

    from services.ticket_routing_service import TicketRoutingService

    routing = TicketRoutingService.current_mode()
    support_mode = "ai" if linked_ticket or messages else routing["mode"]
    return render_template(
        "employee/chat.html",
        conversation_id=conv_id,
        messages=messages,
        linked_ticket=linked_ticket,
        support_mode=support_mode,
        routing=routing,
    )


@employee_bp.route("/tickets")
@require_role("employee", "admin")
def tickets():
    page = request.args.get("page", 1, type=int)
    filters = {
        "status": request.args.get("status", "all"),
        "priority": request.args.get("priority", "all"),
        "search": request.args.get("search", ""),
        "sort": request.args.get("sort", "created_at"),
    }
    if current_user.role == "admin":
        pagination = TicketService.get_tickets_for_employee(None, filters, page)
    else:
        pagination = TicketService.get_tickets_for_employee(current_user.id, filters, page)
    return render_template("employee/tickets.html", pagination=pagination, filters=filters)


@employee_bp.route("/tickets/<ticket_id>")
@require_role("employee", "admin")
def ticket_detail(ticket_id):
    ticket = TicketService.get_ticket_for_employee(ticket_id, current_user.id)
    # Fetch activities excluding internal notes
    from models.ticket import TicketActivity
    activities = db.session.execute(
        select(TicketActivity)
        .where(
            TicketActivity.ticket_id == ticket.id,
            TicketActivity.is_internal == False,  # noqa: E712
        )
        .order_by(TicketActivity.timestamp.asc())
    ).scalars().all()

    comment_form = CommentForm()
    feedback_form = FeedbackForm()
    existing_feedback = db.session.execute(
        select(EmployeeFeedback).where(EmployeeFeedback.ticket_id == ticket.id)
    ).scalar_one_or_none()

    return render_template(
        "employee/ticket_detail.html",
        ticket=ticket,
        activities=activities,
        comment_form=comment_form,
        feedback_form=feedback_form,
        existing_feedback=existing_feedback,
    )


@employee_bp.route("/tickets/<ticket_id>/confirm", methods=["POST"])
@require_role("employee", "admin")
def confirm_resolution(ticket_id):
    try:
        TicketService.confirm_resolution(ticket_id, current_user.id)
        flash("Great! Your ticket has been marked as resolved.", "success")
    except ConfirmationRequiredError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("employee.ticket_detail", ticket_id=ticket_id))


@employee_bp.route("/tickets/<ticket_id>/reject", methods=["POST"])
@require_role("employee", "admin")
def reject_resolution(ticket_id):
    description = request.form.get("description", "")
    try:
        ticket = TicketService.get_ticket_for_employee(ticket_id, current_user.id)
        if ticket.routing_method == "ai":
            TicketService.route_ai_ticket_to_manual(
                ticket_id,
                f"Employee reported the issue was not resolved. {description}".strip(),
            )
        else:
            TicketService.reject_resolution(ticket_id, current_user.id, description)
        flash("Noted — your ticket has been kept open for further review.", "info")
    except Exception as exc:
        flash(str(exc), "danger")
    return redirect(url_for("employee.ticket_detail", ticket_id=ticket_id))


@employee_bp.route("/tickets/<ticket_id>/reopen", methods=["POST"])
@require_role("employee", "admin")
def reopen_ticket(ticket_id):
    try:
        TicketService.reopen_ticket(ticket_id, current_user.id)
        flash("Ticket reopened successfully.", "success")
    except ReopenWindowExpiredError:
        flash("The 7-day reopen window has expired. Please create a new ticket.", "warning")
    except Exception as exc:
        flash(str(exc), "danger")
    return redirect(url_for("employee.ticket_detail", ticket_id=ticket_id))


@employee_bp.route("/tickets/<ticket_id>/comment", methods=["POST"])
@require_role("employee", "admin")
def add_comment(ticket_id):
    # Verify ownership first
    ticket = TicketService.get_ticket_for_employee(ticket_id, current_user.id)
    form = CommentForm()
    if form.validate_on_submit():
        TicketService.add_note(ticket_id, current_user.id, form.comment.data, is_internal=False)
        flash("Comment added.", "success")
    else:
        flash("Comment cannot be empty.", "danger")
    return redirect(url_for("employee.ticket_detail", ticket_id=ticket_id))


@employee_bp.route("/tickets/<ticket_id>/feedback", methods=["POST"])
@require_role("employee", "admin")
def submit_feedback(ticket_id):
    ticket = TicketService.get_ticket_for_employee(ticket_id, current_user.id)
    if ticket.status != "Resolved":
        flash("Feedback can only be submitted for resolved tickets.", "warning")
        return redirect(url_for("employee.ticket_detail", ticket_id=ticket_id))

    form = FeedbackForm()
    if form.validate_on_submit():
        existing = db.session.execute(
            select(EmployeeFeedback).where(EmployeeFeedback.ticket_id == ticket.id)
        ).scalar_one_or_none()
        if existing:
            flash("You have already submitted feedback for this ticket.", "info")
        else:
            fb = EmployeeFeedback(
                ticket_id=ticket.id,
                employee_id=current_user.id,
                rating=form.rating.data,
                comment=form.comment.data,
            )
            db.session.add(fb)
            db.session.commit()
            flash("Thank you for your feedback!", "success")
    else:
        flash("Invalid feedback. Please provide a rating between 1 and 5.", "danger")

    return redirect(url_for("employee.ticket_detail", ticket_id=ticket_id))


@employee_bp.route("/profile", methods=["GET", "POST"])
@require_role("employee", "admin")
def profile():
    form = ProfileForm(obj=current_user)
    if form.validate_on_submit():
        current_user.name = form.name.data.strip()
        current_user.department = form.department.data or None
        db.session.commit()
        flash("Profile updated successfully.", "success")
        return redirect(url_for("employee.profile"))
    return render_template("employee/profile.html", form=form)
