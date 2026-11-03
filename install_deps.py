"""Install dependencies only."""
import subprocess, sys
result = subprocess.run(
    [sys.executable, "-m", "pip", "install",
     "Flask==3.0.3", "Flask-SQLAlchemy==3.1.1", "Flask-Login==0.6.3",
     "Flask-WTF==1.2.1", "WTForms==3.1.2", "Werkzeug==3.0.3",
     "SQLAlchemy==2.0.31", "python-dotenv==1.0.1", "requests==2.32.3",
     "pytest==8.3.2", "pytest-flask==1.3.0", "email-validator==2.2.0",
     "itsdangerous==2.2.0", "click==8.1.7"],
    capture_output=False, text=True
)
sys.exit(result.returncode)
