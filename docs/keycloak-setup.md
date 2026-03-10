# 🔐 Keycloak Setup Guide

> Local development setup for BankChat authentication — follow these steps in order.

---

## Prerequisites

- Docker Desktop running with at least **2GB RAM** allocated
- Project cloned and at the root directory

---

## 1. Start Keycloak

```bash
docker compose up -d
```

Once the container is up, open the admin console at:

**http://localhost:8080/admin**

Login with:
| Field | Value |
|---|---|
| Username | `admin` |
| Password | `admin` |

> ⏳ Keycloak takes 30–60 seconds to fully start. If the page doesn't load immediately, wait and refresh.

---

## 2. Create Realm

1. Click the realm dropdown in the **top-left corner**
2. Select **"Create realm"**
3. Set **Realm name** to `myrealm`
4. Click **Create**

---

## 3. Create Client

1. Go to **Clients** → **Create client**
2. Fill in the following:

| Field | Value |
|---|---|
| Client type | `OpenID Connect` |
| Client ID | `bank_chat` |

3. Click **Next**
4. Set **Client authentication** to `OFF` *(public client for SPA)*
5. Ensure **Standard flow** is `ON`
6. Click **Next** and configure access settings:

| Setting | Value |
|---|---|
| Root URL | `http://localhost:4200` |
| Home URL | `http://localhost:4200/` |
| Valid redirect URIs | `http://localhost:4200/*` |
| Valid post logout redirect URIs | `http://localhost:4200/*` |
| Web origins | `http://localhost:4200` |
| Admin URL | `http://localhost:4200` |

7. Click **Save**

> ⚠️ **Web origins** must be `http://localhost:4200` with **no** `/*` suffix — this controls CORS for the token endpoint.

---

## 4. Add Audience Mapper ⚠️ Critical

Without this step, the JWT token's `aud` field won't contain `bank_chat` and the backend will reject all requests with a **403**.

1. Go to **Clients** → `bank_chat` → **Client scopes** tab
2. Click **`bank_chat-dedicated`**
3. Click **"Add mapper"** → **"By configuration"**
4. Select **"Audience"**
5. Fill in:

| Field | Value |
|---|---|
| Name | `bank_chat-audience` |
| Included Client Audience | `bank_chat` |
| Add to ID token | `ON` |
| Add to access token | `ON` |

6. Click **Save**

---

## 5. Enable User Registration

1. Go to **Realm Settings** → **Login** tab
2. Toggle **User registration** to `ON`
3. Click **Save**

---

## 6. Create Test User

1. Go to **Users** → **Add user**
2. Fill in:

| Field | Value |
|---|---|
| Username | `testuser` |
| Email | `testuser@bank.local` |
| First name | `Test` |
| Last name | `User` |
| Email verified | `ON` |
| Enabled | `ON` |

3. Click **Save**
4. Go to the **Credentials** tab → **Set password**
5. Set password to `test1234`
6. Set **Temporary** to `OFF`
7. Click **Save**

> ⚠️ If **Temporary** is `ON`, Keycloak will redirect to a password-change flow after login, which breaks the SPA redirect.

---

## 7. Import Realm Configuration

A `realm-export.json` file is included in the `keycloak/` folder. This contains the pre-configured realm, client, mappers, and settings so you do not have to repeat steps 2–5 manually.

The `docker-compose.yml` is already set up to auto-import it on first start:

```yaml
volumes:
  - ./keycloak/themes/mytheme:/opt/keycloak/themes/mytheme
  - ./keycloak/realm-export.json:/opt/keycloak/data/import/realm-export.json
command: >
  start-dev
  --import-realm
```

> ⚠️ The import only runs on **first boot** when the realm does not already exist. If you need to reimport, delete the container and volume then run `docker compose up -d` again:

```bash
docker compose down -v
docker compose up -d
```

> 💡 If you make changes to the realm config and want to share them with the team, export the updated realm via **Realm Settings** → **Action** → **Export** and replace `keycloak/realm-export.json` before pushing.

---

## 8. Apply the Custom Theme

1. Make sure the container is running with the volume mount in `docker-compose.yml`:
```yaml
volumes:
  - ./keycloak/themes/mytheme:/opt/keycloak/themes/mytheme
```
2. Go to **Realm Settings** → **Themes** tab
3. Set **Login Theme** to `mytheme`
4. Click **Save**

> 💡 To see CSS changes live, just refresh the browser. Only restart the container if you edit `theme.properties`.

---

## 9. Verify Your Setup

Open these URLs to confirm everything is working:

**Realm config endpoint** *(should return a JSON document)*
```
http://localhost:8080/realms/myrealm/.well-known/openid-configuration
```

**Login page** *(should display the styled BankChat login)*
```
http://localhost:8080/realms/myrealm/protocol/openid-connect/auth?client_id=bank_chat&response_type=code&redirect_uri=http://localhost:4200/
```

---

## 10. How It All Connects

```
User opens http://localhost:4200
        ↓
Redirected to Keycloak login (port 8080)
        ↓
User logs in → Keycloak issues JWT
        ↓
Angular attaches JWT to all API requests (via authInterceptor)
        ↓
Django validates JWT against Keycloak's public key
        ↓
request.user is set from token's preferred_username
```

### Frontend (Angular)
- `keycloak-js` initializes before Angular bootstraps in `main.ts`
- `KeycloakService` (static singleton) manages the Keycloak instance
- `authInterceptor` adds `Authorization: Bearer <token>` to all HTTP requests
- Config lives in `frontend/src/environments/environment.ts`

### Backend (Django)
- `chatbot/auth/keycloak_client.py` fetches and caches the realm's RSA public key
- `chatbot/auth/authentication.py` validates JWT tokens on every request via DRF
- Key settings in `config/settings.py`: `KEYCLOAK_URL`, `KEYCLOAK_REALM`, `KEYCLOAK_CLIENT_ID`

---

## 11. Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| "Client not found" on login page | Client ID mismatch (case-sensitive) | Check **Clients** → Client ID column — must be `bank_chat` with underscore |
| CORS error on `/token` endpoint | Web origins has `/*` suffix | Set Web origins to `http://localhost:4200` (no path suffix) |
| 403 Forbidden from backend | Token audience mismatch | Add the audience mapper (Step 4) |
| Blank page after login | Angular DI issue — KeycloakService not shared | Ensure `KeycloakService` uses static fields |
| "Authentication service unavailable" | Keycloak init failed | Keycloak 17+ uses no `/auth` prefix — check `environment.ts` URL |
| Credentials rejected on login | User in wrong realm or password is Temporary | Confirm user is in `myrealm` and Temporary is `OFF` |
| Admin console not loading | Keycloak still starting | Wait 30–60s and retry — check `docker logs bank_chat_keycloak` |
| Port 8080 already in use | Another Keycloak instance running | Run `docker ps`, stop the old container, then `docker compose up -d` |