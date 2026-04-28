# BankChat System Audit & Security Roadmap

## Executive Summary
This audit identifies critical security vulnerabilities and architectural inconsistencies. The focus is on implementing Robust RBAC and correcting logic duplication.

## 1. Security (CRITICAL)
- [x] **SEC-01**: `fraud-service/analyze` was unauthenticated. (**FIXED**)
- [x] **SEC-02**: `fraud-service/reports` leaked sensitive filenames. (**FIXED**)
- [x] **SEC-03**: Health check exposed internal paths. (**FIXED**)
- [x] **SEC-04**: Backend crashed during streaming auth due to missing `jwt` import. (**FIXED**)
- [x] **SEC-05**: Auth errors were silent; added robust logging. (**FIXED**)
- [ ] **SEC-06**: Internal communication uses local Bearer keys; move to secrets management.

## 2. Architecture (HIGH)
- [x] **ARCH-01**: Duplicated fraud logic between streaming and non-streaming. (**FIXED via unification**)
- [x] **ARCH-02**: Conversation history endpoints lack proper authorization. (**FIXED**)
- [ ] **ARCH-03**: Environment variable key mismatch between services.
- [ ] **ARCH-04**: LLM fallback system is not production-ready (Ollama integration).
- [x] **ARCH-05**: "Select Agent" SQL refers to a non-existent microservice. (**CLEANED**)

## 3. Bugs & Quality (MEDIUM)
- [x] **BUG-01**: Hardcoded `user_id` as "anonymous" in streaming mode. (**FIXED**)
- [x] **BUG-02**: Unauthorized users could see any conversation list via API. (**FIXED**)
- [ ] **BUG-03**: Excel processing overflow logic error.
- [ ] **BUG-04**: Fraud report path handling is OS-dependent.
- [x] **QUALITY-01**: System prompts contain 100+ lines of duplicated policy. (**FIXED via shared constant**)

## Next Steps
1. Synchronize environment variables across `docker-compose`.
2. Implement production-grade LLM fallback strategies.
3. Harden Docker networking to prevent localhost bypass.





## -------------------------bugs history---------------------------------




# 🔥 BankChat System Roast — Full Audit Report

> **Verdict:** The system is architecturally solid and ambition is high, but it has some dangerous gaps in security and reliability that would make any banking compliance officer lose sleep. Let's go.

---

## 🔴 CRITICAL (ship-stopping • fix before any production use)

### SEC-01 — `/analyze` is completely unauthenticated
**File:** `fraud-service/main.py:151`

`POST /analyze` — the most sensitive endpoint that runs full IBAN fraud checks and generates compliance reports — has **zero auth**. Anyone who can reach `http://localhost/fraud/analyze` (or via Nginx) can trigger expensive AI-powered fraud analysis on arbitrary IBANs, harvest the `llm_summary`, `score_final`, `risk_level`, `tracfin_required` and `download_url` from the response. This is a data exfiltration risk AND a DoS vector.

```python
# Current:
@app.post("/analyze")
async def analyze(req: FraudRequest):  # ← no Depends(require_bank_agent)
```
**Fix:** Add `Depends(require_bank_agent)` immediately.

---

### SEC-02 — `/reports/{filename}` and `/reports` are unauthenticated
**File:** `fraud-service/main.py:189,203`

Excel reports containing full IBAN transaction analysis with scores, risk levels, and TRACFIN flags are downloadable by anyone who guesses a filename. The directory listing endpoint (`GET /reports`) makes this even easier — it returns filenames, sizes, and download URLs without any auth check.

```python
# Current:
@app.get("/reports/{filename}")
async def download_report(filename: str):  # ← no auth
```
**Fix:** Add `Depends(require_bank_agent)` to both.

---

### SEC-03 — `/health` leaks internal system info
**File:** `fraud-service/main.py:222`

