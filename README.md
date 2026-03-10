# 🏦 BankChat — AI-Powered Banking Assistant

An intelligent banking chatbot built with **Angular 21**, **Django 6**, **LangGraph** multi-agent orchestration, and **Keycloak** authentication.

## Architecture

```
┌─────────────┐     JWT      ┌─────────────┐     LangGraph     ┌─────────────┐
│   Angular    │ ──Bearer──▶  │   Django     │ ───────────────▶  │  Groq LLM   │
│  (frontend)  │ ◀──JSON───  │  (backend)   │ ◀───────────────  │  (LLaMA 3)  │
└──────┬───────┘              └──────┬───────┘                   └─────────────┘
       │                             │
       │  OAuth2/OIDC                │  JWT validation
       ▼                             ▼
┌─────────────┐              ┌─────────────┐
│  Keycloak   │              │ PostgreSQL   │
│   (auth)    │              │   (data)     │
└─────────────┘              └─────────────┘
```

## Prerequisites

| Tool | Version | Check |
|------|---------|-------|
| Node.js | LTS (20+) | `node -v` |
| npm | 10+ | `npm -v` |
| Python | 3.12+ | `python --version` |
| Docker | 20+ | `docker --version` |
| PostgreSQL | 14+ | `psql --version` |

## Quick Start

### 1) Start Keycloak

```powershell
docker compose up -d
```

Then follow [docs/keycloak-setup.md](docs/keycloak-setup.md) to configure the realm, client, and test user.

### 2) Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r chatbot/requirements.txt
```

Create a `.env` file from the template:

```powershell
cp .env.example .env
# Edit .env with your values (DB password, GROQ_API_KEY, etc.)
```

Run migrations and start:

```powershell
python manage.py migrate
python manage.py runserver
```

Backend runs on `http://localhost:8000/`

### 3) Frontend

```powershell
cd frontend
npm install
npm start
```

Frontend runs on `http://localhost:4200/`

### 4) Login & Logout

**Login:** Open `http://localhost:4200` — you'll be redirected to Keycloak.
Login with the test user you created (e.g. `testuser` / `test1234`).

**Logout:** Click your profile section at the bottom-left of the sidebar → select **"Log out"**.
You will be redirected back to the Keycloak sign-in page.

## Project Structure

```
bank_chat/
├── docker-compose.yml          # Keycloak container
├── docs/
│   └── keycloak-setup.md       # Auth setup guide
├── backend/
│   ├── .env.example            # Environment template
│   ├── config/
│   │   ├── settings.py         # Django settings (incl. Keycloak config)
│   │   ├── urls.py
│   │   └── wsgi.py
│   ├── chatbot/
│   │   ├── auth/               # Keycloak JWT authentication
│   │   │   ├── authentication.py
│   │   │   └── keycloak_client.py
│   │   ├── graph/              # LangGraph multi-agent orchestration
│   │   │   ├── state.py
│   │   │   ├── nodes.py
│   │   │   └── orchestrator.py
│   │   ├── models.py
│   │   ├── serializers.py
│   │   ├── views.py
│   │   ├── urls.py
│   │   └── requirements.txt
│   └── manage.py
├── frontend/
│   └── src/
│       ├── environments/       # Keycloak + API config per environment
│       │   ├── environment.ts
│       │   └── environment.prod.ts
│       ├── app/
│       │   ├── auth/           # Keycloak integration (login, logout, JWT)
│       │   │   ├── keycloak.service.ts   ← provides username, email, userInitial, logout()
│       │   │   ├── auth.interceptor.ts
│       │   │   └── auth.guard.ts
│       │   ├── services/
│       │   │   └── chat.service.ts
│       │   ├── chat/           # Main chat UI + sidebar profile section
│       │   │   ├── chat.component.ts     ← profile menu logic, logout handler
│       │   │   ├── chat.component.html   ← sidebar profile trigger + popover
│       │   │   └── chat.component.css    ← profile styles (ChatGPT-style)
│       │   ├── app.ts
│       │   ├── app.config.ts
│       │   └── app.routes.ts
│       └── main.ts
└── README.md
```

## Running All Services

You need 3 terminals:

| Terminal | Command | Service |
|----------|---------|---------|
| 1 | `docker compose up -d` | Keycloak (http://localhost:8080) |
| 2 | `cd backend && python manage.py runserver` | Django API (http://localhost:8000) |
| 3 | `cd frontend && npm start` | Angular app (http://localhost:4200) |

## Environment Variables

### Backend (`backend/.env`)

| Variable | Description | Default |
|----------|-------------|---------|
| `DJANGO_SECRET_KEY` | Django secret key | (insecure default) |
| `DEBUG` | Debug mode | `True` |
| `DB_NAME` | PostgreSQL database name | `bank_chat` |
| `DB_USER` | PostgreSQL user | `postgres` |
| `DB_PASSWORD` | PostgreSQL password | (empty) |
| `DB_HOST` | PostgreSQL host | `localhost` |
| `DB_PORT` | PostgreSQL port | `5432` |
| `GROQ_API_KEY` | Groq API key for LLM | (required) |
| `KEYCLOAK_URL` | Keycloak server URL | `http://localhost:8080` |
| `KEYCLOAK_REALM` | Keycloak realm name | `myrealm` |
| `KEYCLOAK_CLIENT_ID` | Keycloak client ID | `bank_chat` |

### Frontend (`frontend/src/environments/environment.ts`)

| Setting | Description | Default |
|---------|-------------|---------|
| `apiBaseUrl` | Backend API URL | `http://localhost:8000/api/v1/chatbot` |
| `keycloak.url` | Keycloak URL | `http://localhost:8080` |
| `keycloak.realm` | Realm name | `myrealm` |
| `keycloak.clientId` | Client ID | `bank_chat` |

## Scripts

### Frontend

```powershell
npm start      # Dev server (http://localhost:4200)
npm run build  # Production build
npm test       # Unit tests
```

### Backend

```powershell
python manage.py runserver   # Dev server
python manage.py migrate     # Apply migrations
python manage.py test        # Run tests
```