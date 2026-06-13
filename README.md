# BankChat — AI-Powered Banking Assistant

An intelligent banking chatbot built with **Angular**, **Django 6**, **LangGraph** multi-agent orchestration, **Keycloak** authentication, dedicated microservices for fraud detection, natural-language SQL queries, and email alerting, all behind an **Nginx API Gateway**.

---

## Architecture

```
                          ┌──────────────────────────────────────────┐
                          │          NGINX API GATEWAY (:80)         │
                          │                                          │
  Browser ──Bearer JWT──▶ │  /api/   → Django Orchestrateur (:8000) │
                          │  /fraud/ → Fraud Service      (:8001)   │
                          │  /mail/  → Mail Service       (:8002)   │
                          │  /sql/   → Text-to-SQL Service(:8003)   │
                          │  /auth/  → Keycloak           (:8080)   │
                          │  /       → Angular SPA        (:4200)   │
                          └──────────────────────────────────────────┘
```

### Services overview

| Service | Container | Port | Role |
|---------|-----------|------|------|
| Angular (frontend) | `bank_chat_frontend` | `4200` | SPA UI |
| Nginx (API Gateway) | `bank_chat_gateway` | `80` | Single entry point, reverse-proxy |
| Django (orchestrateur) | `bank_chat_orchestrateur` | `8000` | LangGraph, chat API, auth |
| Fraud Service | `bank_chat_fraud` | `8001` | Fraud analysis (FastAPI + LangGraph) |
| Mail Service | `bank_chat_mail` | `8002` | SMTP emails + audit log (FastAPI) |
| Text-to-SQL Service | `bank_chat_text2sql` | `8003` | NL → SQL → results (FastAPI) |
| Keycloak | `bank_chat_keycloak` | `8080` | OAuth2 / OIDC authentication |
| PostgreSQL | `bank_chat_db` | `5432` | Primary data store (4 databases) |
| Redis | `bank_chat_redis` | `6379` | Session summary cache (TTL 1 h) |

### PostgreSQL databases

| Database | User | Used by |
|----------|------|---------|
| `keycloak_db` | `keycloak_user` | Keycloak |
| `bank_orchestrateur` | `orchestrateur_user` | Django chatbot (conversations, messages) |
| `banking_data` | `fraud_user` / `sql_user` | Fraud service + Text-to-SQL (transactions, fraud_rules, fraud_decision_logs) |
| `mail_db` | `mail_user` | Mail service (sent_emails audit table) |

---

## LangGraph Multi-Agent Orchestration

```
User message
     │
     ▼
┌───────────────────┐
│  Detect Intent    │  (LLM classifies the request)
│  (Django / graph) │
└────────┬──────────┘
         │
  ┌──────┴──────────────────────────────────┐
  ▼         ▼          ▼         ▼          ▼
Account  Transfer  Support   Fraud     SQL Agent   Fallback
 Agent    Agent    Agent     Agent     (bank_agent  Agent
  │         │        │         │       / admin only)  │
  ▼         ▼        ▼         │                     ▼
 LLM       LLM      LLM        │                    LLM
                               ▼
                   ┌─────────────────────────┐
                   │  Fraud Service (:8001)   │
                   │  13 rules + scoring      │
                   │  TRACFIN report          │
                   │  Excel export            │
                   └───────────┬─────────────┘
                               │
                               ▼
                   Mail Service (:8002) — sends fraud alert email
```

### Role-gated agents

| Agent | Required role |
|-------|--------------|
| `fraud` | `bank_agent` or `admin` |
| `sql` | `bank_agent` or `admin` |
| All others | Any authenticated user |

---

## Text-to-SQL Service

Converts natural language banking questions into validated SQL, executes them on the `banking_data` database, and returns business-readable results.

**Pipeline:** NL question → LLM generates SQL → security validation → PostgreSQL execution → formatted response

**Accessible tables:** `transactions`, `fraud_rules`, `fraud_decision_logs`

**Security:**
- SELECT-only: DML (INSERT/UPDATE/DELETE/DROP) blocked
- System tables (`pg_*`, `information_schema`) blocked
- Dangerous functions blocked
- JWT required — `bank_agent` or `admin` role only

