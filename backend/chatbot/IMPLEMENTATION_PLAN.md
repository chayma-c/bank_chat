# Support Agent Implementation Plan

**Status:** ✅ READY FOR DEVELOPMENT  
**Date:** 2026-06-12  
**Scope:** Add controlled knowledge base to support_agent to reduce hallucinations  
**Retrieval Strategy:** Keyword Matching + Fuzzy Scoring (Phase 1 → LLM Re-ranking in Phase 2)

---

## Overview

Transform the support agent from a generic LLM responder into a **knowledge-bounded agent** that:
- Uses only curated VirtuBank knowledge
- Explicitly rejects account-specific data requests
- Strictly enforces topic scope
- Provides clear fallback guidance

**Retrieval Method (Phase 1):**
- Extract keywords from user query (remove stop words)
- Fuzzy match against FAQ keywords using Levenshtein distance (`fuzzywuzzy`)
- Score each match (0–100 similarity, normalized 0–1)
- Return top-3 matches with scores > 50%
- **Performance:** ~10ms per query
- **Future:** Add LLM re-ranking in Phase 2 if accuracy improves needed

---

## Phase 1: Create Support Knowledge Base

### 1.1 File: `backend/chatbot/support_knowledge.json`

**Purpose:** Centralized, curated data for support queries

**Structure:**
```json
{
  "meta": {
    "version": "1.0",
    "bank_name": "VirtuBank",
    "last_updated": "2026-06-12"
  },
  "bank_identity": {
    "name": "VirtuBank",
    "support_phone": "+33 (0) 800 123 456",
    "support_email": "support@virtubank.example",
    "business_hours": "Mon–Fri 08:00–18:00 CET",
    "disclaimer": "This is a simulated banking environment for training purposes only."
  },
  "products": {
    "current_account": { ... },
    "savings_account": { ... },
    "debit_card": { ... },
    "overdraft": { ... },
    "domestic_transfers": { ... },
    "international_transfers": { ... },
    "mobile_app": { ... }
  },
  "faq": [
    {
      "id": "lost_card",
      "category": "Card Management",
      "keywords": ["lost card", "stolen", "blocked card", "card issue"],
      "question_template": "What should I do if my card is lost or stolen?",
      "answer": "Immediately block your card in the mobile app (instant). If you cannot access the app, call support at +33 (0) 800 123 456...",
      "scope": "card_management",
      "account_specific": false
    },
    ...
  ],
  "scope_rules": {
    "in_scope": ["card_management", "password_security", "transfer_limits", "product_info", "account_opening", "account_closure", "fees", "fraud_protection"],
    "out_of_scope": ["account_balance", "transaction_details", "personal_account_data", "real_world_banking", "investments", "taxes", "external_topics"]
  },
  "rejection_messages": {
    "account_specific": "I don't have access to your account details. To check your balance or transaction history, please use the VirtuBank mobile app or visit our website. Is there something else I can help with?",
    "out_of_scope": "I'm here to help with VirtuBank products and services. I can't assist with that topic. For other questions, please contact support at +33 (0) 800 123 456.",
    "personal_data": "For security reasons, I'll never ask for or discuss your password, PIN, or card number. Please contact support if you have security concerns.",
    "fallback": "I don't have information about that. Please contact our support team at +33 (0) 800 123 456 or email support@virtubank.example. We're available Mon–Fri, 08:00–18:00 CET."
  }
}
```

**Content:** See separate `support_knowledge.json` file creation below

---

## Phase 2: Build Support Knowledge Module

### 2.1 File: `backend/chatbot/support_knowledge.py`

**Purpose:** Load, search, and retrieve knowledge base data

**Key Functions:**

1. `load_knowledge_base()` → Dict
   - Load JSON from disk at startup
   - Cache in memory
   - Return parsed data

2. `extract_keywords(user_message: str) -> List[str]`
   - Tokenize user message
   - Extract lowercased keywords
   - Remove stop words (the, a, is, etc.)

3. `match_faq_entry(user_query: str, max_results: int = 3) -> List[Dict]`
   - **Retrieval method:** Keyword extraction + fuzzy matching
   - Extract keywords from user query (remove stop words: the, a, is, etc.)
   - Fuzzy match each keyword against FAQ keywords using Levenshtein distance
   - Score each FAQ entry by best keyword match (0–100, normalized 0–1)
   - Return top N matches with score > 0.5 (50% similarity), ordered descending
   - Library: `fuzzywuzzy.fuzz.partial_ratio()` for performance

