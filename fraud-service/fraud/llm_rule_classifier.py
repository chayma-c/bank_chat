"""
llm_rule_classifier.py — LLM-based intent classifier for fraud rules.

When _pick_evaluator() cannot match a rule trigger via keyword matching,
this module asks the LLM to classify the rule's semantic intent and map it
to one of the available evaluator keys.

Results are cached in-memory keyed by (rule.id, updated_at) so the LLM is
called at most once per rule version — not on every analysis run.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from langchain_core.messages import HumanMessage
from langchain_groq import ChatGroq

logger = logging.getLogger(__name__)

# ── Evaluator registry — descriptions used in the LLM prompt ─────────────────

EVALUATOR_DESCRIPTIONS: dict[str, str] = {
    "large_amount":       "Detects single transactions above a monetary threshold (e.g. amount > 5000 EUR).",
    "near_threshold":     "Detects amounts just below a reporting threshold (e.g. 9500–9999 EUR). AML structuring bands.",
    "suspicious_iban":    "Checks IBANs against blacklists, OFAC sanctions lists, or high-risk country prefixes (RU, IR, KP…).",
    "structuring":        "Detects many small deposits spread over time to stay below a threshold (smurfing, layering).",
    "night_transactions": "Detects transfers or payments during unusual hours, typically 00:00–05:00.",
    "foreign_ip":         "Detects logins or transactions from IPs in a foreign country, or geo-location far from home.",
    "high_risk_mcc":      "Detects high-risk merchant category codes (MCC) combined with suspicious amounts.",
    "balance_ratio":      "Detects transactions that drain a high percentage of the account balance (e.g. > 80%).",
    "repeated_alerts":    "Detects accounts that have triggered multiple fraud alerts within a recent time window.",
    "velocity":           "Detects abnormally high transaction frequency within a short time window (e.g. > 5 card tx/hour).",
    "new_beneficiary":    "Detects high-value immediate transfers sent to a newly registered beneficiary.",
    "dormant_account":    "Detects sudden activity on accounts that were inactive for a long period (e.g. > 90 days).",
    "cross_border":       "Detects transactions passing through multiple countries in a short window (geographic layering).",
}

VALID_KEYS = frozenset(EVALUATOR_DESCRIPTIONS)

# ── In-memory cache: (rule_id, updated_at_str) → evaluator_key ───────────────
_intent_cache: dict[tuple[str, str], str] = {}

_llm: Optional[ChatGroq] = None


def _get_llm() -> ChatGroq:
    global _llm
    if _llm is None:
        _llm = ChatGroq(
            model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
            temperature=0,
            groq_api_key=os.getenv("GROQ_API_KEY", ""),
            max_tokens=16,
        )
    return _llm


def _build_prompt(rule) -> str:
    evaluator_list = "\n".join(
        f"  - {key}: {desc}" for key, desc in EVALUATOR_DESCRIPTIONS.items()
    )
    return (
        "You are a fraud rule classifier for a banking anti-fraud system.\n"
        "Map the rule below to exactly ONE evaluator key from the list.\n\n"
        f"Available evaluators:\n{evaluator_list}\n\n"
        f"Rule name: {rule.name}\n"
        f"Domain: {rule.domain}\n"
        f"Trigger: {rule.trigger}\n"
        f"Trigger detail: {rule.trigger_detail or '(none)'}\n"
        f"Description: {(rule.description or '')[:300]}\n\n"
        "Reply with ONLY the evaluator key — one word, exactly as written above. No explanation."
    )


def classify_rule_intent(rule) -> Optional[str]:
    """
    Returns the evaluator key that best matches this rule's semantic intent,
    or None if the LLM call fails or returns an unrecognized key.

    Cached by (rule.id, rule.updated_at) — invalidated automatically when the
    rule is modified via the API (call invalidate_rule_cache on update/patch).
    """
    cache_key = (rule.id, str(getattr(rule, "updated_at", "")))

    if cache_key in _intent_cache:
        cached = _intent_cache[cache_key]
        logger.debug(f"[llm_classifier] Cache hit for rule {rule.id} → '{cached}'")
        return cached

    try:
        llm = _get_llm()
        response = llm.invoke([HumanMessage(content=_build_prompt(rule))])
        raw = response.content.strip().lower()
        # Accept first token only — model may add punctuation or a newline
        key = raw.split()[0].rstrip(".,;:")

        if key not in VALID_KEYS:
            logger.warning(
                f"[llm_classifier] Unknown key '{key}' returned for rule {rule.id} "
                f"(trigger: '{rule.trigger[:60]}'). Using generic fallback."
            )
            return None

        logger.info(
            f"[llm_classifier] Rule {rule.id} ('{rule.name}') classified → '{key}'"
        )
        _intent_cache[cache_key] = key
        return key

    except Exception as exc:
        logger.error(
            f"[llm_classifier] Classification failed for rule {rule.id}: {exc}"
        )
        return None


def invalidate_rule_cache(rule_id: str) -> None:
    """
    Remove cached classification for a rule.
    Call this whenever a rule's trigger/description is updated so the next
    analysis run re-classifies it with the new text.
    """
    stale = [k for k in _intent_cache if k[0] == rule_id]
    for k in stale:
        del _intent_cache[k]
    if stale:
        logger.debug(f"[llm_classifier] Cache invalidated for rule {rule_id}")