**Endpoints (via gateway `/sql/`):**

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/sql/query` | NL question → SQL → results |
| `GET` | `/sql/schema` | Whitelisted tables and columns |
| `GET` | `/sql/examples` | Pre-built example questions |
| `GET` | `/sql/health` | Health check (public) |

---

## Mail Service

Independent SMTP microservice. Renders Jinja2 HTML templates, sends emails via SMTP, and logs every send attempt to PostgreSQL.

**Email templates:** `fraud_alert`, `critical_alert`, `client_response`, `nightly_report`

**Endpoints (via gateway `/mail/`):**

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/mail/send` | Send an email with optional attachment |
| `GET` | `/mail/history` | Paginated email audit log (filter by template, status, IBAN) |
| `GET` | `/mail/stats` | Dashboard counts (by template, by status, TRACFIN count) |
| `GET` | `/mail/health` | Health check + DB connectivity |

---

## Fraud Detection Service

**Endpoints (via gateway `/fraud/`):**

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/fraud/analyze` | Full fraud analysis (IBAN + action) |
| `GET` | `/fraud/health` | Health check |
| `GET` | `/fraud/reports/<file>` | Download Excel report (no auth — filename is the secret) |

**Transaction file lookup order:**
1. `FRAUD_DATA_DIR` env var if set
2. `/app/data/transactions.xlsx` (Docker)
3. `backend/data/transactions.xlsx` (local dev)

---

## Memory System

```
Every message →
  MemoryManager.build_context()
        │
        ├─ 1. Load all messages from PostgreSQL
        │
        ├─ 2. Split: old msgs (to summarize) + recent 12 msgs (keep intact)
        │
        ├─ 3. Redis HIT? ──YES──▶ use cached summary (~1 ms)
        │         │
        │        NO
        │         ▼
        │    Generate summary via LLM → store in Redis (TTL 1 h)
        │
        └─ 4. Assemble context within 3,000 token budget
                [summary ~200 tokens] + [12 recent msgs] + [new message]
                         ▼
                   LLM (Ollama / Groq)

Nightly archiving (02:00) →
  archive_messages management command
        ├─ Conversations with > 50 messages
        ├─ Generate consolidated LLM summary
        ├─ Save summary → Conversation.summary (PostgreSQL)
        └─ Delete old messages (keep last 12)
             Result: ~94% reduction in PostgreSQL size
```

### Storage summary

| Data | Where | Lifetime |
|------|-------|---------|
| All messages (raw) | PostgreSQL `chatbot_message` | Permanent until archiving |
| Archived summary | PostgreSQL `chatbot_conversation.summary` | Permanent |
| Session summary cache | Redis `bankchat:mem:{session}:summary` | 1 hour TTL |
| Email audit log | PostgreSQL `mail_db.sent_emails` | Permanent |

---

## Prerequisites

| Tool | Version | Check |
|------|---------|-------|
| Node.js | LTS (20+) | `node -v` |
| npm | 10+ | `npm -v` |
| Python | 3.12+ | `python --version` |
| Docker | 20+ | `docker --version` |
| Ollama | latest | `ollama --version` |

---

## Quick Start (Docker — recommended)

### 1. Start all services

```powershell
docker compose up -d --build
```

Services started:

| Service | URL |
|---------|-----|
| API Gateway (single entry point) | `http://localhost` |
| Angular SPA | `http://localhost:4200` |
| Keycloak admin console | `http://localhost/auth` |
| Django API (direct) | `http://localhost:8000` |
| Fraud Service (direct) | `http://localhost:8001` |
| Mail Service (direct) | `http://localhost:8002` |
| Text-to-SQL Service (direct) | `http://localhost:8003` |
| PostgreSQL | `localhost:5432` |
| Redis | `localhost:6379` |

### 2. Start Ollama (on your host machine)

```powershell
ollama serve
ollama pull llama3.2
```

> Ollama runs on your machine. Docker connects to it via `host.docker.internal:11434`.

### 3. Configure Keycloak

Follow [docs/keycloak-setup.md](docs/keycloak-setup.md) to configure the realm, client, and test user.

Required Keycloak roles: `bank_agent`, `admin`

### 4. Login

Open `http://localhost:4200` — you will be redirected to Keycloak.
Login with your test user (e.g. `testuser` / `test1234`).

---

## Local Development (without Docker)

### Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r chatbot/requirements.txt
cp .env.example .env
# Edit .env — see Environment Variables section
python manage.py migrate
python manage.py runserver
```

### Text-to-SQL Service

```powershell
cd text-to-sql-service
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# Set DATABASE_URL, KEYCLOAK_* env vars
uvicorn main:app --port 8003
```

### Mail Service

```powershell
cd mail-service
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# Set SMTP_*, MAIL_DATABASE_URL env vars
uvicorn main:app --port 8002
```

### Frontend

```powershell
cd frontend
npm install
npm start
```

---

## Project Structure

```
bank_chat/
├── docker-compose.yml
├── .env                          # SMTP credentials for mail-service
├── docs/
│   └── keycloak-setup.md
├── postgres/                     # Init SQL scripts (create DBs + users)
├── api-gateway/
│   ├── Dockerfile
│   └── nginx.conf                # Routing: /api/, /fraud/, /mail/, /sql/, /auth/
├── backend/                      # Django orchestrateur
│   ├── .env.example
│   ├── Dockerfile
│   ├── config/
│   │   ├── settings.py
│   │   ├── urls.py
│   │   └── wsgi.py
│   ├── chatbot/
│   │   ├── auth/
│   │   │   ├── authentication.py
│   │   │   ├── permissions.py    # IsAuthenticated, IsBankAgent, IsAdmin
│   │   │   ├── keycloak_client.py
│   │   │   ├── views.py          # Role/user CRUD proxied to Keycloak Admin API
│   │   │   └── urls.py           # /me/, /roles/, /users/ endpoints
│   │   ├── graph/
│   │   │   ├── state.py
│   │   │   ├── nodes.py          # Agents: fraud, sql, account, transfer, support, fallback
│   │   │   └── orchestrator.py
│   │   ├── management/commands/
│   │   │   └── archive_messages.py
│   │   ├── migrations/
│   │   ├── memory_manager.py     # Redis + PostgreSQL intelligent memory
│   │   ├── archiving.py
│   │   ├── models.py
│   │   ├── serializers.py
│   │   ├── views.py              # Chat, ConversationCRUD, FraudAnalyze, UserAdmin
│   │   └── urls.py
│   ├── data/
│   │   ├── transactions.xlsx     # Shared dataset for fraud analysis
│   │   └── reports/              # Generated Excel fraud reports
│   └── manage.py
├── fraud-service/                # Fraud detection (FastAPI + LangGraph)
│   ├── Dockerfile
│   ├── main.py
│   ├── requirements.txt
│   └── fraud/
│       ├── graph.py
│       ├── loader.py
│       ├── nodes.py
│       ├── report.py
│       ├── rules.py
│       ├── scoring.py
│       └── state.py
├── mail-service/                 # SMTP email service (FastAPI)
│   ├── Dockerfile
│   ├── main.py                   # /send, /history, /stats, /health
│   ├── requirements.txt
│   └── templates/                # Jinja2 HTML email templates
│       ├── fraud_alert.html
│       ├── critical_alert.html
│       ├── client_response.html
│       └── nightly_report.html
├── text-to-sql-service/          # NL → SQL service (FastAPI)
│   ├── Dockerfile
│   ├── main.py                   # /query, /schema, /examples, /health
│   ├── requirements.txt
│   └── sql_agent/
│       ├── schema.py             # Whitelisted tables + LLM prompt
│       ├── generator.py          # LLM: NL → SQL
│       ├── validator.py          # Security checks (SELECT-only, whitelist)
│       ├── executor.py           # PostgreSQL execution
│       ├── explainer.py          # Formats results (markdown table + prose)
│       └── auth.py               # JWT require_bank_agent dependency
└── frontend/
    └── src/app/
        ├── auth/
        │   ├── keycloak.service.ts
        │   ├── auth.interceptor.ts
        │   └── auth.guard.ts
        ├── services/
        │   └── chat.service.ts
        ├── fraud-settings/       # Fraud settings UI component
        ├── chat/
        └── app.ts
```

---

## API Endpoints

### Django Orchestrateur (`/api/v1/chatbot/`)

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `POST` | `chat/` | Any user | Standard (blocking) chat |
| `POST` | `chat/stream/` | Any user | Streaming SSE chat |
| `POST` | `fraud/analyze/` | `bank_agent` / `admin` | Direct fraud analysis |
| `GET` | `conversations/` | Any user | Conversation list |
| `GET` | `conversations/<session_id>/` | Any user | Conversation detail + messages |
| `DELETE` | `conversations/<session_id>/` | Any user | Delete + invalidate Redis cache |
| `GET` | `health/` | Public | Health check |
| `GET` | `me/` | Any user | Current user info and roles |
| `GET` | `roles/` | `admin` | List Keycloak realm roles |
| `POST` | `roles/` | `admin` | Create realm role |
| `GET/PUT/DELETE` | `roles/<role_name>/` | `admin` | Role detail / update / delete |
| `GET` | `roles/<role_name>/users/` | `admin` | Users with a specific role |
| `GET` | `users/` | `admin` | List all Keycloak users |
| `GET/PUT/DELETE` | `users/<user_id>/` | `admin` | User detail / update / delete |
| `GET/POST/DELETE` | `users/<user_id>/roles/` | `admin` | Get / assign / remove user roles |
| `GET` | `admin/users/list/` | `admin` | User list with roles (legacy) |
| `POST` | `admin/users/update-role/` | `admin` | Update user role (legacy) |

### Text-to-SQL Service (via gateway `/sql/`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/sql/query` | `bank_agent` / `admin` | NL → SQL → results |
| `GET` | `/sql/schema` | `bank_agent` / `admin` | Available schema |
| `GET` | `/sql/examples` | `bank_agent` / `admin` | Example questions |
| `GET` | `/sql/health` | Public | Health check |

