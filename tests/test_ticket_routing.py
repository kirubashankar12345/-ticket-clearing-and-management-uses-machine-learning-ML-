from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from models import db
from models.conversation import Conversation, Message
from models.ticket import Ticket
from models.user import User
from services.ticket_service import TicketService
from services.ticket_routing_service import TicketRoutingService
from tests.conftest import login


def _employee_ids():
    return [
        user.id
        for user in db.session.execute(
            select(User).where(User.role == "employee").order_by(User.id)
        ).scalars()
    ]


def test_new_tickets_switch_to_ai_at_rolling_window_threshold_across_employees(app):
    with app.app_context():
        app.config["AI_ROUTING_THRESHOLD"] = 5
        app.config["AI_ROUTING_WINDOW_MINUTES"] = 60
        employees = _employee_ids()
        tickets = [
            TicketService.create_ticket(
                employee_id=employees[index % 2],
                issue_description=f"Cross-employee ticket number {index} for routing.",
            )
            for index in range(6)
        ]
        assert [ticket.routing_method for ticket in tickets] == [
            "manual", "manual", "manual", "manual", "ai", "ai"
        ]
        assert TicketRoutingService.current_mode() == {
            "mode": "ai",
            "recent_count": 6,
            "threshold": 5,
            "window_minutes": 60,
        }


def test_tickets_outside_window_do_not_increase_routing_volume(app):
    with app.app_context():
        app.config["AI_ROUTING_THRESHOLD"] = 5
        app.config["AI_ROUTING_WINDOW_MINUTES"] = 60
        employee_id = _employee_ids()[0]
        old_ticket = db.session.execute(select(Ticket).limit(1)).scalar_one()
        old_ticket.created_at = datetime.utcnow() - timedelta(minutes=61)
        db.session.commit()

        ticket = TicketService.create_ticket(
            employee_id, "A request after older tickets have expired."
        )
        assert ticket.routing_method == "manual"
        assert TicketRoutingService.recent_ticket_count() == 1


def test_critical_issue_immediately_escalates_to_humans(app):
    with app.app_context():
        employee_id = _employee_ids()[0]
        ticket = TicketService.create_ticket(
            employee_id=employee_id,
            issue_description="Critical production outage: production is down for everyone.",
        )
        assert ticket.routing_method == "manual"
        assert ticket.status == "Escalated"
        assert ticket.priority == "Urgent"


def test_dashboard_shows_active_mode_and_recent_volume(client):
    login(client, "admin@test.com", "Admin@123")
    response = client.get("/admin/dashboard")
    assert response.status_code == 200
    assert b"Current clearing mode: Manual Support Mode" in response.data
    assert b"0 ticket(s) in the last" in response.data
    assert b"AI routing starts at" in response.data


def _create_ai_ticket_with_confirmation_prompt():
    employee = db.session.execute(
        select(User).where(User.email == "employee@test.com")
    ).scalar_one()
    ticket = TicketService.create_ticket(
        employee.id, "The application is not letting me sign in."
    )
    conversation = Conversation(
        employee_id=employee.id,
        active_ticket_id=ticket.ticket_id,
    )
    db.session.add(conversation)
    db.session.flush()
    ticket.routing_method = "ai"
    ticket.status = "Waiting for Employee"
    ticket.conversation_id = conversation.id
    ticket.ai_recommendation = "Restart the app and sign in again."
    db.session.add(Message(
        conversation_id=conversation.id,
        role="assistant",
        content="Try restarting the app. Is your issue resolved?",
    ))
    db.session.commit()
    return ticket.ticket_id, conversation.id


def test_employee_confirmation_resolves_ai_ticket(client, app):
    with app.app_context():
        ticket_id, conversation_id = _create_ai_ticket_with_confirmation_prompt()
    login(client, "employee@test.com", "Employee@123")

    follow_up = client.post(
        "/api/chat/message",
        json={
            "message": "Yes, it's fixed",
            "conversation_id": conversation_id,
        },
    )
    assert follow_up.status_code == 200
    assert "now resolved" in follow_up.get_json()["reply"]
    with app.app_context():
        ticket = db.session.execute(
            select(Ticket).where(Ticket.ticket_id == ticket_id)
        ).scalar_one()
        assert ticket.routing_method == "ai"
        assert ticket.status == "Resolved"
        assert ticket.resolution_method == "AI"
        assert ticket.employee_confirmed is True


