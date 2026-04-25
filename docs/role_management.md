# Role-Based Access Control (RBAC) Implementation

## Overview
BankChat uses **Keycloak** as the Identity and Access Management (IAM) provider. Security is enforced at three levels: Frontend, Backend Orchestrator, and Microservices.

## 1. Roles
- `bank_agent`: Allows access to specialized nodes (Fraud Agent, SQL Agent) and history viewing.
- `admin`: Full access to settings and system triggers.

## 2. Frontend Enforcement (Angular)
- **Guards**: `bankAgentGuard` and `adminGuard` prevent unauthorized routing.
- **UI Gating**: Components use `*ngIf="isBankAgent"` to hide/show restricted UI elements (like the Fraud Agent selector).

## 3. Backend Enforcement (Django)
- **DRF Permissions**: `KeycloakAuthentication` validates standard JWTs. `IsBankAgent` and `IsAdmin` permission classes are used on specific views.
- **State Forwarding**: The orchestrator extracts the Bearer token and stores it in `BankChatState.auth_token` to forward it to downstream services.

## 4. Service-to-Service Security
- **JWT Forwarding**: Microservices (like `fraud-service`) require a valid `bank_agent` token in the `Authorization` header.
- **FastAPI Dependency**: `require_bank_agent` validates the signature and roles for every request.