### Mail Service (via gateway `/mail/`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/mail/send` | None (internal) | Send email from template |
| `GET` | `/mail/history` | None (internal) | Email audit log |
| `GET` | `/mail/stats` | None (internal) | Dashboard statistics |
| `GET` | `/mail/health` | Public | Health check |

### Fraud Service (via gateway `/fraud/`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/fraud/analyze` | JWT | Full fraud analysis |
| `GET` | `/fraud/health` | Public | Health check |
| `GET` | `/fraud/reports/<file>` | Public (URL secret) | Download Excel report |

---

## Environment Variables

### Backend (`backend/.env`)

| Variable | Description | Default |
|----------|-------------|---------|
| `DJANGO_SECRET_KEY` | Django secret key | (insecure default) |
| `DEBUG` | Debug mode | `False` |
| `ALLOWED_HOSTS` | Allowed hosts | `localhost,127.0.0.1,chatbot` |
| `DB_NAME` | PostgreSQL database name | `bank_orchestrateur` |
| `DB_USER` | PostgreSQL user | `orchestrateur_user` |
| `DB_PASSWORD` | PostgreSQL password | `orchestrateur_password` |
| `DB_HOST` | PostgreSQL host | `db` |
| `DB_PORT` | PostgreSQL port | `5432` |
| `REDIS_URL` | Redis connection URL | `redis://redis:6379/0` |
| `LLM_PROVIDER` | LLM backend: `ollama` or `groq` | `ollama` |
| `OLLAMA_BASE_URL` | Ollama server URL | `http://host.docker.internal:11434` |
| `OLLAMA_MODEL` | Ollama model name | `llama3.2` |
| `GROQ_API_KEY` | Groq API key (if provider=groq) | — |
| `GROQ_MODEL` | Groq model name | `llama-3.3-70b-versatile` |
| `FRAUD_SERVICE_URL` | Fraud service URL | `http://fraud-service:8001` |
| `FRAUD_DATA_DIR` | Optional override for transaction file directory | `/app/data` |
| `TEXT2SQL_SERVICE_URL` | Text-to-SQL service URL | `http://text-to-sql-service:8003` |
| `MAIL_SERVICE_URL` | Mail service URL | `http://mail-service:8002` |
| `ALERT_EMAIL` | Compliance alert email recipient | `compliance@yourbank.com` |
| `KEYCLOAK_URL` | Keycloak server URL | `http://keycloak:8080` |
| `KEYCLOAK_REALM` | Keycloak realm name | `myrealm` |
| `KEYCLOAK_CLIENT_ID` | Keycloak client ID | `bank_chat` |
| `KEYCLOAK_ISSUER` | Public issuer URL | `http://localhost/auth` |

### Root `.env` (for mail-service SMTP)

| Variable | Description |
|----------|-------------|
| `SMTP_HOST` | SMTP server host (e.g. `smtp.gmail.com`) |
| `SMTP_PORT` | SMTP port (default `587`) |
| `SMTP_USER` | SMTP username / Gmail address |
| `SMTP_PASSWORD` | SMTP password / App Password |
| `SMTP_FROM` | From address (defaults to `SMTP_USER`) |

### Text-to-SQL Service (set via docker-compose environment)

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL URL for `banking_data` | `postgresql://sql_user:sql_password@db:5432/banking_data` |
| `SQL_MAX_ROWS` | Maximum rows returned per query | `100` |
| `SQL_TIMEOUT_MS` | Query timeout in milliseconds | `30000` |
| `KEYCLOAK_URL` | Keycloak URL for JWT validation | `http://keycloak:8080/auth` |
| `KEYCLOAK_REALM` | Keycloak realm | `myrealm` |
| `KEYCLOAK_CLIENT_ID` | Keycloak client ID | `bank_chat` |

### Frontend (`frontend/src/environments/environment.ts`)

