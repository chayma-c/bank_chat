┌─────────────────────────────────────────────────────────────────────────┐
│                           DOCKER COMPOSE                                |
│                                                                         │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐    │
│  │ 🐘 Postgres │  │ 🔐 Keycloak │  │ 🐍 Django   │  │ 🅰️ Angular │    │
│  │   :5432     │  │   :8080     │  │   :8000     │  │   :4200      │    │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬───────┘    │
│         │                │                │                 │           │
└─────────┼────────────────┼────────────────┼─────────────────┼──────────┘
          │                │                │                 │
          │   stores       │  JWT verify    │  REST API       │  serves UI
          │   data         │  auth          │  + SSE          │
          ▼                ▼                ▼                 ▼

══════════════════════════════════════════════════════════════════════════

         👤 User (Browser)
              │
              │  types message
              ▼
    ┌──────────────────┐
    │   Angular App    │
    │                  │
    │  Auth (Keycloak) │
    │  Chat UI         │
    │  Services        │
    └────────┬─────────┘
             │
             │  POST + Bearer JWT
             │  (REST or SSE stream)
             ▼
    ┌──────────────────┐
    │   Django API     │
    │                  │
    │  JWT validation  │
    │  Conversation DB │
    │  Context builder │
    └────────┬─────────┘
             │
             ▼
    ┌──────────────────────────────────────────────────────┐
    │           🧠 LangGraph Orchestrator                  │
    │                                                      │
    │              ┌────────────────┐                      │
    │              │ Detect Intent  │                      │
    │              │   (LLM)        │                      │
    │              └───────┬────────┘                      │
    │                      │                               │
    │        ┌─────┬───────┼───────┬──────────┐           │
    │        ▼     ▼       ▼       ▼          ▼           │
    │     ┌─────┐┌──────┐┌──────┐┌─────┐ ┌────────┐      │
    │     │ 💰  ││ 💸   ││ 🛟   ││ 🔍  │ │ 💬     │      │
    │     │Acct ││Trans ││Supp. ││Fraud│ │Fallback│      │
    │     │Agent││Agent ││Agent ││Agent│ │       │      │
    │     └──┬──┘└──┬───┘└──┬───┘└──┬──┘ └───┬────┘      │
    │        │      │       │       │        │            │
    │        ▼      ▼       ▼       │        ▼            │
    │      LLM    LLM     LLM      │      LLM            │
    │   response response response  │   response           │
    │                               │                      │
    └───────────────────────────────┼──────────────────────┘
                                    │
                     delegates to sub-graph
                                    │
                                    ▼
    ┌──────────────────────────────────────────────────────┐
    │          🔍 Fraud Detection Sub-Graph                 │
    │                                                      │
    │   ┌──────────┐                                       │
    │   │  Parse   │  extract IBAN + desired action        │
    │   │  Request │                                       │
    │   └────┬─────┘                                       │
    │        ▼                                             │
    │   ┌──────────┐                                       │
    │   │  Load    │  Excel file → filter by IBAN          │
    │   │  Data    │                                       │
    │   └────┬─────┘                                       │
    │        │                                             │
    │   ┌────┴──────────────┐                              │
    │   ▼                   ▼                              │
    │ ┌──────────┐   ┌────────────┐                        │
    │ │ Analyze  │   │  Export    │                        │
    │ │ Fraud    │   │  to Excel  │                        │
    │ │          │   │            │                        │
    │ │ 13 rules │   └─────┬──────┘                        │
    │ │ scoring  │         │                               │
    │ │ TRACFIN  │         │                               │
    │ │ report   │         │                               │
    │ └────┬─────┘         │                               │
    │      │               │                               │
    │      ▼               ▼                               │
    │   ┌──────────────────────┐                           │
    │   │  Generate Summary    │  LLM natural language     │
    │   │  + Excel Report      │  fraud report             │
    │   └──────────┬───────────┘                           │
    │              ▼                                       │
    │             END                                      │
    └──────────────────────────────────────────────────────┘
                   │
                   ▼
    ┌──────────────────┐
    │  Response back    │
    │  to Angular UI    │
    │  (JSON or SSE)    │
    └──────────────────┘


══════════════════════════════════════════════════════════════════════════

    🔑 Auth Flow:    Angular → Keycloak login → JWT → Django validates
    💬 Chat Flow:    Angular → Django API → LangGraph → Agent → LLM → response
    🔍 Fraud Flow:   Angular → Django API → LangGraph → Fraud Sub-Graph
                         → Excel load → 13 rules + scoring → report → LLM summary
    💾 Storage:      PostgreSQL (conversations) + Excel files (transactions & reports)

══════════════════════════════════════════════════════════════════════════

    ⚙️  Tech Stack

    Frontend:   Angular · TypeScript · Tailwind
    Backend:    Django · Django REST Framework
    AI:         LangChain · LangGraph · Groq / Ollama
    Auth:       Keycloak · JWT (RS256)
    Database:   PostgreSQL 16
    Data:       Pandas · OpenPyXL
    Infra:      Docker Compose