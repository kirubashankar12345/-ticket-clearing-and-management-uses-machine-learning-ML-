from app import create_app
from models import db

app = create_app()

with app.app_context():
    try:
        db.create_all()
        from init_db import seed_data
        seed_data(app)
    except Exception as e:
        print(f"Database initialization note: {e}")

if __name__ == "__main__":
    app.run()