The health endpoint exposes `reports_dir` (internal Docker filesystem path) and a `reports_count` to the public. This reveals the filesystem structure and gives attackers a live count of valuable data.

```python
return {
    "reports_dir": str(reports_dir),      # ← internal path exposed
    "reports_count": ...,                  # ← info leak
}
```
**Fix:** Strip sensitive fields from the public health response.

---

### SEC-04 — `_get_realm_roles` silently eats **all** exceptions
**File:** `backend/chatbot/views.py:57`

```python
except Exception:
    return frozenset()  # ← invalid token = no roles = access denied, but also: Keycloak down = no roles
```
If Keycloak is temporarily unreachable (container restart, network blip), the function returns an empty set, meaning **the role check passes as if the user has no roles** — which correctly blocks access. But the *failure* is invisible. No log, no metric, no alert. Combined with a Keycloak outage, the entire chat with agent selection silently fails with confusing 403s.

```
except Exception:
    logger.warning("JWT decode failed — Keycloak may be unreachable: %s", ...)
    return frozenset()
```
**Fix:** Log at WARNING with the exception. Differentiate "bad token" from "Keycloak down."

---

### SEC-05 — `jwt` module is imported in `views.py` but never imported
**File:** `backend/chatbot/views.py:48`

The `_get_realm_roles` function calls `jwt.decode(...)` but `import jwt` is missing at the top of the file. This causes an immediate **NameError** at runtime for ANY user who touches a fraud or SQL agent via streaming. The streaming role check is completely broken.

```python
def _get_realm_roles(request) -> frozenset:
    ...
    payload = jwt.decode(...)  # ← NameError: name 'jwt' is not defined
```
**Fix:** Add `import jwt` to `views.py`.

---

### BUG-01 — `stream_agent_response` passes `user_id: "anonymous"` and `session_id: ""` hardcoded
**File:** `backend/chatbot/nodes.py:514`

```python
resp = httpx.post(
    f"{FRAUD_SERVICE_URL}/analyze",
    json={
        "user_id": "anonymous",   # ← HARDCODED — loses real user identity
        "session_id": "",         # ← HARDCODED — breaks audit trail
```
The fraud analysis triggered from the streaming path loses the real user's identity. This means the fraud report can't be tied back to the requesting user. The non-streaming `fraud_agent()` node (line 306) correctly uses `state.get("user_id", "anonymous")` — the streaming equivalent is just wrong.

**Fix:** Pass `state.get("user_id")` and `state.get("session_id")` properly.

---

## 🟠 HIGH (fix before showing to stakeholders)

### ARCH-01 — Dual fraud analysis code paths are out of sync
**File:** `nodes.py:261` vs `nodes.py:466`

The `fraud_agent()` node (non-streaming, used by ChatView) and `stream_agent_response()` (streaming, used by StreamChatView) both implement the complete ANALYZE/TALK decision logic. This is **400+ lines of duplicated business logic** that has already diverged (user_id hardcoding above, different error messages, different IBAN extraction paths). Any future bug fix or rule change must be applied in two places.

**Fix:** Extract the ANALYZE path into a shared `_do_fraud_analyze(iban, user_id, session_id, last_msg)` function that both paths call.

---

### ARCH-02 — `ConversationListView` and `ConversationDetailView` have zero auth
**File:** `backend/chatbot/views.py:143-173`

These endpoints return full conversation history for any user — accessible to anyone on the same network without a valid JWT. A client can enumerate another user's conversation history by trying different `user_id` values.

```python
class ConversationListView(APIView):
    # No authentication_classes
    # No permission_classes
    def get(self, request):
        qs = Conversation.objects.all()  # ← returns ALL conversations if no user_id param
```

**Fix:** Add `authentication_classes = [KeycloakAuthentication]` + `permission_classes = [IsAuthenticated]` to both views.

---

### BUG-02 — `ChatView` doesn't have `authentication_classes` set either
**File:** `backend/chatbot/views.py:82`

