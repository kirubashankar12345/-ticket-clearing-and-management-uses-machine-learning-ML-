# ResolveAI — AI-Powered Software IT Support

ResolveAI is a full-stack Flask application that helps employees troubleshoot
software issues through an AI chatbot, automatically creates support tickets,
and lets employees track tickets and confirm when AI troubleshooting resolves an issue.

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

Copy the example environment file, then replace `YOUR_PASSWORD` in
`DATABASE_URL` with your PostgreSQL password. The PostgreSQL server must be
running and the `room_status_db` database must already exist. If your password
contains URL-reserved characters, percent-encode them in the connection URL.
You can also configure a custom secret key or connect an OpenAI-compatible API.
ResolveAI works without an API key by using its built-in troubleshooting
assistant.

```powershell
Copy-Item .env.example .env
```

Keep `.env` local; do not commit database credentials. New tickets are routed
automatically using the rolling ticket count across all employees:
`AI_ROUTING_THRESHOLD=5` and `AI_ROUTING_WINDOW_MINUTES=60` route the fifth and
subsequent recent tickets to AI; below that threshold, tickets go to manual
support. Both queues remain visible on the admin dashboard. AI troubleshooting
must be confirmed by the employee before a ticket is resolved; AI failures,
critical security incidents, and production outages go to human support.

### 3. Initialise or safely update the database

```bash
python init_db.py
```
The initializer applies additive schema updates to existing databases and
preserves ticket rows. It adds `routing_method`, `routed_at`, and
`routing_reason` when absent and synchronizes the ticket-ID counter; it does
not drop or reset tables. It also creates the AI-clearing job and per-ticket
progress tables. Run it once after updating the application. Keep the Flask app
running while a bulk AI-clearing job is in progress because its worker runs in
the application process.

### 4. Run the application

```bash
python app.py
```

Then open http://localhost:5000 in your browser.

## Test Accounts

| Role     | Email                        | Password      |
|----------|------------------------------|---------------|
| Admin    | admin@resolveai.com          | Admin@123     |
| Engineer | engineer1@resolveai.com      | Engineer@123  |
| Employee | john.doe@company.com         | Employee@123  |

## Run Tests

```bash
python -m pytest tests/ -v
```

## Windows and VS Code setup

1. Install Python 3.11+ and PostgreSQL, then create the `room_status_db`
   database (for example, in pgAdmin or with `createdb room_status_db`).
2. Open this project folder in VS Code and open **Terminal → New Terminal**.
3. Create and activate a virtual environment:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

4. Install the pinned requirements and configure a local `.env`:

   ```powershell
   python -m pip install -r requirements.txt
   Copy-Item .env.example .env
   ```

   Edit `DATABASE_URL` in `.env` with the PostgreSQL username, password, host,
   and database. Keep the real password out of source control and logs.
5. Create or migrate the schema and seed a new database:

   ```powershell
   python init_db.py
   ```

6. Start ResolveAI and open the local page:

   ```powershell
   python app.py
   ```

   Browse to `http://localhost:5000`. To make it reachable from a phone on
   your trusted local network, use:

   ```powershell
   python -m flask --app app run --host 0.0.0.0 --port 5000
   ```

   Then open the computer's LAN IP address on the phone.
7. The admin dashboard shows both **AI Ticket Clearing** and
   **Manual Ticket Clearing**, and displays the automatically active mode,
   recent ticket count, and threshold. Tickets are routed according to the
   configured rolling window; existing tickets stay in their original queue.
   AI-routed tickets are processed automatically in the background, and their
   progress appears on the dashboard. A ticket remains unresolved until its
   employee confirms the AI troubleshooting worked. Run
   automated tests with `python -m pytest tests/ -v`.

   PostgreSQL concurrency coverage is opt-in and requires a separate test
   database named `resolveai_test` (the pytest fixture recreates only that test
   database's tables):

   ```powershell
   createdb -U postgres resolveai_test
   $env:TEST_DATABASE_URL = "postgresql+psycopg://postgres:YOUR_PASSWORD@localhost:5432/resolveai_test"
   python -m pytest tests/test_ticket_routing.py -v
   Remove-Item Env:TEST_DATABASE_URL
   ```

   Never point `TEST_DATABASE_URL` at the application or production database.

## Project Structure

```
resolveai/
  app.py              # Flask app factory
  config.py           # Dev/Prod/Test config
  init_db.py          # DB init + seed
  models/             # SQLAlchemy models
  services/           # Business logic layer
  routes/             # Flask blueprints
  templates/          # Jinja2 templates
  static/             # CSS + JS
  tests/              # pytest suite
```

## Key Design Decisions

- **Admin-selected ticket clearing**: New requests go to manual support. An administrator can explicitly start AI Ticket Clearing to process every eligible open, unassigned ticket in bounded batches in a background worker. AI troubleshooting, recommendations, conversation history, and per-ticket outcomes are persisted. An AI answer only resolves a ticket after the employee confirms it; unavailable or unsuccessful AI troubleshooting transfers the existing ticket to human support.
- **Durable clearing progress**: Background jobs and per-ticket outcomes are saved in PostgreSQL and polled by the dashboard. A database advisory lock prevents concurrent admin clicks from starting duplicate bulk jobs.
- **Race-safe ticket IDs**: Ticket creation locks the `TicketCounter` row to preserve unique IT-XXXXX IDs under concurrent requests.
- **AI fallback**: When no `OPENAI_API_KEY` is set, the `FallbackChatbot` provides multi-step troubleshooting for all 9 software categories.
- **AI outage escalation**: If an external AI request fails, the employee's conversation is retained and an unassigned manual-support ticket is created; critical security incidents and production outages are escalated immediately.
- **Resolution history**: Confirmed AI resolution is recorded as `resolution_method=AI`; manual admin/engineer resolution is recorded as `resolution_method=Manual`. Employees can reopen resolved tickets within the existing 7-day window.
- **Chat and ticket flow**: AI-cleared tickets are linked to employee conversations. AI failures or unsuccessful troubleshooting preserve the conversation and ticket ID while routing the ticket to the manual queue.
- **SQLAlchemy 2.0 style**: All queries use `db.session.execute(select(...))` — no legacy `Model.query.get()`.
