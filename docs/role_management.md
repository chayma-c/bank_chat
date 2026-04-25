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

## 5. In-App User Management
The system now includes a dedicated **User Security** dashboard accessible only to `admin` users.

- **Frontend Route**: `/admin/users`
- **Features**:
    - **User Listing**: Real-time synchronization with Keycloak identity store.
    - **Role Toggles**: One-click assignment/revocation of `bank_agent` and `admin` roles.
- **Backend Architecture**:
    - **Proxy Layer**: Django views acting as a secure proxy to the Keycloak Admin API.
    - **Admin Token**: The backend automatically manages a high-privilege master token to perform realm modifications on behalf of the UI admin.