The non-streaming `ChatView` (APIView) uses `request.user` for the role check but without explicit `authentication_classes`, it relies on global DRF defaults. If the global defaults don't include `KeycloakAuthentication`, the role check compares `isinstance(request.user, dict)` against `AnonymousUser` — the check evaluates False and silently falls through, giving unauthenticated access when `selected_agent` is NOT in `_RESTRICTED_AGENTS`.

**Fix:** Add `authentication_classes = [KeycloakAuthentication]` and `permission_classes = [IsAuthenticated]` explicitly.

---

### BUG-03 — Scheduler uses `print()` instead of `logger`
**File:** `fraud-service/fraud/scheduler.py` (nearly every line)

```python
print("🚀 [scheduler] Starting Global Fraud Audit...")
print(f"⚠️ [scheduler] Loop error: {e}")
```
Every important event in the scheduler — analysis start, IBAN errors, completion — goes to stdout via `print()`. This bypasses the structured logging system, making it impossible to filter logs, set log levels, or route to log aggregators in production. Critical errors (line 90) are just `print(f"💥 ...")` — they'll be invisible in any log monitoring system that watches `ERROR` level.

**Fix:** Replace all `print()` calls with `logger.info/warning/error`.

---

### BUG-04 — `scheduler_loop` duplicate import
**File:** `fraud-service/fraud/scheduler.py:92`

```python
from .db import get_settings, update_last_run, save_report_blob  # line 5
...
from .db import get_settings, update_last_auto_run, save_report_blob  # line 92 ← duplicate, mid-function
```
There's a stale import duplicated mid-file after a refactor. While Python doesn't crash on this, it's a maintenance landmine and indicates the file was edited in a rush.

---

### BUG-05 — `nodes.py` uses `print()` for all debug output
**File:** `backend/chatbot/graph/nodes.py:71,81,180,222,228`

```python
print(f"✅ Using Ollama LLM: {model} at {base_url}")
print(f"🎯 User selected agent override: {selected}")
print(f"🧠 Detected final intent: {intent}")
```
Same issue as the scheduler — all operational telemetry is going to stdout via print. In a Docker container, stdout is fine for raw logs, but these are bypassing `logger` which is already set up and named. You lose the ability to filter by module, set DEBUG/INFO/WARNING levels, or ship to ELK/Grafana Loki.

---

### UX-01 — `FraudSettingsComponent.triggerNow()` calls `ngOnInit()` to refresh
**File:** `frontend/fraud-settings/fraud-settings.component.ts:99`

```typescript
setTimeout(() => this.ngOnInit(), 5000);
```
Calling `ngOnInit()` manually from a button handler is an Angular anti-pattern. `ngOnInit` is a lifecycle hook, not a general-purpose refresh method. The correct approach is to extract the load logic into a dedicated `loadSettings()` method and call that. Also, polling for 5 seconds is a guess — the backend analysis is async with no completion signal, so the user may see stale "running" status for much longer.

---

### UX-02 — Error responses from 403 in streaming are displayed as generic "Une erreur est survenue"
**File:** `frontend/chat/chat.component.ts:187-195`

```typescript
if (evt.error) {
  // ← always shows "Une erreur est survenue. Veuillez réessayer."
  // even if it's a role-based 403 that we just implemented
```
Now that we gate fraud/SQL agents, a client who manages to trigger the check (e.g. via API) will see a useless "try again" message. The SSE 403 payload has `{"error": "Accès refusé..."}` but the frontend ignores the error content.

**Fix:** Display `evt.error` content directly when it's a permission/server error.

---

## 🟡 MEDIUM (polish and correctness)

### ARCH-03 — `GET /settings` (fraud) is unauthenticated — fine for reading, but inconsistent
**File:** `fraud-service/main.py:86`