| Setting | Description | Default |
|---------|-------------|---------|
| `apiBaseUrl` | Backend API URL | `http://localhost/api/v1/chatbot` |
| `keycloak.url` | Keycloak URL | `http://localhost/auth` |
| `keycloak.realm` | Realm name | `myrealm` |
| `keycloak.clientId` | Client ID | `bank_chat` |

---

## Memory System — Configuration

Memory behavior is controlled by constants in `backend/chatbot/memory_manager.py`:

| Constant | Default | Description |
|----------|---------|-------------|
| `TOKEN_BUDGET` | `3000` | Max tokens sent to LLM per request |
| `RECENT_TURNS` | `6` | Number of recent exchanges kept intact |
| `SUMMARY_TRIGGER` | `8` | Summarize when conversation exceeds N messages |
| `SUMMARY_MAX_TOKENS` | `200` | Max length of compressed summary |
| `SESSION_TTL` | `3600` | Redis TTL in seconds (1 hour) |

Archiving behavior is controlled in `backend/chatbot/archiving.py`:

| Constant | Default | Description |
|----------|---------|-------------|
| `ARCHIVE_THRESHOLD` | `50` | Archive when conversation exceeds N messages |
| `KEEP_RECENT` | `12` | Messages kept after archiving |
| `BATCH_SIZE` | `100` | Conversations processed per archiving run |

---

## Scripts

### Backend

```powershell
# Apply migrations
python manage.py migrate

# Test archiving without modifying database
python manage.py archive_messages --dry-run

# Archive a specific conversation
python manage.py archive_messages --session <session_id>

# Run full archiving batch
python manage.py archive_messages

# Development server
python manage.py runserver
```

### Frontend

```powershell
npm start        # Dev server (http://localhost:4200)
npm run build    # Production build
npm test         # Unit tests
```

### Docker

```powershell
# Start all services
docker compose up -d --build

# View logs for a specific service
docker logs bank_chat_orchestrateur -f
docker logs bank_chat_fraud -f
docker logs bank_chat_text2sql -f
docker logs bank_chat_mail -f

# View Redis cache keys
docker exec -it bank_chat_redis redis-cli KEYS "bankchat*"

# Check PostgreSQL conversation summaries
docker exec -it bank_chat_db psql -U postgres -d bank_orchestrateur -c \
  "SELECT session_id, LEFT(summary,80), archived_count FROM chatbot_conversation;"

# Check email audit log
docker exec -it bank_chat_db psql -U postgres -d mail_db -c \
  "SELECT sent_at, recipient, template_type, status, risk_level FROM sent_emails ORDER BY sent_at DESC LIMIT 10;"

# Manual archiving inside Docker
docker exec bank_chat_orchestrateur python manage.py archive_messages

# Rebuild a specific service after code change
docker compose up -d --build fraud-service
docker compose up -d --build text-to-sql-service
docker compose up -d --build mail-service
```

---

## Running All Services (local dev — multiple terminals)

| Terminal | Command | Service |
|----------|---------|---------|
| 1 | `ollama serve` | Ollama LLM |
| 2 | `docker compose up -d db redis keycloak` | PG + Redis + Keycloak |
| 3 | `cd backend && python manage.py runserver` | Django API (:8000) |
| 4 | `cd fraud-service && uvicorn main:app --port 8001` | Fraud Service (:8001) |
| 5 | `cd mail-service && uvicorn main:app --port 8002` | Mail Service (:8002) |
| 6 | `cd text-to-sql-service && uvicorn main:app --port 8003` | Text-to-SQL (:8003) |
| 7 | `cd frontend && npm start` | Angular (:4200) |

---

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| `Transaction file not found` | `backend/data/transactions.xlsx` missing or not mounted | Make sure the file exists; check the `fraud-service` volume |
| Fraud analysis returns no transactions | IBAN not in the spreadsheet | Check the IBAN format and contents of `transactions.xlsx` |
| `403 Forbidden` on authenticated requests | Token audience mismatch | Add/verify the Keycloak audience mapper for `bank_chat` |
| `403` on fraud or SQL agent | User lacks `bank_agent` or `admin` role | Assign the role in Keycloak or via `/api/v1/chatbot/users/<id>/roles/` |
| Mail not sent | Wrong SMTP credentials | Check `SMTP_USER` / `SMTP_PASSWORD` (use a Gmail App Password) |
| Text-to-SQL returns validation error | Query touches a blocked table or uses DML | The service is read-only; rephrase as a SELECT question |
| `503 Service Unavailable` from Keycloak admin | Admin service token missing | Check `KEYCLOAK_CLIENT_SECRET` in backend `.env` |
