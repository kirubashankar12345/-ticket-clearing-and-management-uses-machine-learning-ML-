from datetime import datetime, timedelta
from sqlalchemy import select

from models import db
from models.conversation import Conversation, Message
from models.ticket import Ticket
from models.user import User
from tests.conftest import login


def test_chat_page_explains_automatic_clearing_mode(client, app):
    with app.app_context():
        tickets = db.session.execute(select(Ticket)).scalars().all()
        original_dates = [(ticket.id, ticket.created_at) for ticket in tickets]
        for ticket in tickets:
            ticket.created_at = datetime.utcnow() - timedelta(hours=2)
        db.session.commit()

    try:
        login(client, "other@test.com", "Employee@123")
        response = client.get("/employee/chat")

        assert response.status_code == 200
        assert b"Manual support" in response.data
        assert b"AI Troubleshooting" in response.data
        assert b"Manual Support" in response.data
        assert b"High request volume" not in response.data
        assert b"Routing is selected automatically" in response.data
    finally:
        with app.app_context():
            for ticket_id, created_at in original_dates:
                ticket = db.session.get(Ticket, ticket_id)
                if ticket:
                    ticket.created_at = created_at
            db.session.commit()


def test_manual_chat_ticket_cannot_be_resolved_by_employee_confirmation(client, app):
    login(client, "employee@test.com", "Employee@123")
    first_response = client.post(
        "/api/chat/message",
        json={"message": "I cannot login to my work account"},
    )
    assert first_response.status_code == 200
    first_data = first_response.get_json()
    assert first_data["ticket_id"]
    assert first_data["ask_confirmation"] is False
    with app.app_context():
        ticket = db.session.execute(
            select(Ticket).where(Ticket.ticket_id == first_data["ticket_id"])
        ).scalar_one()
        assert ticket.routing_method == "manual"
        assert ticket.status == "Open"

    prompt = client.post(
        "/api/chat/message",
        json={
            "message": "clear it",
            "conversation_id": first_data["conversation_id"],
        },
    )
    assert prompt.status_code == 200
    prompt_data = prompt.get_json()
    assert prompt_data["ask_confirmation"] is False
    assert "manual support queue" in prompt_data["reply"]

    with app.app_context():
        ticket = db.session.execute(
            select(Ticket).where(Ticket.ticket_id == first_data["ticket_id"])
        ).scalar_one()
        assert ticket.status == "Open"
        assert ticket.resolution_method is None


def test_ticket_created_from_chat_is_linked_to_conversation(client, app):
    login(client, "employee@test.com", "Employee@123")
    chat_response = client.post(
        "/api/chat/message",
        json={"message": "The work application keeps crashing on startup"},
    )
    assert chat_response.status_code == 200
    conversation_id = chat_response.get_json()["conversation_id"]

    response = client.post(
        "/api/chat/create-ticket",
        json={
            "conversation_id": conversation_id,
            "issue_description": "The work application keeps crashing on startup",
            "category": "crashes",
            "priority": "High",
        },
    )
    assert response.status_code == 200
    ticket_id = response.get_json()["ticket_id"]

    with app.app_context():
        ticket = db.session.execute(
            select(Ticket).where(Ticket.ticket_id == ticket_id)
        ).scalar_one()
        conversation = db.session.get(Conversation, conversation_id)
        assert ticket.conversation_id == conversation_id
        assert conversation.active_ticket_id == ticket_id


def test_manual_support_creates_ticket_without_ai_conversation(client, app):
    login(client, "employee@test.com", "Employee@123")

    response = client.post(
        "/api/chat/create-ticket",
        json={
            "support_mode": "manual",
            "issue_description": "I need help accessing the finance application.",
            "category": "permissions",
            "priority": "High",
        },
    )

    assert response.status_code == 200
    ticket_id = response.get_json()["ticket_id"]
    with app.app_context():
        ticket = db.session.execute(
            select(Ticket).where(Ticket.ticket_id == ticket_id)
        ).scalar_one()
        assert ticket.status == "Open"
        assert ticket.category == "permissions"
        assert ticket.priority == "High"
        assert ticket.conversation_id is None
        assert ticket.ai_recommendation is None


def test_ticket_volume_switches_intake_to_ai_automatically(
    client, app, monkeypatch
):
    from services.ticket_service import TicketService
    from services.ai_clearing_service import AiClearingService

    auto_jobs = []
    with app.app_context():
        app.config["AI_ROUTING_THRESHOLD"] = 5
        app.config["AI_ROUTING_WINDOW_MINUTES"] = 60
        tickets = db.session.execute(select(Ticket)).scalars().all()
        original_dates = [(ticket.id, ticket.created_at) for ticket in tickets]
        for ticket in tickets:
            ticket.created_at = datetime.utcnow() - timedelta(hours=2)
        employee_id = db.session.execute(
            select(User).where(User.email == "other@test.com")
        ).scalar_one().id
        db.session.commit()

    login(client, "other@test.com", "Employee@123")
    created_ticket_ids = []
    try:
        for index in range(4):
            ticket = TicketService.create_ticket(
                employee_id=employee_id,
                issue_description=f"Recent support request number {index}.",
            )
            created_ticket_ids.append(ticket.id)
            assert ticket.routing_method == "manual"

        response = client.get("/employee/chat")
        assert response.status_code == 200
        assert b"AI Troubleshooting" in response.data
        assert b"Manual Support" in response.data
        assert b"Manual Support Mode" in response.data

        def record_automatic_job(cls, requesting_employee_id, ticket_id):
            auto_jobs.append((requesting_employee_id, ticket_id))
            return None, False

        monkeypatch.setattr(
            AiClearingService,
            "start_ticket_job",
            classmethod(record_automatic_job),
        )

        manual_response = client.post(
            "/api/chat/create-ticket",
            json={
                "support_mode": "manual",
                "issue_description": "A request submitted from a stale manual form.",
            },
        )
        assert manual_response.status_code == 200
        ticket_id = manual_response.get_json()["ticket_id"]
        with app.app_context():
            created = db.session.execute(
                select(Ticket).where(Ticket.ticket_id == ticket_id)
            ).scalar_one()
            assert created.routing_method == "ai"
            assert manual_response.get_json()["current_mode"] == "ai"
            assert auto_jobs == [(employee_id, created.id)]
            created_ticket_ids.append(created.id)
    finally:
        with app.app_context():
            for ticket_id in created_ticket_ids:
                ticket = db.session.get(Ticket, ticket_id)
                if ticket:
                    db.session.delete(ticket)
            for ticket_id, created_at in original_dates:
                ticket = db.session.get(Ticket, ticket_id)
                if ticket:
                    ticket.created_at = created_at
            db.session.commit()
