import os
import re
import httpx
import json
import logging
from langchain_groq import ChatGroq
from langchain_ollama import ChatOllama
from langchain_core.messages import SystemMessage, AIMessage, HumanMessage
from .state import BankChatState

logger = logging.getLogger(__name__)

FRAUD_SERVICE_URL = os.getenv("FRAUD_SERVICE_URL", "http://fraud-service:8001")
MAIL_SERVICE_URL  = os.getenv("MAIL_SERVICE_URL",  "http://mail-service:8002")
ALERT_EMAIL       = os.getenv("ALERT_EMAIL",        "compliance@yourbank.com")

# ── MAIL_AGENT_SYSTEM ─────────────────────────────────────────────────────────
# Utilisé UNIQUEMENT pour composer le sujet et le contexte du mail.
# La décision d'envoyer est déterministe (pas de LLM pour ça).
MAIL_AGENT_SYSTEM = """\
You are a banking compliance mail agent. Your only job is to compose email metadata.

You MUST respond with ONLY a valid JSON object — no markdown, no explanation, no backticks.
The JSON must start with { and end with }.

Response format (strictly):
{
  "subject": "concise subject line in French",
  "template": "fraud_alert" | "critical_alert",
  "context": {
    "iban": "...",
    "score_final": 0,
    "risk_level": "...",
    "tracfin_required": false,
    "llm_summary": "...",
    "download_url": "...",
    "triggered_count": 0
  }
}

Rules for template selection:
- score_final >= 80 OR tracfin_required = true → use "critical_alert"
- score_final >= 50 AND score_final < 80       → use "fraud_alert"
"""

# ── IBAN extraction helper ────────────────────────────────────────────────────

IBAN_PATTERN = re.compile(
    r"\b([A-Z]{2}\d{2}[\s]?[\dA-Z]{4}[\s]?[\dA-Z]{4}[\s]?[\dA-Z]{4}[\s]?[\dA-Z]{0,16})\b"
    r"|"
    r"\b(IBAN_[A-Z]{2}\d+)\b",
    re.IGNORECASE,
)

def extract_iban(messages: list) -> str:
    for msg in reversed(messages):
        content = msg.content if hasattr(msg, "content") else str(msg)
        match = IBAN_PATTERN.search(content)
        if match:
            return (match.group(1) or match.group(2) or "").replace(" ", "").upper()
    return ""


# ── Configuration du LLM ─────────────────────────────────────────────────────

def get_llm():
    provider = os.getenv("LLM_PROVIDER", "groq").lower()
    if provider == "ollama":
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        model    = os.getenv("OLLAMA_MODEL", "llama3.2")
        print(f"✅ Using Ollama LLM: {model} at {base_url}")
        return ChatOllama(base_url=base_url, model=model, temperature=0.7)
    else:
        api_key = os.getenv("GROQ_API_KEY")
        model   = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
        if not api_key:
            raise ValueError(
                "GROQ_API_KEY is required when LLM_PROVIDER=groq. "
                "Set it in your .env file or switch to LLM_PROVIDER=ollama"
            )
        print(f"✅ Using Groq LLM: {model}")
        return ChatGroq(model=model, api_key=api_key, temperature=0.7)


llm = get_llm()

# ── System prompts ────────────────────────────────────────────────────────────

