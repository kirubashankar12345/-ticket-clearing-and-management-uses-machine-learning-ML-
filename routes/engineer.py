from flask import (
    Blueprint, render_template, redirect, url_for,
    flash, request, abort, current_app
)
from flask_login import current_user
from sqlalchemy import select
from werkzeug.exceptions import HTTPException
from models import db
from models.ticket import Ticket, TicketActivity
from routes import require_role
from services.ticket_service import TicketService
from services.exceptions import ConfirmationRequiredError

engineer_bp = Blueprint("engineer", __name__)


def _require_assigned_ticket(ticket_id):
    ticket = db.session.execute(
        select(Ticket).where(Ticket.ticket_id == ticket_id)
    ).scalar_one_or_none()
    if ticket is None:
        abort(404)
    if ticket.assigned_engineer_id != current_user.id:
        abort(403)
    return ticket


@engineer_bp.route("/dashboard")
@require_role("engineer")
def dashboard():
    from sqlalchemy import func
    # Assigned tickets
    assigned = db.session.execute(
        select(Ticket)
        .where(
            Ticket.assigned_engineer_id == current_user.id,
            Ticket.status.in_(["Open", "In Progress", "Escalated", "Waiting for Employee"]),
        )
        .order_by(Ticket.created_at.desc())
    ).scalars().all()

    # Unassigned queue
    queue = db.session.execute(
        select(Ticket)
        .where(Ticket.assigned_engineer_id.is_(None), Ticket.status == "Open")
        .order_by(Ticket.created_at.asc())
        .limit(20)
    ).scalars().all()

    stats = {
        "assigned": len(assigned),
        "open": sum(1 for t in assigned if t.status == "Open"),
        "escalated": sum(1 for t in assigned if t.status == "Escalated"),
        "in_progress": sum(1 for t in assigned if t.status == "In Progress"),
    }
    return render_template(
        "engineer/dashboard.html",
        assigned=assigned,
        queue=queue,
        stats=stats,
    )


@engineer_bp.route("/tickets/<ticket_id>")
@require_role("engineer")
def ticket_detail(ticket_id):
    ticket = db.session.execute(
        select(Ticket).where(Ticket.ticket_id == ticket_id)
    ).scalar_one_or_none()
    if ticket is None:
        abort(404)

    # Engineers can view tickets assigned to them or any open ticket
    if (
        ticket.assigned_engineer_id != current_user.id
        and ticket.assigned_engineer_id is not None
    ):
        abort(403)

    activities = db.session.execute(
        select(TicketActivity)
        .where(TicketActivity.ticket_id == ticket.id)
        .order_by(TicketActivity.timestamp.asc())
    ).scalars().all()

    return render_template(
        "engineer/ticket_detail.html",
        ticket=ticket,
        activities=activities,
    )


@engineer_bp.route("/tickets/<ticket_id>/note", methods=["POST"])
@require_role("engineer")
def add_note(ticket_id):
    _require_assigned_ticket(ticket_id)
    note_text = request.form.get("note", "").strip()
    if not note_text:
        flash("Note cannot be empty.", "danger")
        return redirect(url_for("engineer.ticket_detail", ticket_id=ticket_id))
    TicketService.add_note(ticket_id, current_user.id, note_text, is_internal=True)
    flash("Note added.", "success")
    return redirect(url_for("engineer.ticket_detail", ticket_id=ticket_id))


@engineer_bp.route("/tickets/<ticket_id>/respond", methods=["POST"])
@require_role("engineer")
def respond_to_ticket(ticket_id):
    _require_assigned_ticket(ticket_id)
    response_text = request.form.get("response", "").strip()
    if not response_text:
        flash("Response cannot be empty.", "danger")
        return redirect(url_for("engineer.ticket_detail", ticket_id=ticket_id))
    TicketService.add_note(ticket_id, current_user.id, response_text, is_internal=False)
    try:
        TicketService.set_status(
            ticket_id, "Waiting for Employee", current_user.id,
            note=f"Engineer responded: {response_text[:80]}"
        )
    except ConfirmationRequiredError:
        pass
    flash("Response sent to employee.", "success")
    return redirect(url_for("engineer.ticket_detail", ticket_id=ticket_id))


@engineer_bp.route("/tickets/<ticket_id>/escalate", methods=["POST"])
@require_role("engineer")
def escalate_ticket(ticket_id):
    _require_assigned_ticket(ticket_id)
    note = request.form.get("note", "Escalated by engineer").strip()
    try:
        TicketService.set_status(ticket_id, "Escalated", current_user.id, note=note)
        flash("Ticket escalated.", "warning")
    except ConfirmationRequiredError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("engineer.ticket_detail", ticket_id=ticket_id))


@engineer_bp.route("/tickets/<ticket_id>/resolve", methods=["POST"])
@require_role("engineer")
def resolve_ticket(ticket_id):
    _require_assigned_ticket(ticket_id)
    note = request.form.get("note", "Resolved by assigned engineer").strip()
    try:
        TicketService.set_status(
            ticket_id, "Resolved", current_user.id, note=note
        )
        flash("Ticket marked resolved.", "success")
    except ConfirmationRequiredError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("engineer.ticket_detail", ticket_id=ticket_id))


@engineer_bp.route("/tickets/<ticket_id>/claim", methods=["POST"])
@require_role("engineer")
def claim_ticket(ticket_id):
    try:
        TicketService.assign_engineer(ticket_id, current_user.id, current_user.id)
        flash("Ticket claimed.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    except HTTPException:
        raise
    except Exception as exc:
        current_app.logger.error(
            "Ticket claim failed (%s).", type(exc).__name__
        )
        flash("Ticket claim failed. Please try again.", "danger")
    return redirect(url_for("engineer.ticket_detail", ticket_id=ticket_id))