The GET settings endpoint is public — anyone can see the current fraud analysis schedule (frequency, time, day). This isn't a critical security issue, but it's inconsistent with the rest of the protected surface and leaks operational information. Consider at least a `require_bank_agent` on GET as well.

---

### ARCH-04 — `selectedAgent` resets to "auto" for clients only visually — not enforced on send
**File:** `frontend/chat/chat.component.ts:177`

```typescript
const agent = this.selectedAgent() === 'auto' ? undefined : this.selectedAgent();
```
If a client somehow gets `selectedAgent = 'fraud'` (browser DevTools, URL manipulation of signals), the frontend will happily send `selected_agent: "fraud"` to the backend. The backend DOES check this — but the frontend should also reset the selected agent to 'auto' when `isBankAgent` is false, rather than only hiding the option.

---

### ARCH-05 — No `text-to-sql` service exists but the code references it everywhere
**File:** `nodes.py:560`, `app.routes.ts`

```python
resp = httpx.post(
    f"{os.getenv('TEXT2SQL_SERVICE_URL', 'http://text-to-sql-service:8003')}/query",
```
The SQL agent in `stream_agent_response` makes HTTP requests to a non-existent service (`text-to-sql-service:8003`). There's no container, no Dockerfile, no mention in docker-compose. Selecting "SQL to Text Agent" will always return `"❌ Erreur service SQL : ..."`. It's a broken feature. Either build the service or remove the option.

---

### BUG-06 — `IsAdmin` permission reads `resource_access.realm-management.roles` — complex and fragile
**File:** `backend/chatbot/auth/permissions.py:80-103`

The `IsAdmin` check reads from `realm_access`, `resource_access.realm-management`, AND a flat `roles` list — three different locations. The new `IsBankAgent` check only reads from the flat `roles` list. These are inconsistent. The flat `roles` list that `IsBankAgent` uses is populated by `KeycloakAuthentication` from realm roles — which is correct. But `IsAdmin` also checks `resource_access.realm-management` which is Keycloak's internal management roles — this means a Keycloak admin console user automatically has `IsAdmin` even if they don't have your custom `admin` realm role. This is a privilege confusion bug.

---

### BUG-07 — `ConversationListView` returns ALL conversations without user_id filter by default
**File:** `backend/chatbot/views.py:146`

```python
qs = Conversation.objects.all()
if user_id:
    qs = qs.filter(user_id=user_id)
```
If `user_id` is not provided in the query params, this returns every single conversation from every user in the system. Even with auth, a bank_agent could retrieve conversations of all clients.

**Fix:** If authenticated, `user_id` should default to the current user's ID from the JWT.

---

### BUG-08 — Memory manager calls LLM to summarize on EVERY non-cached request
**File:** `backend/chatbot/memory_manager.py:410-416`

When Redis cache misses (e.g. after restart), the `_get_or_build_summary` function generates a new summary via LLM for every single message. This adds a full LLM round-trip (Groq API call) before the actual user response. For high concurrency, this doubles your API cost silently.

---

### BUG-09 — `fraud_agent` node (non-streaming) doesn't stream — it blocks the Django thread for up to 120s
**File:** `backend/chatbot/views.py:125`

```python
result = bank_graph.invoke(initial_state)  # ← synchronous, blocking
```
`ChatView.post()` is a synchronous Django view that blocks the WSGI worker for the full duration of a fraud analysis (can be 2-3 minutes with a slow LLM + fraud-service). Under any real load, this exhausts the thread pool.

---

### QUALITY-01 — System prompts are duplicated inline as one massive string
**File:** `nodes.py:89-135`

The "Response policy" and "Response Layout Rules" sections are **copy-pasted identically** into every single agent system prompt (account, transfer, support, fallback, fraud). This is ~8 lines repeated 5 times = 40 lines of duplication. Any change requires editing 5 places.

**Fix:** Extract to `BASE_RESPONSE_POLICY = "..."` and f-string each agent prompt.

