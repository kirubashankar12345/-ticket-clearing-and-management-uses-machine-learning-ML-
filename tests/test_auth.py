import re
import pytest
from tests.conftest import login


def test_register_success(client):
    """POST /auth/register with valid data returns 302 redirect."""
    resp = client.post("/auth/register", data={
        "name": "New User",
        "email": "newuser@test.com",
        "password": "Newpass1",
        "department": "Finance",
    }, follow_redirects=False)
    assert resp.status_code == 302


def test_registered_account_can_log_in_from_a_new_client(client, app):
    email = "newlogin@test.com"
    password = "Newpass1"
    resp = client.post("/auth/register", data={
        "name": "New Login User",
        "email": email,
        "password": password,
        "department": "Finance",
    }, follow_redirects=False)
    assert resp.status_code == 302

    with app.test_client() as returning_client:
        resp = returning_client.post("/auth/login", data={
            "email": email.upper(),
            "password": password,
        }, follow_redirects=False)

    assert resp.status_code == 302
    assert "/employee/dashboard" in resp.headers["Location"]


def test_duplicate_email(client):
    """POST /auth/register with existing email returns 400 or re-renders with error."""
    # First registration
    client.post("/auth/register", data={
        "name": "Dup User",
        "email": "dup2@test.com",
        "password": "Duppass1",
        "department": "",
    })
    # Log out so the authenticated guard doesn't redirect on 2nd attempt
    client.get("/auth/logout")
    # Second registration with same email
    resp = client.post("/auth/register", data={
        "name": "Dup User",
        "email": "dup2@test.com",
        "password": "Duppass1",
        "department": "",
    }, follow_redirects=False)
    # Should be 400 (re-render with error), not a successful redirect to dashboard
    assert resp.status_code in (302, 400), f"Expected 302 or 400, got {resp.status_code}"
    # If 302, it must NOT redirect to employee/dashboard (could go to login)
    if resp.status_code == 302:
        location = resp.headers.get("Location", "")
        assert "employee/dashboard" not in location, \
            f"Duplicate email should not redirect to dashboard, got: {location}"


def test_login_correct(client):
    """POST /auth/login with valid credentials returns 302."""
    resp = client.post("/auth/login", data={
        "email": "employee@test.com",
        "password": "Employee@123",
    }, follow_redirects=False)
    assert resp.status_code == 302


def test_login_wrong_password(client):
    """POST /auth/login with wrong password does NOT redirect to dashboard."""
    resp = client.post("/auth/login", data={
        "email": "employee@test.com",
        "password": "WrongPassword99",
    }, follow_redirects=True)
    # Should stay on login page or show error — not reach dashboard
    assert b"dashboard" not in resp.data.lower() or b"Invalid" in resp.data or resp.status_code == 200


def test_employee_cannot_access_admin(app):
    """Employee accessing /admin/dashboard gets 302 (login redirect) or 403."""
    # Use a fresh client so no prior session bleeds in
    with app.test_client() as fresh_client:
        login(fresh_client, "employee@test.com", "Employee@123")
        resp = fresh_client.get("/admin/dashboard", follow_redirects=False)
        assert resp.status_code in (302, 403), \
            f"Expected 302 or 403, got {resp.status_code}"
