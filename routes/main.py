from flask import Blueprint, render_template, redirect, url_for
from sqlalchemy import select, func
from models import db
from models.ticket import Ticket

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def landing():
    # Live stats from DB
    total_tickets = db.session.execute(select(func.count(Ticket.id))).scalar() or 0
    resolved_tickets = db.session.execute(
        select(func.count(Ticket.id)).where(Ticket.status == "Resolved")
    ).scalar() or 0
    resolution_rate = round((resolved_tickets / total_tickets * 100) if total_tickets > 0 else 0)
    return render_template(
        "landing.html",
        total_tickets=total_tickets,
        resolved_tickets=resolved_tickets,
        resolution_rate=resolution_rate,
    )


@main_bp.route("/overview")
def overview():
    return redirect(url_for("admin.dashboard"))


@main_bp.route("/login")
def login():
    return redirect(url_for("auth.login"))
