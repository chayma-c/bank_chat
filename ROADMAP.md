# 🏦 Project Roadmap & Catch-up Guide

Welcome back! This document summarizes the current state of **BankChat** to help you get back up to speed after your week away.

---

## 🏗️ System Architecture (Current State)

The project is a **microservices-based AI Banking Assistant** orchestrated via Docker.

### Core Services
1.  **Frontend (Angular 21)**: Modern chat UI, Keycloak-integrated authentication, and administrative dashboards.
2.  **Orchestrator (Django 6)**: The central brain. Uses **LangGraph** to route user intents to specialized agents.
3.  **Fraud Service (FastAPI)**: Specialized microservice for transaction analysis. Uses a **dynamic rule engine** (stored in DB) and generates Excel reports.
4.  **Mail Service (FastAPI)**: Independent service for SMTP alerts and tracking of sent communications.
5.  **API Gateway (Nginx)**: Unified entry point (Port 80) for all backend services.
6.  **Auth (Keycloak)**: Handles OIDC login, JWT generation, and Role-Based Access Control (RBAC).

---

## 🚀 Key Features Implemented

### 🧠 Intelligent Memory System
- **Real-time**: Recent messages are kept in context; older ones are summarized via LLM and cached in **Redis** (1h TTL) for lightning-fast sub-token lookups.
- **Persistence**: Full history in PostgreSQL.
- **Archiving**: Nightly batch jobs consolidate long conversations into permanent summaries, reducing DB size by ~94%.

### 🕵️ Dynamic Fraud Detection
- **Multi-domain rules**: Behavioral, Velocity, Geo, and Limit-based scoring.
- **Rule Engine**: Rules are now read **dynamically from the database**, allowing you to toggle/edit rules without restarting services.
- **Excel Integration**: Reverted from Google Sheets to **Local Excel** for transaction data loading and report generation (located in `backend/data/`).
- **Reporting**: Generates automated TRACFIN alerts and summary reports sent via the Mail Service.

### 🔐 Security & Auth
- **Keycloak RBAC**: Implemented roles (`Admin`, `Bank Agent`) to gate features like the Admin Dashboard and Fraud Settings.
- **JWT Validation**: All microservices validate RS256 signatures from Keycloak.

---

## 📅 Recent Progress (Last Week)

1.  **Fraud Service Refactoring**: Completely decoupled from Google Sheets; it now operates on local Excel files for better performance and privacy.
2.  **Dynamic Rule Engine**: Moved fraud rules from static code to PostgreSQL, enabling the "Fraud Settings" UI.
3.  **Nginx Stability**: Resolved `502 Bad Gateway` issues by optimizing upstream health checks and timeouts.
4.  **Keycloak Documentation**: Updated the setup guide to include detailed role management instructions.
5.  **Architecture Audit**: Completed a gap analysis against banking industry standards (Maturity Score: 78%).

---

## 🛠️ How to Resume Development

### 1. Start the Environment
```powershell
docker compose up -d --build
```
*Note: Ensure Ollama is running locally (`ollama serve`).*

### 2. Verify Health
Check the unified health endpoint:
- Gateway: `http://localhost/health`
- Frontend: `http://localhost:4200`

### 3. Current Focus Areas (To-Do)
- [ ] **SQL Agent Refinement**: Improve the orchestrator's ability to query transaction history directly via the SQL agent.
- [ ] **Admin Dashboard UI**: Polish the user role management interface in the Angular app.
- [ ] **Email Templates**: Add more professional Jinja2 templates to the `mail-service`.

---

## 📂 Key Files to Review
- [architecture.md](file:///c:/Users/chayma/Desktop/bank_chat/architecture.md): Visual diagram of the data flow.
- [docker-compose.yml](file:///c:/Users/chayma/Desktop/bank_chat/docker-compose.yml): Service definitions and network layout.
- [fraud-service/fraud/nodes.py](file:///c:/Users/chayma/Desktop/bank_chat/fraud-service/fraud/nodes.py): The core logic for the new dynamic fraud analysis.
