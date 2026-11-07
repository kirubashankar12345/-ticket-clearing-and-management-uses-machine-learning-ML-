from flask import (
    Blueprint, render_template, redirect, url_for,
    flash, request, abort, current_app
)
from flask_login import login_user, logout_user, login_required, current_user
from flask_wtf import FlaskForm
from urllib.parse import urlparse
from datetime import datetime, timedelta
from sqlalchemy import select, func, delete
from models import db
from models.login_attempt import LoginAttempt
from wtforms import StringField, PasswordField, SelectField
from wtforms.validators import DataRequired, Email, Length
from services.auth_service import AuthService
from services.exceptions import EmailAlreadyExistsError

auth_bp = Blueprint("auth", __name__)

_LOGIN_WINDOW_SECONDS = 15 * 60
_LOGIN_MAX_FAILURES = 5


def _login_limited(ip_address):
    cutoff = datetime.utcnow() - timedelta(seconds=_LOGIN_WINDOW_SECONDS)
    try:
        db.session.execute(delete(LoginAttempt).where(LoginAttempt.created_at < cutoff))
        count = db.session.execute(
            select(func.count(LoginAttempt.id)).where(
                LoginAttempt.ip_address == ip_address,
                LoginAttempt.created_at >= cutoff,
            )
        ).scalar() or 0
        db.session.commit()
        return count >= _LOGIN_MAX_FAILURES
    except Exception:
        # A throttling store failure must not take the login page down.
        db.session.rollback()
        current_app.logger.exception("Login attempt store unavailable")
        return False


def _record_login_failure(ip_address):
    try:
        db.session.add(LoginAttempt(ip_address=ip_address))
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Could not record failed login attempt")


class LoginForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email()])
    password = PasswordField("Password", validators=[DataRequired()])


class RegisterForm(FlaskForm):
    name = StringField("Full Name", validators=[DataRequired(), Length(2, 120)])
    email = StringField("Email", validators=[DataRequired(), Email()])
    password = PasswordField("Password", validators=[DataRequired(), Length(8, 128)])
    department = StringField("Department", validators=[Length(0, 120)])


ROLE_REDIRECTS = {
    "admin": "admin.dashboard",
    "engineer": "engineer.dashboard",
    "employee": "employee.raise_ticket",
}


@auth_bp.route("/user-login", methods=["GET", "POST"])
def user_login():
    """User/Employee login portal — directs the employee straight to raise a ticket."""
    if request.method == "POST":
        form = LoginForm()
        client_ip = request.remote_addr or "unknown"
        if _login_limited(client_ip):
            flash("Too many failed login attempts. Please try again in 15 minutes.", "danger")
            return redirect(url_for("auth.login", portal="user")), 429

        if form.validate_on_submit():
            user = AuthService.authenticate(form.email.data, form.password.data)
            if user:
                try:
                    db.session.execute(
                        delete(LoginAttempt).where(LoginAttempt.ip_address == client_ip)
                    )
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                    current_app.logger.exception("Could not clear login attempts")

                # Switch user cleanly
                if current_user.is_authenticated:
                    logout_user()
                login_user(user)

                next_page = request.args.get("next")
                parsed_next = urlparse(next_page or "")
                if (next_page and next_page.startswith("/")
                        and not next_page.startswith("//") and "\\" not in next_page
                        and not parsed_next.netloc and parsed_next.scheme == ""):
                    return redirect(next_page)

                if user.role == "employee":
                    flash(f"Welcome back, {user.name.split()[0]}! You can now raise your support ticket below.", "success")
                    return redirect(url_for("employee.raise_ticket"))
                elif user.role == "admin":
                    return redirect(url_for("admin.dashboard"))
                else:
                    return redirect(url_for(ROLE_REDIRECTS.get(user.role, "main.landing")))
            else:
                _record_login_failure(client_ip)
                flash("Invalid work email or password. Please try again.", "danger")
                return redirect(url_for("auth.login", portal="user", next=request.args.get("next")))

        flash("Please enter a valid work email and password.", "danger")
        return redirect(url_for("auth.login", portal="user", next=request.args.get("next")))

    # GET request
    if current_user.is_authenticated and current_user.role == "employee":
        return redirect(url_for("employee.raise_ticket"))
    return redirect(url_for("auth.login", portal="user", next=request.args.get("next")))


@auth_bp.route("/admin-login", methods=["GET", "POST"])
def admin_login():
    """Admin login portal — directs the admin to Admin Overview with AI & Manual bars."""
    if request.method == "POST":
        form = LoginForm()
        client_ip = request.remote_addr or "unknown"
        if _login_limited(client_ip):
            flash("Too many failed login attempts. Please try again in 15 minutes.", "danger")
            return redirect(url_for("auth.login", portal="admin")), 429

        if form.validate_on_submit():
            user = AuthService.authenticate(form.email.data, form.password.data)
            if user:
                if user.role != "admin":
                    flash("This account does not have Admin access. Please use the User Login side.", "warning")
                    return redirect(url_for("auth.login", portal="user"))

                try:
                    db.session.execute(
                        delete(LoginAttempt).where(LoginAttempt.ip_address == client_ip)
                    )
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                    current_app.logger.exception("Could not clear login attempts")

                # Switch user cleanly
                if current_user.is_authenticated:
                    logout_user()
                login_user(user)

                next_page = request.args.get("next")
                parsed_next = urlparse(next_page or "")
                if (next_page and next_page.startswith("/")
                        and not next_page.startswith("//") and "\\" not in next_page
                        and not parsed_next.netloc and parsed_next.scheme == ""):
                    return redirect(next_page)

                flash("Signed in to Admin Dashboard.", "success")
                return redirect(url_for("admin.dashboard"))
            else:
                _record_login_failure(client_ip)
                flash("Invalid admin credentials. Please verify your email and password.", "danger")
                return redirect(url_for("auth.login", portal="admin", next=request.args.get("next")))

        flash("Please enter a valid admin email and password.", "danger")
        return redirect(url_for("auth.login", portal="admin", next=request.args.get("next")))

    # GET request
    if current_user.is_authenticated and current_user.role == "admin":
        return redirect(url_for("admin.dashboard"))
    return redirect(url_for("auth.login", portal="admin", next=request.args.get("next")))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """Single page with user login on one side and admin login on the other side."""
    if request.method == "POST":
        login_type = request.form.get("login_type", "user")
        if login_type == "admin":
            return admin_login()
        else:
            return user_login()

    portal = request.args.get("portal") or request.args.get("role") or "user"
    user_form = LoginForm()
    admin_form = LoginForm()
    return render_template("auth/unified_login.html", user_form=user_form, admin_form=admin_form, portal=portal)


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("employee.raise_ticket"))

    form = RegisterForm()
    if form.validate_on_submit():
        try:
            user = AuthService.register_employee(
                name=form.name.data,
                email=form.email.data,
                password=form.password.data,
                department=form.department.data or None,
            )
            login_user(user)
            flash("Account created successfully! You can now raise your support ticket.", "success")
            return redirect(url_for("employee.raise_ticket"))
        except EmailAlreadyExistsError:
            flash("An account with this email already exists.", "danger")
            return render_template("auth/register.html", form=form), 400
        except ValueError as exc:
            flash(str(exc), "danger")
            return render_template("auth/register.html", form=form), 400

    return render_template("auth/register.html", form=form)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("main.landing"))
