import re
import pytest
from sqlalchemy import select
from models import db
from models.ticket import Ticket, TicketCounter, generate_ticket_id
from models.user import User
from services.ticket_service import TicketService
from services.exceptions import ConfirmationRequiredError
from tests.conftest import login


def test_create_ticket_format(app):
    """ticket.ticket_id matches r'^IT-\d{5}$'."""
    with app.app_context():
        employee = db.session.execute(
            select(User).where(User.email == "employee@test.com")
        ).scalar_one()
        ticket = TicketService.create_ticket(
            employee_id=employee.id,
            issue_description="Test ticket for format check — application crash.",
            category="crashes",
            priority="Normal",
        )
        assert re.match(r'^IT-\d{5}$', ticket.ticket_id), \
            f"ticket_id '{ticket.ticket_id}' does not match IT-XXXXX format"


def test_two_tickets_different_ids(app):
    """Two tickets created in sequence get different IDs."""
    with app.app_context():
        employee = db.session.execute(
            select(User).where(User.email == "employee@test.com")
        ).scalar_one()
        t1 = TicketService.create_ticket(
            employee_id=employee.id,
            issue_description="First test ticket for uniqueness check.",
            category="login",
            priority="Normal",
        )
        t2 = TicketService.create_ticket(
            employee_id=employee.id,
            issue_description="Second test ticket for uniqueness check.",
            category="crashes",
            priority="Normal",
        )
        assert t1.ticket_id != t2.ticket_id, \
            f"Both tickets got the same ID: {t1.ticket_id}"


def test_resolve_without_confirmation(client, app):
    """POST /api/tickets/IT-00001/status {status:resolved} with employee_confirmed=False returns 409."""
    # Get an unconfirmed ticket owned by the employee used for this request.
    with app.app_context():
        employee = db.session.execute(
            select(User).where(User.email == "employee@test.com")
        ).scalar_one()
        ticket = db.session.execute(
            select(Ticket).where(
                Ticket.employee_confirmed == False,
                Ticket.employee_id == employee.id,
            ).limit(1)
        ).scalar_one_or_none()
        assert ticket is not None, "Need an unconfirmed ticket for this test"
        ticket_str_id = ticket.ticket_id

    login(client, "employee@test.com", "Employee@123")
    resp = client.post(
        f"/api/tickets/{ticket_str_id}/status",
        json={"status": "Resolved"},
        headers={"X-CSRFToken": "test"},
        content_type="application/json",
    )
    assert resp.status_code == 409, \
        f"Expected 409, got {resp.status_code}: {resp.data}"


def test_cross_employee_access(client, app):
    """Employee cannot GET /employee/tickets/<other_employee_ticket_id> — returns 403."""
    # Get a ticket belonging to the first employee
    with app.app_context():
        emp1 = db.session.execute(
            select(User).where(User.email == "employee@test.com")
        ).scalar_one()
        ticket = db.session.execute(
            select(Ticket).where(Ticket.employee_id == emp1.id).limit(1)
        ).scalar_one_or_none()
        assert ticket is not None
        ticket_str_id = ticket.ticket_id

    # Login as the OTHER employee using a fresh client
    with app.test_client() as fresh_client:
        login(fresh_client, "other@test.com", "Employee@123")
        resp = fresh_client.get(f"/employee/tickets/{ticket_str_id}", follow_redirects=False)
        assert resp.status_code in (403, 302), \
            f"Expected 403 or redirect, got {resp.status_code}"


def test_confirm_resolution(app):
    """confirm_resolution sets employee_confirmed=True and status=Resolved."""
    with app.app_context():
        employee = db.session.execute(
            select(User).where(User.email == "employee@test.com")
        ).scalar_one()
        # Create a fresh ticket to confirm
        ticket = TicketService.create_ticket(
            employee_id=employee.id,
            issue_description="Ticket to be confirmed as resolved.",
            category="login",
            priority="Normal",
        )
        ticket.routing_method = "ai"
        ticket.status = "Waiting for Employee"
        db.session.commit()
        str_id = ticket.ticket_id

        # Now confirm it
        resolved = TicketService.confirm_resolution(str_id, employee.id)
        assert resolved.employee_confirmed is True
        assert resolved.status == "Resolved"
        assert resolved.resolution_method == "AI"


def test_manual_ticket_cannot_be_resolved_by_employee_confirmation(app):
    with app.app_context():
        employee = db.session.execute(
            select(User).where(User.email == "employee@test.com")
        ).scalar_one()
        ticket = TicketService.create_ticket(
            employee_id=employee.id,
            issue_description="Manual support ticket requiring engineer resolution.",
            category="general",
            priority="Normal",
        )
        assert ticket.routing_method == "manual"

        with pytest.raises(ConfirmationRequiredError):
            TicketService.confirm_resolution(ticket.ticket_id, employee.id)

        assert ticket.status == "Open"
        assert ticket.employee_confirmed is False