SYSTEM_PROMPTS = {
    "account_agent": (
        "You are BankChat, a specialized banking assistant for account inquiries. "
        "You help customers with: account balances, transaction history, account statements, "
        "account details, interest rates, fees, credit limits and savings plans. "
        "Be professional, precise and concise. Never ask for passwords or PINs. "
        "If real account data is needed, explain that the customer must log in to the secure portal."
        "Response policy: - Match answer length to the complexity of the user's request. - Use the minimum words necessary to fully answer. - Start with a direct answer first. - Prefer 1–3 sentences for simple questions. - Use bullets for multi-step explanations. - Avoid repetition, filler, and unnecessary context. - Keep responses under 120 words unless the user asks for more detail. - Expand only when clarification improves usefulness."
        "Response Layout Rules: - Start with a direct answer - Keep paragraphs short (1–3 lines) - Use headings for long answers - Use bullets for lists and steps - Use numbered lists for processes - Separate sections with blank lines - Prioritize readability and scanability - Avoid dense text blocks"
    ),
    "transfer_agent": (
        "You are BankChat, a specialized banking assistant for money transfers and payments. "
        "You help customers with: wire transfers, internal transfers between accounts, "
        "payment scheduling, beneficiary management, transfer limits, SWIFT/IBAN/BIC details, "
        "international fees and currency conversion. "
        "Always stress the importance of verifying recipient details before confirming a transfer."
        "Response policy: - Match answer length to the complexity of the user's request. - Use the minimum words necessary to fully answer. - Start with a direct answer first. - Prefer 1–3 sentences for simple questions. - Use bullets for multi-step explanations. - Avoid repetition, filler, and unnecessary context. - Keep responses under 120 words unless the user asks for more detail. - Expand only when clarification improves usefulness."
        "Response Layout Rules: - Start with a direct answer - Keep paragraphs short (1–3 lines) - Use headings for long answers - Use bullets for lists and steps - Use numbered lists for processes - Separate sections with blank lines - Prioritize readability and scanability - Avoid dense text blocks"
    ),
    "support_agent": (
        "You are BankChat, a specialized banking customer support agent. "
        "You assist with: card blocking and unblocking, fraud alerts and dispute filing, "
        "complaints and escalations, technical issues with online banking, "
        "account opening procedures, loan and mortgage inquiries, and product information. "
        "Be empathetic, patient and always offer a clear next step."
        "Response policy: - Match answer length to the complexity of the user's request. - Use the minimum words necessary to fully answer. - Start with a direct answer first. - Prefer 1–3 sentences for simple questions. - Use bullets for multi-step explanations. - Avoid repetition, filler, and unnecessary context. - Keep responses under 120 words unless the user asks for more detail. - Expand only when clarification improves usefulness."
        "Response Layout Rules: - Start with a direct answer - Keep paragraphs short (1–3 lines) - Use headings for long answers - Use bullets for lists and steps - Use numbered lists for processes - Separate sections with blank lines - Prioritize readability and scanability - Avoid dense text blocks"
    ),
    "fallback": (
        "You are BankChat, a professional AI banking assistant for a modern retail bank. "
        "Answer banking-related questions clearly, concisely and professionally. "
        "You can help with accounts, transfers, cards, loans, investments and general banking advice. "
        "If a question is completely unrelated to banking or finance, politely let the customer know "
        "you are specialized in banking services and redirect them appropriately."
        "Response policy: - Match answer length to the complexity of the user's request. - Use the minimum words necessary to fully answer. - Start with a direct answer first. - Prefer 1–3 sentences for simple questions. - Use bullets for multi-step explanations. - Avoid repetition, filler, and unnecessary context. - Keep responses under 120 words unless the user asks for more detail. - Expand only when clarification improves usefulness."
        "Response Layout Rules: - Start with a direct answer - Keep paragraphs short (1–3 lines) - Use headings for long answers - Use bullets for lists and steps - Use numbered lists for processes - Separate sections with blank lines - Prioritize readability and scanability - Avoid dense text blocks"
    ),
    "fraud_agent": (
        "You are BankChat, a specialized expert in banking security and fraud detection. "
        "Your role is to help users identify potential scams, explain security measures, "
        "and provide guidance on how to stay safe. "
        "You can also perform technical analysis on IBANs or transactions if requested. "
        "Be alarming but professional when a potential risk is detected, and always provide "
        "clear, actionable security advice."
        "Response policy: - Match answer length to the complexity of the user's request. - Use the minimum words necessary to fully answer. - Start with a direct answer first. - Prefer 1–3 sentences for simple questions. - Use bullets for multi-step explanations. - Avoid repetition, filler, and unnecessary context. - Keep responses under 120 words unless the user asks for more detail. - Expand only when clarification improves usefulness."
        "Response Layout Rules: - Start with a direct answer - Keep paragraphs short (1–3 lines) - Use headings for long answers - Use bullets for lists and steps - Use numbered lists for processes - Separate sections with blank lines - Prioritize readability and scanability - Avoid dense text blocks"
    ),
}