---

### QUALITY-02 — IBAN regex is duplicated across 2 services
**File:** `fraud-service/main.py:125` AND `backend/chatbot/graph/nodes.py:48`

The exact same IBAN regex pattern is copy-pasted between the fraud-service and the backend orchestrator. They've already diverged slightly. If one gets fixed, the other won't.

---

### QUALITY-03 — `nginx.conf` has a misplaced comment
**File:** `api-gateway/nginx.conf:175-181`

```nginx
# ── STATIC RESOURCES KEYCLOAK — Fallback pour anciennes URLs ──
# Gère les requêtes vers /resources/* qui devraient être /auth/resources/*
...
location /mail/ {  # ← This is the mail service, not Keycloak static resources
```
The comment block for "Keycloak static resources fallback" is immediately followed by the `/mail/` location block with no separator. The comment describes what comes AFTER it (`/resources/` at line 195) but is separated from it by `/mail/`. Misleading for anyone reading the config.

---

### QUALITY-04 — `StreamChatView` hardcodes the CORS origin
**File:** `backend/chatbot/views.py:285`

```python
response['Access-Control-Allow-Origin'] = 'http://localhost:4200'
```
This is hardcoded to localhost:4200 (dev Angular server). In Docker's production mode, the frontend is served from the Nginx gateway on port 80 — this CORS header breaks production streaming because the origin is `http://localhost` not `http://localhost:4200`.

**Fix:** Read from an env var: `os.getenv("CORS_ALLOWED_ORIGIN", "http://localhost:4200")`.

---

## 🟢 LOW (nice-to-have polish)

### UX-03 — No loading state on agent selector change
The user can switch agents mid-conversation while `sending()` is true — the `[disabled]="sending()"` on the select is good, but there's no visual indicator of which agent is currently responding (aside from the tiny `agent_used` badge that appears after the response).

---

### UX-04 — `fraud-settings.component.ts` has hardcoded French day names
```typescript
daysOfWeek = [
  { value: 1, label: 'Lundi' },
```
The rest of the UI uses English for technical labels but French for day names. Pick a language and stick with it. The agent backend uses French/English inconsistently too (`"message requis"`, `"Accès refusé"` mixed with `"fraud analysis"`, `"Access denied"`).

---

### UX-05 — The `/unauthorized` page doesn't show the page that was attempted
When a client hits `/fraud-settings` and gets redirected to `/unauthorized`, they don't know WHICH page they were denied. A `returnUrl` query param would improve UX significantly.

---

### UX-06 — New chat button doesn't reset `selectedAgent` to 'auto'
**File:** `chat.component.ts:130`
```typescript
newChat(): void {
    this.sessionId.set(uuidv4());
    this.messages.set([]);
    this.activeSession.set('');
    // ← selectedAgent stays at whatever it was before
```
Starting a new chat with a fraud agent selected will silently keep that agent for the next conversation.

---

### QUALITY-05 — `package-lock.json` at root is nearly empty (94 bytes)
**File:** `package-lock.json`
There's a 94-byte `package-lock.json` at the project root that's either a stale artifact or placeholder. It's being tracked by git and will confuse anyone who runs `npm install` at the root.

---

### QUALITY-06 — `debug_csv.py` and `diagnostic.sh` are committed to the repo root
**File:** Root directory
Debug and diagnostic scripts intended for development are checked into the repo root. These should either be moved to a `scripts/` or `tools/` directory, or added to `.gitignore`. An Excel fraud report (`backend/data/reports/master_fraud_report_20260421_200029.xlsx`) was also committed to git — real or synthetic transaction data should never be in version control.

---

### INFRA-01 — Keycloak runs in `start-dev` mode
**File:** `docker-compose.yml:72`
```yaml
command: start-dev
```
Keycloak's `start-dev` mode disables HTTPS, uses an H2 in-memory DB fallback, and enables relaxed security settings. In this deployment it's backed by PostgreSQL which is fine, but `start-dev` carries other implicit dev-mode behaviors. The correct command for containerized deployments is `start` with explicit config.

