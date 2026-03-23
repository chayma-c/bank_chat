# 🏦 BankChat — AI-Powered Banking Assistant

An intelligent banking chatbot built with **Angular 21**, **Django 6**, **LangGraph** multi-agent orchestration, **Keycloak** authentication, and an **intelligent memory system** (Redis + PostgreSQL).

## Architecture

```
┌─────────────┐     JWT      ┌─────────────┐     LangGraph     ┌──────────────────┐
│   Angular    │ ──Bearer──▶  │   Django     │ ───────────────▶  │  Ollama / Groq   │
│  (frontend)  │ ◀──JSON───  │  (backend)   │ ◀───────────────  │  (LLaMA 3)       │
└──────┬───────┘              └──────┬───────┘                   └──────────────────┘
       │                             │
       │  OAuth2/OIDC                ├── PostgreSQL  (messages + résumés archivés)
       ▼                             └── Redis       (cache résumé session TTL 1h)
┌─────────────┐
│  Keycloak   │
│   (auth)    │
└─────────────┘
```

## Memory System Architecture

```
Every message →
  MemoryManager.build_context()
        │
        ├─ 1. Load all messages from PostgreSQL
        │
        ├─ 2. Split: old msgs (to summarize) + recent 12 msgs (keep intact)
        │
        ├─ 3. Redis HIT?  ──YES──▶ use cached summary (~1ms)
        │         │
        │        NO
        │         ▼
        │    Generate summary via LLM → store in Redis (TTL 1h)
        │
        └─ 4. Assemble context within 3,000 token budget
                [summary ~200 tokens] + [12 recent msgs] + [new message]
                         │
                         ▼
                    LLM (Ollama / Groq)

Nightly archiving (02:00) →
  archive_messages management command
        │
        ├─ Conversations with > 50 messages
        ├─ Generate consolidated LLM summary
        ├─ Save summary → Conversation.summary (PostgreSQL)
        └─ Delete old messages (keep last 12)
             Result: ~94% reduction in PostgreSQL size
```

## Prerequisites

| Tool | Version | Check |
|------|---------|-------|
| Node.js | LTS (20+) | `node -v` |
| npm | 10+ | `npm -v` |
| Python | 3.12+ | `python --version` |
| Docker | 20+ | `docker --version` |
| Ollama | latest | `ollama --version` |

## Quick Start (Docker — recommended)

### 1) Start all services

```powershell
docker compose up -d --build
```

Services started:
- PostgreSQL on `localhost:5432`
- Redis on `localhost:6379`
- Keycloak on `http://localhost:8080`
- Django backend on `http://localhost:8000`
- Angular frontend on `http://localhost:4200`

### 2) Start Ollama (on your host machine)

```powershell
ollama serve
ollama pull llama3.2
```

> Ollama runs on your machine. Docker connects to it via `host.docker.internal:11434`.

### 3) Configure Keycloak

Follow [docs/keycloak-setup.md](docs/keycloak-setup.md) to configure the realm, client, and test user.

### 4) Login

Open `http://localhost:4200` — redirected to Keycloak.
Login with your test user (e.g. `testuser` / `test1234`).

---

## Local Development (without Docker)

### Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r chatbot/requirements.txt
```

Create `.env` from template:

```powershell
cp .env.example .env
# Edit .env — see Environment Variables section
```

Run migrations and start:

```powershell
python manage.py migrate
python manage.py runserver
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
├── docs/
│   └── keycloak-setup.md
├── backend/
│   ├── .env.example
│   ├── config/
│   │   ├── settings.py           # Django settings + Redis cache config
│   │   ├── urls.py
│   │   └── wsgi.py
│   ├── chatbot/
│   │   ├── auth/
│   │   │   ├── authentication.py
│   │   │   └── keycloak_client.py
│   │   ├── graph/
│   │   │   ├── state.py
│   │   │   ├── nodes.py          # LLM init (Ollama or Groq)
│   │   │   ├── orchestrator.py
│   │   │   └── fraud/            # Fraud detection sub-graph
│   │   │       ├── graph.py
│   │   │       ├── nodes.py
│   │   │       ├── state.py
│   │   │       ├── loader.py
│   │   │       ├── rules.py
│   │   │       ├── scoring.py
│   │   │       └── report.py
│   │   ├── management/
│   │   │   └── commands/
│   │   │       └── archive_messages.py   # ← archiving command
│   │   ├── migrations/
│   │   │   ├── 0001_initial.py
│   │   │   └── 0002_conversation_summary.py  # ← adds summary fields
│   │   ├── memory_manager.py     # ← intelligent memory (Redis + PG)
│   │   ├── archiving.py          # ← PostgreSQL archiving service
│   │   ├── models.py
│   │   ├── serializers.py
│   │   ├── views.py
│   │   ├── urls.py
│   │   └── requirements.txt
│   └── manage.py
└── frontend/
    └── src/
        ├── environments/
        │   ├── environment.ts
        │   └── environment.prod.ts
        └── app/
            ├── auth/
            │   ├── keycloak.service.ts
            │   ├── auth.interceptor.ts
            │   └── auth.guard.ts
            ├── services/
            │   └── chat.service.ts
            ├── chat/
            │   ├── chat.component.ts
            │   ├── chat.component.html
            │   └── chat.component.css
            ├── app.ts
            ├── app.config.ts
            └── app.routes.ts