4. `check_scope(user_message: str) -> Tuple[bool, str | None]`
   - Detect if message is in/out of scope
   - Return (is_valid, rejection_message) tuple
   - Keywords: "balance", "account number", "transaction", "pin" → out_of_scope
   - Keywords: "product info", "limits", "fees" → in_scope

5. `build_context(matched_entries: List[Dict], bank_identity: Dict) -> str`
   - Format matched FAQ entries into markdown
   - Include bank identity info
   - Wrap in "KNOWLEDGE BASE" markers for LLM
   - Limit to ~1500 tokens

6. `handle_no_match(reason: str) -> str`
   - Return appropriate rejection message from config
   - Clear, professional tone
   - Directs to support channel

**Usage Pattern:**
```python
kb = load_knowledge_base()
keywords = extract_keywords(user_message)
is_valid, rejection = check_scope(user_message)

if not is_valid:
    return rejection

matches = match_faq_entry(keywords)
if not matches:
    return handle_no_match("no_match")

context = build_context(matches, kb["bank_identity"])
return context  # Pass to LLM
```

---

## Phase 3: Update Support Agent Node

### 3.1 File: `backend/chatbot/graph/nodes.py`

**Current Code (Lines 414–416):**
```python
async def support_agent(state: BankChatState):   return await _run_agent(state, "support_agent")
async def transfer_agent(state: BankChatState):  return await _run_agent(state, "transfer_agent")
async def support_agent(state: BankChatState):   return await _run_agent(state, "support_agent")
```

**New Implementation:**

Replace the one-liner with:
```python
async def support_agent(state: BankChatState) -> BankChatState:
    """
    Support agent with knowledge-base enforcement.
    
    Ensures responses are:
    1. Not account-specific
    2. Within VirtuBank product scope
    3. Based only on curated knowledge
    """
    from .support_knowledge import (
        load_knowledge_base, extract_keywords, check_scope,
        match_faq_entry, build_context, handle_no_match
    )
    
    try:
        last_msg = state["messages"][-1].content if state["messages"] else ""
        
        # ── Step 1: Load knowledge base ──────────────────────────────────────
        kb = load_knowledge_base()
        
        # ── Step 2: Scope check (reject account-specific & out-of-scope) ──────
        is_valid, rejection = check_scope(last_msg)
        if not is_valid:
            logger.info(f"[support_agent] ❌ Scope check failed: {rejection[:50]}…")
            return {
                **state,
                "messages": [AIMessage(content=rejection)],
                "agent": "support_agent",
                "context": {"type": "scope_rejection"}
            }
        
        # ── Step 3: Extract keywords & match to FAQ ──────────────────────────
        keywords = extract_keywords(last_msg)
        matches = match_faq_entry(keywords, max_results=3)
        
        if not matches:
            logger.info(f"[support_agent] ⚠️ No FAQ matches found")
            fallback = handle_no_match("no_match")
            return {
                **state,
                "messages": [AIMessage(content=fallback)],
                "agent": "support_agent",
                "context": {"type": "no_match"}
            }
        
        # ── Step 4: Build context for LLM ────────────────────────────────────
        knowledge_context = build_context(matches, kb["bank_identity"])
        
        # ── Step 5: Call LLM with knowledge base as context ──────────────────
        system_msg = SystemMessage(content=SYSTEM_PROMPTS.get("support_agent") + f"\n\n{knowledge_context}")
        resp = await llm.ainvoke([system_msg] + list(state["messages"]))
        
        logger.info(f"[support_agent] ✅ Responded based on {len(matches)} KB matches")
        
        return {
            **state,
            "messages": [AIMessage(content=resp.content)],
            "agent": "support_agent",
            "context": {
                "type": "knowledge_based",
                "matches_used": len(matches),
                "keywords": keywords
            }
        }
        
    except Exception as e:
        logger.exception("[support_agent] Unexpected error")
        return {
            **state,
            "messages": [AIMessage(content=f"❌ Error: {str(e)[:100]}")],
            "agent": "support_agent",
            "context": {"type": "error"}
        }
```

