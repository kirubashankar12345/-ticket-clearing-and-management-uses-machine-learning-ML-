from functools import wraps
from flask import abort
from flask_login import login_required, current_user


def require_role(*roles):
    """Decorator that enforces role-based access control."""
    def decorator(f):
        @wraps(f)
        @login_required
        def wrapper(*args, **kwargs):
            if current_user.role not in roles:
                abort(403)
            return f(*args, **kwargs)
        return wrapper
    return decorator


def register_blueprints(app):
    from routes.auth import auth_bp
    from routes.employee import employee_bp
    from routes.admin import admin_bp
    from routes.engineer import engineer_bp
    from routes.api import api_bp
    from routes.knowledge_base import kb_bp
    from routes.main import main_bp

    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(employee_bp, url_prefix="/employee")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(engineer_bp, url_prefix="/engineer")
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(kb_bp, url_prefix="/kb")
    app.register_blueprint(main_bp)