# ── Helper : appel au mail-service ───────────────────────────────────────────

def call_mail_service(payload: dict) -> dict:
    """Appel HTTP au mail-service. Log l'erreur sans faire planter le graph."""
    try:
        r = httpx.post(f"{MAIL_SERVICE_URL}/send", json=payload, timeout=15.0)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.warning(f"[mail] Mail service unreachable or error: {e}")
        return {"status": "error", "detail": str(e)}


# ── Helper : extraction JSON robuste ─────────────────────────────────────────

def _extract_json(text: str) -> dict | None:
    """
    Extrait le premier objet JSON valide d'une chaîne, même si le LLM
    a ajouté du texte avant/après les accolades.
    """
    text  = text.replace("```json", "").replace("```", "").strip()
    start = text.find("{")
    end   = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


# ── Intent detection ──────────────────────────────────────────────────────────

def detect_intent(state: BankChatState) -> BankChatState:
    # ── Règle 0 : Override par l'utilisateur ───────────────────────────────────
    selected = state.get("selected_agent")
    if selected and selected not in ("orchestrator", "auto"):
        if selected == "sql":
            selected = "text_to_sql"
        valid_intents = ("account", "transfer", "support", "fraud", "text_to_sql")
        if selected in valid_intents:
            print(f"🎯 User selected agent override: {selected}")
            return {**state, "intent": selected}

    last_msg  = state["messages"][-1].content
    msg_lower = last_msg.lower()
    IBAN_PAT  = re.compile(r'\b(IBAN_\w+|[A-Z]{2}\d{2}[\w\s]{10,30})\b', re.IGNORECASE)

    prompt = (
        "You are a strict banking intent classifier. "
        "Reply with EXACTLY one word, nothing else, no punctuation.\n\n"
        "Rules:\n"
        "- account   → balance, statement, account info\n"
        "- transfer  → send money, wire transfer, payment to someone\n"
        "- support   → card blocked, complaint, technical problem, help, asking what fraud is, learning about scams, requesting advice against dangers\n"
        "- fraud     → explicitly reporting a fraudulent transaction, requesting IBAN analysis, anomaly detection request, AML or Tracfin checks\n"
        "- fallback  → anything else\n\n"
        f"Message: {last_msg}\n\n"
        "Your answer (one word only):"
    )

    try:
        response = llm.invoke(prompt)
        intent   = response.content.strip().lower().split()[0]
    except Exception as e:
        print(f"⚠️ LLM intent fallback triggered due to error: {e}")
        intent = "fallback"

    if intent not in ("account", "transfer", "support", "fraud"):
        intent = "fallback"

    FRAUD_ACTION_KEYWORDS = [
        "analyse", "vérifie", "verif", "check", "detect",
        "export", "évaluer", "scan", "tester", "envoie", "envoyer", "mail", "email"
    ]
    FRAUD_KEYWORDS = [
        "fraude", "fraud", "anomalie", "anomal", "suspect",
        "iban_", "blanchiment", "aml", "tracfin", "risque", "arnaque", "vol"
    ]

    has_iban = bool(IBAN_PAT.search(last_msg))

    if intent != "fraud" and has_iban and any(kw in msg_lower for kw in FRAUD_KEYWORDS + FRAUD_ACTION_KEYWORDS):
        print(f"🧠 Detected intent override: fraud (IBAN + keyword match from: '{last_msg[:80]}')")
        intent = "fraud"
    elif intent != "fraud" and len(msg_lower.split()) <= 4 and any(kw in msg_lower for kw in FRAUD_KEYWORDS):
        print(f"🧠 Detected intent override: fraud (short keyword phrase from: '{last_msg[:80]}')")
        intent = "fraud"

    print(f"🧠 Detected final intent: {intent} (from: '{last_msg[:80]}...')")
    return {**state, "intent": intent}


def route_to_agent(state: BankChatState) -> str:
    return {
        "account":     "account_agent",
        "transfer":    "transfer_agent",
        "support":     "support_agent",
        "fraud":       "fraud_agent",
        "text_to_sql": "text_to_sql_agent",
        "sql":         "text_to_sql_agent",
    }.get(state["intent"], "fallback")


