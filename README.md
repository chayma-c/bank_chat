# Bank Chat — Global Setup (Frontend + Backend)

This repository contains:
- `frontend/`: Angular 21 app
- `backend/`: Django 6 project

## 1) Prerequisites

Install these tools first:
- **Node.js** (LTS recommended) and **npm**
- **Python** 3.12+ (recommended for Django 6)
- (Optional but recommended) **Git**

Check versions:

```powershell
node -v
npm -v
python --version
```

## 2) Backend setup (Django)

From the project root:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install Django==6.0.2
python manage.py migrate
python manage.py runserver
```

Backend runs on:
- `http://127.0.0.1:8000/`
- Admin: `http://127.0.0.1:8000/admin/`

> If needed, create an admin user:

```powershell
python manage.py createsuperuser
```

## 3) Frontend setup (Angular)

Open a new terminal, from project root:

```powershell
cd frontend
npm install
npm start
```

Frontend runs on:
- `http://localhost:4200/`

## 4) Run both services together

Use 2 terminals:
- Terminal 1: backend (`python manage.py runserver` in `backend/` with venv activated)
- Terminal 2: frontend (`npm start` in `frontend/`)

## 5) Project scripts

### Frontend (`frontend/package.json`)

```powershell
npm start      # ng serve
npm run build  # production build
npm test       # unit tests
```

### Backend

```powershell
python manage.py runserver
python manage.py migrate
python manage.py test
```

## 6) Current status

- Backend is currently a fresh Django project (SQLite, admin route only).
- Frontend is currently a fresh Angular project scaffold.
- API integration between frontend and backend is not yet configured.
