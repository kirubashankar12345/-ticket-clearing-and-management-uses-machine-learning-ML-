import pytest
from datetime import datetime, timedelta
from app import create_app
from models import db as _db
from models.user import User
from models.ticket import Ticket, TicketCounter, TicketActivity, generate_ticket_id


@pytest.fixture(scope="session")
def app():
    """Create a test app using SQLite or the explicitly named test PostgreSQL DB."""
    application = create_app("testing")
    with application.app_context():
        _db.drop_all()
        _db.create_all()
        _seed_test_data()

    yield application

    with application.app_context():
        _db.drop_all()


def _seed_test_data():
    """Seed minimal data needed by all tests."""
    # TicketCounter row
    counter = TicketCounter(id=1, last_ticket_number=0)
    _db.session.add(counter)
    _db.session.flush()

    # Admin
    admin = User(name="Test Admin", email="admin@test.com", role="admin", department="IT")
    admin.set_password("Admin@123")
    _db.session.add(admin)

    # Engineer
    engineer = User(name="Test Engineer", email="engineer@test.com", role="engineer", department="IT")
    engineer.set_password("Engineer@123")
    _db.session.add(engineer)

    # Employee
    employee = User(name="Test Employee", email="employee@test.com", role="employee", department="Engineering")
    employee.set_password("Employee@123")
    _db.session.add(employee)

    # Second employee (for cross-access test)
    employee2 = User(name="Other Employee", email="other@test.com", role="employee", department="Marketing")
    employee2.set_password("Employee@123")
    _db.session.add(employee2)

    _db.session.flush()

    # One test ticket belonging to employee
    str_id = generate_ticket_id(_db.session)
    ticket = Ticket(
        ticket_id=str_id,
        employee_id=employee.id,
        employee_name=employee.name,
        employee_email=employee.email,
        issue_description="Test issue: cannot log into the application.",
        category="login",
        priority="Normal",
        status="Open",
        employee_confirmed=False,
    )
    _db.session.add(ticket)
    _db.session.flush()

    activity = TicketActivity(
        ticket_id=ticket.id,
        changed_by=str(employee.id),
        old_status=None,
        new_status="Open",
        note="Ticket created",
    )
    _db.session.add(activity)
    _db.session.commit()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def isolate_ticket_creation_times(app):
    """Keep ticket-age-dependent tests independent from prior test records."""
    with app.app_context():
        for ticket in _db.session.query(Ticket).all():
            ticket.created_at = datetime.utcnow() - timedelta(hours=2)
        _db.session.commit()
    yield
    with app.app_context():
        for ticket in _db.session.query(Ticket).all():
            ticket.created_at = datetime.utcnow() - timedelta(hours=2)
        _db.session.commit()


@pytest.fixture()
def runner(app):
    return app.test_cli_runner()


def login(client, email, password):
    return client.post("/auth/login", data={"email": email, "password": password},
                       follow_redirects=True)


def get_user_by_email(app, email):
    from sqlalchemy import select
    with app.app_context():
        return _db.session.execute(
            select(User).where(User.email == email)
        ).scalar_one_or_none()


def get_ticket_by_str_id(app, ticket_id):
    from sqlalchemy import select
    with app.app_context():
        return _db.session.execute(
            select(Ticket).where(Ticket.ticket_id == ticket_id)
        ).scalar_one_or_none()