# ── Agent nodes ───────────────────────────────────────────────────────────────

def _run_agent(state: BankChatState, agent_key: str) -> BankChatState:
    system = SystemMessage(content=SYSTEM_PROMPTS[agent_key])
    messages_with_system = [system] + list(state["messages"])
    response = llm.invoke(messages_with_system)
    return {
        **state,
        "messages": [AIMessage(content=response.content)],
        "agent":    agent_key,
    }

def account_agent(state: BankChatState)  -> BankChatState: return _run_agent(state, "account_agent")
def transfer_agent(state: BankChatState) -> BankChatState: return _run_agent(state, "transfer_agent")
def support_agent(state: BankChatState)  -> BankChatState: return _run_agent(state, "support_agent")
def handle_fallback(state: BankChatState)-> BankChatState: return _run_agent(state, "fallback")


def fraud_agent(state: BankChatState) -> BankChatState:
    """
    Smart Fraud Agent : ANALYZE (appel HTTP fraud-service) ou TALK (expert LLM).
    En path ANALYZE, stocke le résultat brut dans state['context'] pour mail_agent.
    """
    last_msg = ""
    for msg in reversed(state["messages"]):
        if msg.__class__.__name__ == "HumanMessage":
            last_msg = msg.content
            break

    decision_prompt = (
        "You are a fraud detection reasoning engine. "
        "Based on the user's message, decide if we need to call a technical tool (ANALYZE) "
        "to check an IBAN/transaction, or if we should just respond as an expert (TALK).\n\n"
        f"Message: {last_msg}\n\n"
        "Rules:\n"
        "- ANALYZE: If there is an IBAN, a specific transaction to check, or a request for deep scan.\n"
        "- TALK: If it's a general question, a request for advice, or an explanation of concepts.\n\n"
        "Your answer must be in this format:\n"
        "REASONING: <brief explanation>\n"
        "DECISION: <ANALYZE or TALK>"
    )

    try:
        decision_resp = llm.invoke(decision_prompt).content
        lines     = decision_resp.strip().split("\n")
        reasoning = "Analyse de la requête..."
        decision  = "TALK"
        for line in lines:
            if line.upper().startswith("REASONING:"):
                reasoning = line.split(":", 1)[1].strip()
            if line.upper().startswith("DECISION:"):
                decision = "ANALYZE" if "ANALYZE" in line.upper() else "TALK"

        prefix = f"💡 *{reasoning}*\n\n---\n\n"

        if decision == "ANALYZE":
            iban = extract_iban(state["messages"])
            resp = httpx.post(
                f"{FRAUD_SERVICE_URL}/analyze",
                json={
                    "message":    last_msg,
                    "iban":       iban,
                    "action":     "fraud_check",
                    "user_id":    state.get("user_id", "anonymous"),
                    "session_id": state.get("session_id", ""),
                    "excel_path": "",
                },
                timeout=120.0,
            )
            resp.raise_for_status()
            result = resp.json()

            logger.info(
                f"[fraud_agent] ANALYZE done — IBAN={result.get('iban')} "
                f"score={result.get('score_final')} risk={result.get('risk_level')} "
                f"tracfin={result.get('tracfin_required')}"
            )

            ai_response = prefix + (
                result.get("llm_summary") or
                result.get("summary") or
                "Analyse terminée."
            )
            return {
                **state,
                "messages": [AIMessage(content=ai_response)],
                "agent":    "fraud_agent",
                "context":  result,   # ← mail_agent lira ce dict
            }
        else:
            system = SystemMessage(content=SYSTEM_PROMPTS["fraud_agent"])
            resp   = llm.invoke([system] + list(state["messages"]))
            return {
                **state,
                "messages": [AIMessage(content=prefix + resp.content)],
                "agent":    "fraud_agent",
                "context":  {},   # pas d'analyse → pas de mail
            }

    except Exception as e:
        logger.exception("[fraud_agent] Error")
        return {
            **state,
            "messages": [AIMessage(content=f"❌ Erreur agent fraude : {str(e)}")],
            "agent":    "fraud_agent",
            "context":  {},
        }


# ── Mail agent (agentique hybride) ────────────────────────────────────────────

