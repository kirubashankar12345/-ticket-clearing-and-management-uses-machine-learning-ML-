import pytest
from sqlalchemy import select
from models import db
from models.ticket import Ticket
from models.user import User
from services.ticket_service import TicketService
from tests.conftest import login


def _get_unconfirmed_ticket_id(app):
    with app.app_context():
        ticket = db.session.execute(
            select(Ticket).where(Ticket.employee_confirmed == False)
        ).scalar_one_or_none()
        return ticket.ticket_id if ticket else None


def test_admin_can_assign(client, app):
    """Admin can POST to assign a ticket to an engineer."""
    with app.app_context():
        engineer = db.session.execute(
            select(User).where(User.email == "engineer@test.com")
        ).scalar_one()
        ticket = db.session.execute(select(Ticket)).scalar_one_or_none()
        assert ticket is not None
        ticket_str_id = ticket.ticket_id
        eng_id = engineer.id

    login(client, "admin@test.com", "Admin@123")
    resp = client.post(
        f"/admin/tickets/{ticket_str_id}/assign",
        data={"engineer_id": eng_id, "csrf_token": "test"},
        follow_redirects=False,
    )
    # Should redirect (302) after successful assign
    assert resp.status_code in (200, 302), f"Expected 200 or 302, got {resp.status_code}"

    # Verify assignment in DB
    with app.app_context():
        t = db.session.execute(
            select(Ticket).where(Ticket.ticket_id == ticket_str_id)
        ).scalar_one()
        assert t.assigned_engineer_id == eng_id


def test_admin_can_resolve_manual_ticket(client, app):
    """Admin can close a manually routed ticket after human support."""
    ticket_str_id = _get_unconfirmed_ticket_id(app)
    if ticket_str_id is None:
        pytest.skip("No unconfirmed ticket available")

    login(client, "admin@test.com", "Admin@123")
    resp = client.post(
        f"/api/tickets/{ticket_str_id}/status",
        json={"status": "Resolved"},
        headers={"X-CSRFToken": "test"},
        content_type="application/json",
    )
    assert resp.status_code == 200, \
        f"Expected successful resolution, got {resp.status_code}: {resp.data}"
    with app.app_context():
        ticket = db.session.execute(
            select(Ticket).where(Ticket.ticket_id == ticket_str_id)
        ).scalar_one()
        assert ticket.status == "Resolved"
        assert ticket.resolution_method == "Manual"


def test_stats_endpoint(client, app):
    """GET /api/admin/stats returns JSON with ticket count fields."""
    login(client, "admin@test.com", "Admin@123")
    resp = client.get("/api/admin/stats")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data is not None
    for field in ("total", "open", "in_progress", "escalated", "resolved"):
        assert field in data, f"Missing field '{field}' in stats response"
    assert isinstance(data["total"], int)


def test_admin_dashboard_offers_both_ticket_clearing_workflows(client):
    login(client, "admin@test.com", "Admin@123")
    response = client.get("/admin/dashboard")
    assert response.status_code == 200
    assert b"AI Ticket Clearing" in response.data
    assert b"Manual Ticket Clearing" in response.data
    assert b"eligible open ticket(s)" in response.data


def test_manual_clearing_queue_is_admin_only_and_shows_counts(client):
    login(client, "admin@test.com", "Admin@123")
    response = client.get("/admin/manual-clearing")
    assert response.status_code == 200
    assert b"Human support queue" in response.data
    assert b"Pending" in response.data
    assert b"In Progress" in response.data
    assert b"Resolved manually" in response.data

    with client.application.test_client() as employee_client:
        login(employee_client, "employee@test.com", "Employee@123")
        assert employee_client.get("/admin/manual-clearing").status_code == 403


def test_only_admin_can_start_bulk_ai_clearing(client):
    with client.application.test_client() as employee_client:
        login(employee_client, "employee@test.com", "Employee@123")
        response = employee_client.post("/admin/ai-clearing/start")
    assert response.status_code == 403