def test_employee_rejection_routes_ai_ticket_to_manual_queue(client, app):
    with app.app_context():
        ticket_id, conversation_id = _create_ai_ticket_with_confirmation_prompt()
    login(client, "employee@test.com", "Employee@123")
    follow_up = client.post(
        "/api/chat/message",
        json={"message": "No, still having issues", "conversation_id": conversation_id},
    )
    assert follow_up.status_code == 200
    assert "human IT support" in follow_up.get_json()["reply"]
    with app.app_context():
        ticket = db.session.execute(
            select(Ticket).where(Ticket.ticket_id == ticket_id)
        ).scalar_one()
        assert ticket.routing_method == "manual"
        assert ticket.status == "Open"


def test_admin_dashboard_displays_explicit_clearing_options(client):
    login(client, "admin@test.com", "Admin@123")
    response = client.get("/admin/dashboard")
    assert response.status_code == 200
    assert b"AI Ticket Clearing" in response.data
    assert b"Manual Ticket Clearing" in response.data


def test_non_admin_cannot_assign_ticket(client, app):
    with app.app_context():
        employee = db.session.execute(
            select(User).where(User.email == "employee@test.com")
        ).scalar_one()
        engineer = db.session.execute(
            select(User).where(User.role == "engineer")
        ).scalars().first()
        ticket = TicketService.create_ticket(
            employee.id, "Ticket requiring authorization test assignment."
        )
        ticket_id = ticket.ticket_id
        engineer_id = engineer.id

    login(client, "employee@test.com", "Employee@123")
    response = client.post(
        f"/admin/tickets/{ticket_id}/assign",
        data={"engineer_id": engineer_id, "csrf_token": "test"},
    )
    assert response.status_code in (302, 403)
    with app.app_context():
        persisted = db.session.execute(
            select(Ticket).where(Ticket.ticket_id == ticket_id)
        ).scalar_one()
        assert persisted.assigned_engineer_id != engineer_id


def test_engineer_cannot_claim_another_engineers_ticket(app):
    with app.app_context():
        employee = db.session.execute(
            select(User).where(User.email == "employee@test.com")
        ).scalar_one()
        engineers = db.session.execute(
            select(User).where(User.role == "engineer").order_by(User.id)
        ).scalars().all()
        ticket = TicketService.create_ticket(
            employee.id, "Ticket assigned to one engineer only."
        )
        TicketService.assign_engineer(ticket.ticket_id, engineers[0].id, engineers[0].id)
        with pytest.raises(ValueError, match="already assigned"):
            TicketService.assign_engineer(ticket.ticket_id, engineers[1].id, engineers[1].id)
        assert ticket.assigned_engineer_id == engineers[0].id


def test_unassigned_engineer_cannot_post_ticket_updates(client, app):
    with app.app_context():
        employee = db.session.execute(
            select(User).where(User.email == "employee@test.com")
        ).scalar_one()
        ticket = TicketService.create_ticket(
            employee.id, "Ticket requiring assigned engineer only updates."
        )
        ticket_id = ticket.ticket_id

    login(client, "engineer@test.com", "Engineer@123")
    response = client.post(
        f"/engineer/tickets/{ticket_id}/note",
        data={"note": "Unauthorized note", "csrf_token": "test"},
    )
    assert response.status_code == 403


def test_unconfirmed_ai_clear_request_does_not_resolve_ticket(client, app):
    with app.app_context():
        employee = db.session.execute(
            select(User).where(User.email == "employee@test.com")
        ).scalar_one()
        ticket = TicketService.create_ticket(
            employee.id, "Please help resolve this test application problem."
        )
        ticket_id = ticket.ticket_id

    login(client, "employee@test.com", "Employee@123")
    response = client.post(
        "/api/chat/message",
        json={"message": "clear it", "active_ticket_id": ticket_id},
    )
    assert response.status_code == 200
    assert response.get_json()["ask_confirmation"] is False
    with app.app_context():
        ticket = db.session.execute(
            select(Ticket).where(Ticket.ticket_id == ticket_id)
        ).scalar_one()
        assert ticket.status != "Resolved"


def test_concurrent_ticket_creation_serializes_ids_and_routing(app):
    with app.app_context():
        if db.engine.dialect.name != "postgresql":
            pytest.skip("Run with TEST_DATABASE_URL set to PostgreSQL to test concurrent locking.")
        app.config["AI_ROUTING_THRESHOLD"] = 5
        app.config["AI_ROUTING_WINDOW_MINUTES"] = 60
        employee_id = _employee_ids()[0]

    def create_concurrently(index):
        with app.app_context():
            ticket = TicketService.create_ticket(
                employee_id,
                f"Concurrent ticket creation test request number {index}.",
            )
            return ticket.ticket_id, ticket.routing_method

    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(create_concurrently, range(6)))

    ticket_ids = [ticket_id for ticket_id, _ in results]
    routing_methods = [method for _, method in results]
    assert len(ticket_ids) == len(set(ticket_ids))
    assert routing_methods.count("manual") == 4
    assert routing_methods.count("ai") == 2