def mail_agent(state: BankChatState) -> BankChatState:
    """
    Nœud agentique mail hybride :
      1. Décision DÉTERMINISTE sur seuils (score/TRACFIN) — fiable, sans LLM
      2. Composition du sujet/contexte via LLM — avec fallback robuste si parse échoue
      3. Appel HTTP au mail-service

    Appelé uniquement après fraud_agent path ANALYZE (context non vide via orchestrator).
    """
    context     = state.get("context", {})
    score_final = context.get("score_final", 0)
    tracfin     = context.get("tracfin_required", False)
    iban        = context.get("iban", "")

    # ── Étape 1 : Décision déterministe (PAS de LLM) ─────────────────────────
    logger.info(
        f"[mail_agent] Evaluating — IBAN={iban} score={score_final} "
        f"tracfin={tracfin} risk={context.get('risk_level')}"
    )

    should_send = score_final >= 50 or tracfin
    if not should_send:
        logger.info(f"[mail_agent] score={score_final} < 50 and tracfin=False — no mail.")
        return {**state, "agent": "mail_agent"}

    # Choix du template selon seuil
    template = "critical_alert" if (score_final >= 80 or tracfin) else "fraud_alert"

    # ── Fallback sujet + contexte (utilisé si LLM échoue) ────────────────────
    subject = (
        f"[BankChat] 🔴 Alerte critique score {score_final}/100 — "
        f"{context.get('risk_level', '')} — IBAN {iban}"
        if template == "critical_alert" else
        f"[BankChat] ⚠️ Alerte fraude score {score_final}/100 — IBAN {iban}"
    )
    mail_context = {
        "iban":             iban,
        "score_final":      score_final,
        "risk_level":       context.get("risk_level", ""),
        "tracfin_required": tracfin,
        "llm_summary":      context.get("llm_summary", ""),
        "download_url":     context.get("download_url", ""),
        "triggered_count":  len(context.get("fraud_results", [])),
    }

    # ── Étape 2 : LLM pour composer sujet + contexte (avec fallback) ─────────
    context_for_llm = json.dumps({
        **mail_context,
        "alert_email":  ALERT_EMAIL,
        "session_id":   state.get("session_id", ""),
        "user_id":      state.get("user_id", "anonymous"),
    }, ensure_ascii=False)

    try:
        llm_resp = llm.invoke([
            SystemMessage(content=MAIL_AGENT_SYSTEM),
            HumanMessage(content=f"Fraud analysis result:\n{context_for_llm}"),
        ])
        parsed = _extract_json(llm_resp.content)
        if parsed:
            subject      = parsed.get("subject", subject)
            template     = parsed.get("template", template)
            mail_context = parsed.get("context", mail_context)
            logger.info(f"[mail_agent] LLM composed — template={template} subject={subject}")
        else:
            logger.warning(
                f"[mail_agent] LLM returned unparseable content, using fallback. "
                f"Raw (200): {llm_resp.content[:200]}"
            )
    except Exception as e:
        logger.warning(f"[mail_agent] LLM composition failed, using fallback: {e}")

    # ── Étape 3 : Envoi via mail-service ─────────────────────────────────────
    payload = {
        "to":              ALERT_EMAIL,
        "subject":         subject,
        "template":        template,
        "context":         mail_context,
        "attachment_path": context.get("report_path") or None,
    }

    logger.info(f"[mail_agent] → Calling mail-service: to={ALERT_EMAIL} subject={subject}")
    result = call_mail_service(payload)
    status = result.get("status", "error")

    if status == "sent":
        logger.info(f"[mail_agent] ✅ Email sent → {ALERT_EMAIL}")
    else:
        logger.error(f"[mail_agent] ❌ Email FAILED: {result.get('detail')}")

    updated_context = {
        **context,
        "mail_results": [{"to": ALERT_EMAIL, "subject": subject, "status": status}]
    }
    return {
        **state,
        "agent":   "mail_agent",
        "context": updated_context,
    }


# ── Streaming helper ──────────────────────────────────────────────────────────