def test_admin_bulk_ai_clearing_waits_for_employee_confirmation(
    client, app, monkeypatch
):
    with app.app_context():
        employee = db.session.execute(
            select(User).where(User.email == "employee@test.com")
        ).scalar_one()
        ticket = TicketService.create_ticket(
            employee.id, "Unable to access the payroll application."
        )
        ticket.routing_method = "ai"
        ticket_id = ticket.ticket_id
        db.session.commit()

    monkeypatch.setattr(
        "services.ai_clearing_service.AIService.get_response",
        lambda *args, **kwargs: {
            "role": "assistant",
            "content": "Clear the browser cache, then sign in again.",
            "category": "login",
            "priority": "High",
            "suggested_ticket": False,
            "ask_confirmation": True,
        },
    )
    from services.ai_clearing_service import AiClearingService

    monkeypatch.setattr(
        AiClearingService,
        "_launch_worker",
        staticmethod(lambda worker_app, job_id: AiClearingService._run_job(worker_app, job_id)),
    )
    login(client, "admin@test.com", "Admin@123")
    response = client.post("/admin/ai-clearing/start")
    assert response.status_code == 202
    job = response.get_json()["job"]
    assert job["status"] == "completed"
    assert job["processed"] == job["total"]
    assert job["awaiting_confirmation"] >= 1

    with app.app_context():
        processed = db.session.execute(
            select(Ticket).where(Ticket.ticket_id == ticket_id)
        ).scalar_one()
        assert processed.routing_method == "ai"
        assert processed.status == "Waiting for Employee"
        assert processed.resolution_method is None
        assert processed.employee_confirmed is False
        assert processed.ai_recommendation
        assert processed.conversation_id is not None
        conversation_id = processed.conversation_id

    with app.test_client() as employee_client:
        login(employee_client, "employee@test.com", "Employee@123")
        confirmation = employee_client.post(
            "/api/chat/message",
            json={
                "message": "Yes, it's fixed",
                "conversation_id": conversation_id,
            },
        )
        assert confirmation.status_code == 200
        assert "now resolved" in confirmation.get_json()["reply"]
    with app.app_context():
        processed = db.session.execute(
            select(Ticket).where(Ticket.ticket_id == ticket_id)
        ).scalar_one()
        assert processed.status == "Resolved"
        assert processed.resolution_method == "AI"
        assert processed.employee_confirmed is True


def test_ai_failure_in_bulk_clearing_transfers_ticket_to_manual_queue(
    client, app, monkeypatch
):
    with app.app_context():
        employee = db.session.execute(
            select(User).where(User.email == "other@test.com")
        ).scalar_one()
        ticket = TicketService.create_ticket(
            employee.id, "The finance application crashes when it opens."
        )
        ticket.routing_method = "ai"
        ticket_id = ticket.ticket_id
        db.session.commit()

    def unavailable(*args, **kwargs):
        from services.exceptions import AIServiceUnavailableError
        raise AIServiceUnavailableError("provider response must not leak")

    monkeypatch.setattr("services.ai_clearing_service.AIService.get_response", unavailable)
    from services.ai_clearing_service import AiClearingService

    monkeypatch.setattr(
        AiClearingService,
        "_launch_worker",
        staticmethod(lambda worker_app, job_id: AiClearingService._run_job(worker_app, job_id)),
    )
    login(client, "admin@test.com", "Admin@123")
    response = client.post("/admin/ai-clearing/start")
    assert response.status_code == 202
    job = response.get_json()["job"]
    assert job["transferred_to_manual"] >= 1

    with app.app_context():
        transferred = db.session.execute(
            select(Ticket).where(Ticket.ticket_id == ticket_id)
        ).scalar_one()
        assert transferred.routing_method == "manual"
        assert transferred.status == "Open"
        assert "AI service unavailable" in transferred.routing_reason
        assert transferred.conversation_id is not None


def test_bulk_ai_clearing_start_is_idempotent_while_job_is_active(
    app, monkeypatch
):
    from models.ai_clearing import AiClearingJob
    from services.ai_clearing_service import AiClearingService

    monkeypatch.setattr(
        AiClearingService,
        "_launch_worker",
        staticmethod(lambda worker_app, job_id: None),
    )
    with app.app_context():
        admin = db.session.execute(
            select(User).where(User.email == "admin@test.com")
        ).scalar_one()
        employee = db.session.execute(
            select(User).where(User.email == "employee@test.com")
        ).scalar_one()
        eligible = TicketService.create_ticket(
            employee.id, "Ticket reserved for AI job idempotency check."
        )
        eligible.routing_method = "ai"
        db.session.commit()
        first, created = AiClearingService.start_job(admin.id)
        second, created_again = AiClearingService.start_job(admin.id)
        assert created is True
        assert created_again is False
        assert second.id == first.id
        first.status = "completed"
        db.session.commit()
        assert db.session.get(AiClearingJob, first.id).status == "completed"