```

---

## Environment Variables

### Backend (`backend/.env`)

| Variable | Description | Default |
|----------|-------------|---------|
| `DJANGO_SECRET_KEY` | Django secret key | (insecure default) |
| `DEBUG` | Debug mode | `False` |
| `ALLOWED_HOSTS` | Allowed hosts | `localhost,127.0.0.1,chatbot` |
| `DB_NAME` | PostgreSQL database name | `bank_chat` |
| `DB_USER` | PostgreSQL user | `postgres` |
| `DB_PASSWORD` | PostgreSQL password | `postgresql` |
| `DB_HOST` | PostgreSQL host | `db` |
| `DB_PORT` | PostgreSQL port | `5432` |
| `REDIS_URL` | Redis connection URL | `redis://redis:6379/0` |
| `LLM_PROVIDER` | LLM backend: `ollama` or `groq` | `ollama` |
| `OLLAMA_BASE_URL` | Ollama server URL | `http://host.docker.internal:11434` |
| `OLLAMA_MODEL` | Ollama model name | `llama3.2` |
| `GROQ_API_KEY` | Groq API key (if provider=groq) | — |
| `GROQ_MODEL` | Groq model name | `llama-3.3-70b-versatile` |
| `KEYCLOAK_URL` | Keycloak server URL | `http://keycloak:8080` |
| `KEYCLOAK_REALM` | Keycloak realm name | `myrealm` |
| `KEYCLOAK_CLIENT_ID` | Keycloak client ID | `bank_chat` |

### Django `settings.py` — Redis cache (required)

Add to `backend/config/settings.py`:

```python
import os

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": os.getenv("REDIS_URL", "redis://redis:6379/0"),
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "IGNORE_EXCEPTIONS": True,  # fallback silently if Redis is down
        },
        "KEY_PREFIX": "bankchat",
        "TIMEOUT": 3600,  # 1 hour — aligned with SESSION_TTL in memory_manager.py
    }
}
```

### Frontend (`frontend/src/environments/environment.ts`)

| Setting | Description | Default |
|---------|-------------|---------|
| `apiBaseUrl` | Backend API URL | `http://localhost:8000/api/v1/chatbot` |
| `keycloak.url` | Keycloak URL | `http://localhost:8080` |
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

## Memory System — How It Works

### Per-message context (real-time)

```
PostgreSQL (all messages)
    ↓
Redis cache check
    ├── HIT  → use cached summary (~1ms, no LLM call)
    └── MISS → generate summary via LLM → cache in Redis
         ↓
Assemble context within 3,000 token budget:
    [📋 summary ~200 tokens] + [💬 12 recent messages] + [✉️ new message]
         ↓
LLM generates response
         ↓
Save new message → PostgreSQL
```

### Nightly archiving (PostgreSQL size management)

```
Conversations with > 50 messages
    ↓
Generate consolidated LLM summary of old messages
    ↓
Save summary → Conversation.summary column
    ↓
Delete old messages (keep last 12)
    ↓
Result: ~94% size reduction per conversation
```

### Storage summary

| Data | Where | Lifetime |
|------|-------|---------|
| All messages (raw) | PostgreSQL `chatbot_message` | Permanent until archiving |
| Archived summary | PostgreSQL `chatbot_conversation.summary` | Permanent |
| Session summary cache | Redis `bankchat:mem:{session}:summary` | 1 hour TTL |
| Recent messages cache | Redis `bankchat:mem:{session}:recent` | 1 hour TTL |

---

## Scripts

### Backend

```powershell
# Apply migrations (includes memory system migration)
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

# View backend logs
docker logs bank_chat_backend -f

# View Redis cache keys
docker exec -it bank_chat_redis redis-cli KEYS "bankchat*"

# Check PostgreSQL conversation summaries
docker exec -it bank_chat_db psql -U postgres -d bank_chat -c \
  "SELECT session_id, LEFT(summary,80), archived_count FROM chatbot_conversation;"

# Manual archiving inside Docker
docker exec bank_chat_backend python manage.py archive_messages
```

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/chatbot/chat/` | POST | Standard chat |
| `/api/v1/chatbot/chat/stream/` | POST | Streaming SSE chat |
| `/api/v1/chatbot/fraud/analyze/` | POST | Fraud detection |
| `/api/v1/chatbot/conversations/?user_id=X` | GET | Conversation list |
| `/api/v1/chatbot/conversations/<session_id>/` | GET | Conversation detail |
| `/api/v1/chatbot/conversations/<session_id>/` | DELETE | Delete + invalidate Redis cache |
| `/api/v1/chatbot/health/` | GET | Health check |

---

## Running All Services (local dev — 3 terminals)

| Terminal | Command | Service |
|----------|---------|---------|
| 1 | `ollama serve` | Ollama LLM (http://localhost:11434) |
| 2 | `docker compose up -d` | PG + Redis + Keycloak |
| 3 | `cd backend && python manage.py runserver` | Django API (http://localhost:8000) |
| 4 | `cd frontend && npm start` | Angular (http://localhost:4200) |