**Key Changes:**
- Load knowledge base at start
- Validate scope before proceeding
- Extract keywords from user message
- Match to FAQ entries
- Build context-aware system prompt
- Pass knowledge to LLM as explicit constraint
- Log decision path for debugging

---

## Phase 4: Update Support Prompt

### 4.1 File: `backend/chatbot/graph/prompts.py`

**Current Code (Line 13):**
```python
"support_agent": "You are BankChat, a support specialist." + BASE_POLICY,
```

**New Implementation:**

Replace with:
```python
"support_agent": (
    "You are VirtuBank Support, a helpful assistant for VirtuBank customer questions.\n"
    "IMPORTANT CONSTRAINTS:\n"
    "1. You are KNOWLEDGE-BOUNDED: Answer ONLY using the VirtuBank knowledge base provided below.\n"
    "2. NEVER access or discuss customer account details (balance, transactions, personal info).\n"
    "3. If a question is outside your knowledge base or scope, politely redirect to support.\n"
    "4. NEVER invent policies, fees, or procedures not in your knowledge base.\n"
    "5. Do NOT ask for sensitive data (password, PIN, full card number).\n\n"
    "KNOWLEDGE BASE:\n"
    "---\n"
    "[Knowledge base content injected here by support_agent()]\n"
    "---\n\n"
    "If the customer asks about something outside this knowledge base, respond with:\n"
    "'I don't have information about that. Please contact support at +33 (0) 800 123 456 or email support@virtubank.example.'"
) + BASE_POLICY,
```

**Rationale:**
- Explicit scope boundaries for the LLM
- Knowledge base is embedded in system prompt
- Clear fallback behavior
- No hallucination opportunity

---

## Phase 5: Create Support Knowledge Data File

### 5.1 File: `backend/chatbot/support_knowledge.json`

**Location:** `backend/chatbot/support_knowledge.json`

**Content Structure:**
- `meta`: Version, timestamp, bank name
- `bank_identity`: Contact, hours, products, disclaimer
- `products`: Details for each financial product
- `faq`: ~20 Q&A entries with:
  - `id`: Unique identifier
  - `keywords`: Search terms
  - `category`: Topic grouping
  - `question`: Example user question
  - `answer`: Curated response
  - `scope`: Category (in/out)
  - `account_specific`: Boolean flag
- `scope_rules`: Lists of in/out scope topics
- `rejection_messages`: Pre-written rejection templates

**FAQ Topics to Cover (minimum 15):**
1. Lost/stolen card
2. Password reset / forgotten password
3. Transfer limits (domestic)
4. Transfer limits (international)
5. Card blocked / declined (reasons)
6. Mobile app crashes / SMS issues
7. Account opening requirements
8. Account closure process
9. Fee structure / charges
10. Savings account interest
11. Overdraft feature
12. Card replacement time
13. Transaction dispute process
14. Fraud protection / security
15. Business hours & contact info

---

## Phase 6: Add Tests

### 6.1 File: `backend/tests/test_support_agent.py`

**Test Cases:**

| Test | Input | Expected | Category |
|------|-------|----------|----------|
| `test_lost_card` | "My card is lost" | Card blocking guidance | in_scope ✅ |
| `test_password_reset` | "Forgot password" | Password reset steps | in_scope ✅ |
| `test_transfer_limits` | "What's the daily limit?" | €10k domestic / €5k intl | in_scope ✅ |
| `test_card_blocked` | "Why is my card declined?" | Troubleshooting steps | in_scope ✅ |
| `test_balance_query` | "What's my balance?" | Reject + redirect | out_of_scope ❌ |
| `test_account_number` | "What's my account number?" | Reject + redirect | account_specific ❌ |
| `test_transaction_history` | "Show my last 5 transactions" | Reject + redirect | account_specific ❌ |
| `test_investment_advice` | "Should I invest in crypto?" | Reject + redirect | out_of_scope ❌ |
| `test_unrelated_topic` | "What's the weather?" | Reject + redirect | out_of_scope ❌ |
| `test_no_match` | "Very obscure question…" | Fallback message | no_match |
| `test_security_warning` | "What's your password?" | Security reminder | security |