def stream_agent_response(intent: str, messages: list):
    """
    Yields (token, agent_key) tuples — ou (token, agent_key, fraud_result) pour fraud ANALYZE.

    CORRECTION : en path ANALYZE, yield un 3e élément (le dict résultat fraude brut)
    sur le dernier yield. views.py le récupère pour appeler mail_agent ensuite.
    """
    if intent == "fraud":
        last_msg = ""
        for msg in reversed(messages):
            if msg.__class__.__name__ == "HumanMessage":
                last_msg = msg.content
                break

        decision_prompt = (
            "You are a fraud detection reasoning engine. "
            "Based on the user's message, decide if we need to call a technical tool (ANALYZE) "
            "to check an IBAN/transaction, or if we should just respond as an expert (TALK).\n\n"
            f"Message: {last_msg}\n\n"
            "Rules:\n"
            "- ANALYZE: If there is an IBAN, a specific transaction to check, or a request for deep scan.\n"
            "- TALK: If it's a general question, a request for advice, or an explanation of concepts.\n\n"
            "Your answer must be in this format:\n"
            "REASONING: <brief explanation in French>\n"
            "DECISION: <ANALYZE or TALK>"
        )

        try:
            decision_resp = llm.invoke(decision_prompt).content
            lines     = decision_resp.strip().split("\n")
            reasoning = "Analyse de la requête..."
            decision  = "TALK"
            for line in lines:
                if line.upper().startswith("REASONING:"):
                    reasoning = line.split(":", 1)[1].strip()
                if line.upper().startswith("DECISION:"):
                    decision = "ANALYZE" if "ANALYZE" in line.upper() else "TALK"

            yield f"💡 *{reasoning}*\n\n---\n\n", "fraud_agent"

            if decision == "ANALYZE":
                iban = extract_iban(messages)
                resp = httpx.post(
                    f"{FRAUD_SERVICE_URL}/analyze",
                    json={
                        "message":    last_msg,
                        "iban":       iban,
                        "action":     "fraud_check",
                        "user_id":    "anonymous",
                        "session_id": "",
                        "excel_path": "",
                    },
                    timeout=120.0,
                )
                resp.raise_for_status()
                result  = resp.json()
                summary = result.get("llm_summary", result.get("summary", "Analyse de fraude terminée."))

                # ── CORRECTION : yield le résultat brut en 3e élément ──────────
                # views.py (StreamChatView) le récupère avec :
                #   for token, agent_key, *extra in stream_agent_response(...)
                # et appelle mail_agent(state_with_context=result) ensuite.
                yield summary, "fraud_agent", result

            else:
                # Path TALK — pas de résultat fraude brut
                system = SystemMessage(content=SYSTEM_PROMPTS["fraud_agent"])
                for chunk in llm.stream([system] + list(messages)):
                    token = chunk.content
                    if token:
                        yield token, "fraud_agent"

        except Exception as e:
            yield f"❌ Erreur lors de la décision : {str(e)}", "fraud_agent"
        return

    # ── Agents classiques ─────────────────────────────────────────────────────
    agent_key_map = {
        "account":     "account_agent",
        "transfer":    "transfer_agent",
        "support":     "support_agent",
        "text_to_sql": "text_to_sql_agent",
        "sql":         "text_to_sql_agent",
    }
    agent_key = agent_key_map.get(intent, "fallback")

    if agent_key == "text_to_sql_agent":
        last_user_msg = ""
        for msg in reversed(messages):
            if msg.__class__.__name__ == "HumanMessage":
                last_user_msg = msg.content
                break
        try:
            resp = httpx.post(
                f"{os.getenv('TEXT2SQL_SERVICE_URL', 'http://text-to-sql-service:8003')}/query",
                json={"question": last_user_msg, "user_id": "anonymous"},
                timeout=60.0,
            )
            resp.raise_for_status()
            result = resp.json()
            yield result.get("explanation", "Query executed."), "text2sql_agent"
        except Exception as e:
            yield f"❌ Erreur service SQL : {str(e)}", "text2sql_agent"
        return

    system = SystemMessage(content=SYSTEM_PROMPTS.get(agent_key, SYSTEM_PROMPTS["fallback"]))
    for chunk in llm.stream([system] + list(messages)):
        token = chunk.content
        if token:
            yield token, agent_key