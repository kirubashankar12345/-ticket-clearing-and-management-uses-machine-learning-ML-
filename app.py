import os
from flask import Flask, render_template
from flask_login import LoginManager, current_user
from flask_wtf.csrf import CSRFProtect
from config import config_map
from models import db


login_manager = LoginManager()
csrf = CSRFProtect()


def create_app(config_name: str = None) -> Flask:
    if config_name is None:
        config_name = os.environ.get("FLASK_ENV", "development")

    app = Flask(__name__, instance_relative_config=True)

    # Load config
    cfg_class = config_map.get(config_name, config_map["development"])
    if callable(cfg_class) and not isinstance(cfg_class, type):
        app.config.from_object(cfg_class())
    else:
        app.config.from_object(cfg_class)

    # Ensure instance folder exists
    os.makedirs(app.instance_path, exist_ok=True)

    # Initialize extensions
    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please log in to access this page."
    login_manager.login_message_category = "warning"

    @login_manager.user_loader
    def load_user(user_id):
        from services.auth_service import AuthService
        return AuthService.load_user(user_id)

    # Context processor: unread notification count
    @app.context_processor
    def inject_notifications():
        if current_user.is_authenticated:
            from services.notification_service import NotificationService
            count = NotificationService.get_unread_count(current_user.id)
            return {"unread_notifications": count}
        return {"unread_notifications": 0}

    # Register blueprints
    from routes import register_blueprints
    register_blueprints(app)

    # Error handlers
    @app.errorhandler(403)
    def forbidden(e):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def internal_error(e):
        db.session.rollback()
        return render_template("errors/500.html"), 500

    return app


# Module-level application instance so 'gunicorn app:app' works out-of-the-box
app = create_app()

with app.app_context():
    try:
        db.create_all()
        from init_db import seed_data
        seed_data(app)
    except Exception as e:
        print(f"Database setup note: {e}")

if __name__ == "__main__":
    app.run(debug=app.config.get("DEBUG", False))