---

### INFRA-02 — No rate limiting anywhere
Neither Nginx nor the Django/FastAPI layers have any rate limiting. The `/api/v1/chatbot/stream/` endpoint calls a paid Groq API on every request — there's nothing stopping a client from flooding it with requests.

---

## Summary Table

| ID | Area | Priority | Description |
|---|---|---|---|
| SEC-01 | Fraud API | 🔴 CRITICAL | `/analyze` is unauthenticated |
| SEC-02 | Fraud API | 🔴 CRITICAL | `/reports` endpoints unauthenticated |
| SEC-03 | Fraud API | 🔴 CRITICAL | `/health` leaks internal paths |
| SEC-04 | Backend | 🔴 CRITICAL | JWT errors silently swallowed |
| SEC-05 | Backend | 🔴 CRITICAL | `import jwt` missing — streaming role check crashes |

| BUG-01 | Backend | 🔴 CRITICAL | `user_id`/`session_id` hardcoded in streaming fraud path |

| ARCH-01 | Backend | 🟠 HIGH | 400+ lines of duplicated fraud analysis logic |
| ARCH-02 | Backend | 🟠 HIGH | ConversationListView/DetailView have zero auth |

| BUG-02 | Backend | 🟠 HIGH | ChatView missing explicit auth classes |
| BUG-03 | Fraud | 🟠 HIGH | Scheduler uses `print()` instead of `logger` |
| BUG-04 | Fraud | 🟠 HIGH | Duplicate import mid-file in scheduler |
| BUG-05 | Backend | 🟠 HIGH | nodes.py uses `print()` for all telemetry |
| UX-01 | Frontend | 🟠 HIGH | `ngOnInit()` called manually as refresh |
| UX-02 | Frontend | 🟠 HIGH | 403 role errors show generic message |
| ARCH-03 | Fraud | 🟡 MEDIUM | GET /settings unauthenticated |
| ARCH-04 | Frontend | 🟡 MEDIUM | selectedAgent not reset for clients on send |
| ARCH-05 | Backend | 🟡 MEDIUM | SQL agent references non-existent service |
| BUG-06 | Backend | 🟡 MEDIUM | IsAdmin reads Keycloak internal roles (privilege confusion) |
| BUG-07 | Backend | 🟡 MEDIUM | ConversationList returns all conversations if no user_id |
| BUG-08 | Backend | 🟡 MEDIUM | LLM called twice per message on cache miss |
| BUG-09 | Backend | 🟡 MEDIUM | ChatView blocks Django thread up to 120s |
| QUALITY-01 | Backend | 🟡 MEDIUM | System prompts duplicated 5× |
| QUALITY-02 | Both | 🟡 MEDIUM | IBAN regex duplicated across 2 services |
| QUALITY-03 | Infra | 🟡 MEDIUM | Misleading nginx.conf comment |
| QUALITY-04 | Backend | 🟡 MEDIUM | CORS origin hardcoded in StreamChatView |
| UX-03 | Frontend | 🟢 LOW | No visual indicator of active responding agent |
| UX-04 | Frontend | 🟢 LOW | Mixed French/English throughout |
| UX-05 | Frontend | 🟢 LOW | /unauthorized page missing returnUrl |
| UX-06 | Frontend | 🟢 LOW | newChat() doesn't reset selectedAgent |
| QUALITY-05 | Infra | 🟢 LOW | Stale empty package-lock.json at root |
| QUALITY-06 | Infra | 🟢 LOW | Debug scripts + Excel report committed to git |
| INFRA-01 | Infra | 🟢 LOW | Keycloak running in start-dev mode |
| INFRA-02 | Infra | 🟢 LOW | No rate limiting on any endpoint |
