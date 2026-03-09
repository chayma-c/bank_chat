# Keycloak Setup Guide

This guide walks you through configuring Keycloak for local development of BankChat.

---

## 1) Start Keycloak

From the project root:

```bash
docker compose up -d
```

Access the admin console at **http://localhost:8080/admin** and log in with:
- Username: `admin`
- Password: `admin`

---

## 2) Create Realm

1. Click the realm dropdown in the top-left corner → **"Create realm"**
2. Set **Realm name** to ` `
3. Click **Create**

---

## 3) Create Client

1. Go to **Clients** → **Create client**
2. Set **Client type** to `OpenID Connect`
3. Set **Client ID** to `bank_chat`
4. Click **Next**
5. Set **Client authentication** to `OFF` (public client for SPA)
6. Ensure **Standard flow** is `ON`
7. Click **Next**, then configure **Access settings**:

| Setting | Value |
|---|---|
| Root URL | `http://localhost:4200` |
| Home URL | `http://localhost:4200/` |
| Valid redirect URIs | `http://localhost:4200/*` |
| Valid post logout redirect URIs | `http://localhost:4200/*` |
| Web origins | `http://localhost:4200` |
| Admin URL | `http://localhost:4200` |

8. Click **Save**

> **⚠️ Important:** Web origins must be `http://localhost:4200` (no `/*` suffix) — this controls CORS for the token endpoint.

---

## 4) Add Audience Mapper (CRITICAL)

This step is essential — without it, the JWT token's `aud` field won't contain `bank_chat` and the backend will reject all requests with a 403.

1. Go to **Clients** → `bank_chat` → **Client scopes** tab
2. Click **`bank_chat-dedicated`**
3. Click **"Add mapper"** → **"By configuration"**
4. Select **"Audience"**
5. Fill in:

| Field | Value |
|---|---|
| Name | `bank_chat-audience` |
| Included Client Audience | `bank_chat` |
| Add to ID token | ON |
| Add to access token | ON |

6. Click **Save**

---

## 5) Create Test User

1. Go to **Users** → **Add user**
2. Fill in:
   - Username: `testuser`
   - Email: `testuser@bank.local`
   - First name: `Test`
   - Last name: `User`
   - Email verified: `ON`
   - Enabled: `ON`
3. Click **Save**
4. Go to the **Credentials** tab → **Set password**
5. Set Password to `test1234`
6. Set **Temporary** to `OFF`

> **⚠️ Important:** If Temporary is ON, Keycloak will redirect to a password-change flow after login, which breaks the SPA redirect.

7. Click **Save**

---

## 6) Verification

Use these URLs to verify your setup:

- **Realm exists:**
  `http://localhost:8080/realms/myrealm/.well-known/openid-configuration`
  → should return a JSON document

- **Client exists (shows login page):**
  `http://localhost:8080/realms/myrealm/protocol/openid-connect/auth?client_id=bank_chat&response_type=code&redirect_uri=http://localhost:4200/`
  → should display the Keycloak login page

---

## 7) How It Integrates

### Frontend (Angular)

- `keycloak-js` initializes before Angular bootstraps (`main.ts`)
- `KeycloakService` (static singleton) manages the Keycloak instance
- `authInterceptor` adds `Authorization: Bearer <token>` to all HTTP requests
- Config lives in `frontend/src/environments/environment.ts`

### Backend (Django)

- `chatbot/auth/keycloak_client.py` fetches and caches the realm's RSA public key
- `chatbot/auth/authentication.py` validates JWT tokens on every request via DRF
- Settings in `config/settings.py`: `KEYCLOAK_URL`, `KEYCLOAK_REALM`, `KEYCLOAK_CLIENT_ID`

### Auth flow

1. User opens `http://localhost:4200` → redirected to Keycloak login
2. User logs in → Keycloak redirects back with JWT
3. Angular attaches JWT to all API requests
4. Django validates JWT against Keycloak's public key
5. `request.user` is set from the token's `preferred_username`

---

## 8) Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| "Client not found" on Keycloak login page | Client ID mismatch (case-sensitive, dash vs underscore) | Check **Clients** → Client ID column in Keycloak admin |
| CORS error on `/token` endpoint | Web origins has `/*` suffix or wrong value | Set Web origins to `http://localhost:4200` (no path) |
| 403 Forbidden from backend | Token audience mismatch | Add the audience mapper (step 4 above) |
| Blank page after login | Angular DI issue — KeycloakService instance not shared | Ensure `KeycloakService` uses static fields |
| "Authentication service unavailable" | Keycloak init failed (wrong URL, port, or `/auth` prefix) | Keycloak 17+ uses no `/auth` prefix; check `environment.ts` |
| Login page appears but credentials rejected | User created in wrong realm or password is Temporary | Check user is in `myrealm` and Temporary is OFF |