**Test Structure:**
```python
@pytest.mark.asyncio
async def test_lost_card():
    state = {
        "messages": [HumanMessage(content="My card is lost, what do I do?")],
        "user_id": "test_user",
        "session_id": "test_session",
        "auth_token": None,
        "intent": "support",
        "agent": "",
        "selected_agent": None,
        "context": {},
        "error": None,
    }
    result = await support_agent(state)
    assert "block" in result["messages"][0].content.lower()
    assert "3" in result["messages"][0].content  # 3 business days
    assert result["context"]["type"] == "knowledge_based"
```

---

## Phase 7: Integration Checklist

- [ ] `support_knowledge.json` created and populated
- [ ] `support_knowledge.py` module created with 6 functions
- [ ] `nodes.py` support_agent updated (async, KB-aware)
- [ ] `prompts.py` support_agent system prompt updated
- [ ] Tests written and passing
- [ ] Manual smoke test (via chatbot UI)
- [ ] Logs reviewed for scope violations
- [ ] Performance check (KB load time)

---

## File Dependency Graph

```
nodes.py (support_agent function)
  ├─ prompts.py (SYSTEM_PROMPTS["support_agent"])
  └─ support_knowledge.py (KB helpers)
      └─ support_knowledge.json (curated data)

tests/test_support_agent.py
  ├─ support_agent (from nodes)
  ├─ support_knowledge.py
  └─ support_knowledge.json
```

---

## Implementation Sequence

**Order of execution:**

1. **Create** `support_knowledge.json` (data layer)
2. **Create** `support_knowledge.py` (logic layer)
3. **Update** `prompts.py` (constraint enforcement)
4. **Update** `nodes.py` support_agent function (orchestration)
5. **Create** tests (validation)
6. **Manual test** via chatbot UI
7. **Review logs** and iterate

---

## Expected Outcomes

### Before Implementation
- Support agent: Generic LLM (can hallucinate)
- Example: "Your daily transfer limit is €25,000" (FALSE)

### After Implementation
- Support agent: Knowledge-bounded (can only use curated data)
- Example: "Your daily transfer limit is €10,000" (FROM KB) ✅
- Out-of-scope questions rejected cleanly

---

## Files to Create/Modify

| File | Action | Reason |
|------|--------|--------|
| `support_knowledge.json` | **CREATE** | Curated KB data with keywords |
| `support_knowledge.py` | **CREATE** | KB load + keyword extraction + fuzzy matching |
| `graph/prompts.py` | **MODIFY** | Tighten support_agent system prompt |
| `graph/nodes.py` | **MODIFY** | KB-aware support_agent with retrieval |
| `tests/test_support_agent.py` | **CREATE** | Validation suite (11 test cases) |
| `requirements.txt` | **UPDATE** | Add `fuzzywuzzy` + `python-Levenshtein` |

---

## Success Criteria

✅ **Support agent:**
- Rejects account-specific queries cleanly
- Rejects out-of-scope topics cleanly
- Provides answers ONLY from KB
- No hallucinated policies or fees
- Guides users to correct support channel

✅ **Tests:**
- All 11 test cases pass
- Coverage > 80% for support_agent path
- Manual UI smoke test passes

✅ **Performance:**
- KB load time: < 100ms
- Matching time: < 50ms per request
- Total latency overhead: < 200ms

---

## Ready to Proceed?

✅ **PLAN APPROVED** — Keyword + Fuzzy Matching approach

### Build Sequence (Ready to Execute Now):

1. Create `support_knowledge.json` — Comprehensive FAQ data with keywords for each entry
2. Create `support_knowledge.py` — Keyword extraction + fuzzy matching logic
3. Update `graph/prompts.py` — Tighten support_agent system prompt with KB constraint
4. Update `graph/nodes.py` — Integrate KB retrieval into support_agent function
5. Create `tests/test_support_agent.py` — 11 test cases (in-scope, out-of-scope, no-match)
6. Update `requirements.txt` — Add `fuzzywuzzy` and `python-Levenshtein`

### Success Criteria:
- ✅ All 11 tests pass
- ✅ Support agent rejects account-specific queries
- ✅ Support agent rejects out-of-scope topics
- ✅ Support agent returns KB-only answers
- ✅ Retrieval latency < 50ms

**→ READY TO BUILD**

