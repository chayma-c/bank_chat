"""
tests/test_rule_engine.py — Test suite for the rule engine dispatch.

Covers:
  Section 1 — _keyword_match: 13 standard English triggers (should match)
  Section 2 — _keyword_match: triggers that must NOT match keywords
  Section 3 — _pick_evaluator: LLM fallback for unrecognized triggers
  Section 4 — _pick_evaluator: LLM error/invalid-key handling
  Section 5 — _pick_evaluator: domain fallback when LLM also fails
  Section 6 — classify_rule_intent: in-memory cache behaviour
  Section 7 — classify_rule_intent: LLM key validation and stripping

Run (from fraud-service/):
    python tests/test_rule_engine.py
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ── colour helpers (same style as existing test suite) ────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def ok(msg):   print(f"  {GREEN}✔{RESET}  {msg}")
def fail(msg): print(f"  {RED}✗{RESET}  {msg}")
def info(msg): print(f"  {CYAN}ℹ{RESET}  {msg}")
def warn(msg): print(f"  {YELLOW}⚠{RESET}  {msg}")

def section(title):
    bar = "─" * 62
    print(f"\n{BOLD}{CYAN}{bar}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{bar}{RESET}")

PASSED = 0
FAILED = 0

def assert_true(condition, label, detail=""):
    global PASSED, FAILED
    if condition:
        ok(label)
        PASSED += 1
    else:
        fail(f"{label}  →  {detail}")
        FAILED += 1

def assert_is(actual, expected, label):
    assert_true(
        actual is expected, label,
        f"expected {getattr(expected, '__name__', expected)!r}, "
        f"got {getattr(actual,   '__name__', actual)!r}",
    )

def assert_eq(actual, expected, label):
    assert_true(actual == expected, label, f"expected {expected!r}, got {actual!r}")

def assert_none(val, label):
    assert_true(val is None, label, f"expected None, got {val!r}")


# ── helpers ───────────────────────────────────────────────────────────────────

def make_rule(
    trigger: str,
    trigger_detail: str = "",
    domain: str = "BEHAVIORAL",
    rule_id: str = "RL-TEST-0001",
    name: str = "Test rule",
    points: int = 10,
    severity: str = "MEDIUM",
    description: str = "",
    updated_at=None,
) -> SimpleNamespace:
    """Lightweight stand-in for FraudRuleModel — no DB required."""
    import datetime
    return SimpleNamespace(
        id=rule_id,
        name=name,
        domain=domain,
        trigger=trigger,
        trigger_detail=trigger_detail,
        points=points,
        severity=severity,
        description=description,
        active=True,
        updated_at=updated_at or datetime.datetime(2025, 1, 1),
    )


# ── import under test ─────────────────────────────────────────────────────────

try:
    from fraud.rule_engine import (
        _keyword_match,
        _pick_evaluator,
        _eval_large_amount,
        _eval_near_threshold_amount,
        _eval_suspicious_iban,
        _eval_structuring,
        _eval_night_transactions,
        _eval_foreign_ip,
        _eval_high_risk_mcc,
        _eval_balance_ratio,
        _eval_repeated_alerts,
        _eval_velocity,
        _eval_new_beneficiary,
        _eval_dormant_account,
        _eval_cross_border,
        _eval_generic,
    )
    _IMPORTS_OK = True
except Exception as exc:
    warn(f"Could not import rule_engine: {exc}")
    traceback.print_exc()
    _IMPORTS_OK = False


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 1 — _keyword_match: 13 standard English triggers
# ═════════════════════════════════════════════════════════════════════════════

def test_keyword_match_known():
    section("Section 1 — _keyword_match: 13 standard English triggers")

    cases = [
        # (trigger text, expected evaluator, label)
        ("account inactive for 90 days",                        _eval_dormant_account,       "dormant — 'inactive'"),
        ("dormant account reactivated",                         _eval_dormant_account,       "dormant — 'dormant'"),
        ("account idle more than 90 day threshold",             _eval_dormant_account,       "dormant — '90 day'"),

        ("structuring pattern detected",                        _eval_structuring,           "structuring — 'structur'"),
        ("smurfing: many small deposits",                       _eval_structuring,           "structuring — 'smurfing'"),
        ("more than 20 deposits in 7 days",                     _eval_structuring,           "structuring — '20 deposits'"),

        ("amount between 9500 EUR and 9999 EUR",                _eval_near_threshold_amount, "near-threshold — 'between ... EUR'"),
        ("near-threshold AML amount detected",                  _eval_near_threshold_amount, "near-threshold — 'near-threshold'"),
        ("suspicious amount around 9500",                       _eval_near_threshold_amount, "near-threshold — '9500'"),
        ("amount just below 9999",                              _eval_near_threshold_amount, "near-threshold — '9999'"),

        ("night transfer detected at 00:00",                    _eval_night_transactions,    "night — '00:00'"),
        ("unusual night transaction",                           _eval_night_transactions,    "night — 'night'"),
        ("transaction at 01:00 AM",                             _eval_night_transactions,    "night — '01:00'"),

        ("ip country mismatch foreign",                         _eval_foreign_ip,            "foreign IP — 'ip' + 'foreign'"),
        ("ip country does not match home",                      _eval_foreign_ip,            "foreign IP — 'ip' + 'country'"),
        ("ip geofencing violation detected",                    _eval_foreign_ip,            "foreign IP — 'ip' + 'geofenc'"),
        ("geofencing alert triggered",                          _eval_foreign_ip,            "foreign IP — 'geofencing'"),
        ("distance exceeds 1000 km from home",                  _eval_foreign_ip,            "foreign IP — '1000 km'"),

        ("high-risk merchant mcc code 5541",                    _eval_high_risk_mcc,         "MCC — 'mcc'"),
        ("suspicious merchant transaction",                     _eval_high_risk_mcc,         "MCC — 'merchant'"),

        ("transaction drains more than 80% of balance",         _eval_balance_ratio,         "balance ratio — 'balance' + '%'"),
        ("balance exceeds 90% withdrawn",                       _eval_balance_ratio,         "balance ratio — 'balance %'"),

        ("repeated alert: 2 or more alerts in 7 days",          _eval_repeated_alerts,       "repeated alerts — '2 or more'"),
        ("alert: repeated fraud signals",                       _eval_repeated_alerts,       "repeated alerts — 'repeated'"),
        ("account alerted multiple times",                      _eval_repeated_alerts,       "repeated alerts — 'alerted'"),

        ("high velocity: more than 5 card tx per 1 hour",       _eval_velocity,              "velocity — 'per 1 hour'"),
        ("velocity threshold exceeded",                         _eval_velocity,              "velocity — 'velocity'"),
        ("wire transfer: more than 10 tx per hour",             _eval_velocity,              "velocity — 'per hour'"),
        ("10 min window exceeded",                              _eval_velocity,              "velocity — '10 min'"),

        ("iban on ofac sanctions list",                         _eval_suspicious_iban,       "IBAN — 'iban' + 'ofac'"),
        ("iban flagged in blacklist",                           _eval_suspicious_iban,       "IBAN — 'iban' + 'blacklist'"),
        ("counterpart iban is suspicious",                      _eval_suspicious_iban,       "IBAN — 'counterpart'"),

        ("transaction spans different countries in 48 hours",   _eval_cross_border,          "cross-border — 'different countr'"),
        ("cross-border transaction detected",                   _eval_cross_border,          "cross-border — 'cross-border'"),
        ("cross border layering in 48 hour window",             _eval_cross_border,          "cross-border — '48 hour'"),

        ("new beneficiary added with immediate transfer",       _eval_new_beneficiary,       "new beneficiary — 'new beneficiar'"),
        ("beneficiary is new, amount 3000 EUR",                 _eval_new_beneficiary,       "new beneficiary — 'beneficiar' + '3000'"),

        ("amount > 5000 EUR",                                   _eval_large_amount,          "large amount — 'amount >'"),
        ("transaction amount > 10000",                          _eval_large_amount,          "large amount — 'amount >'"),
    ]

    for trigger, expected, label in cases:
        result = _keyword_match(trigger.lower())
        assert_is(result, expected, label)


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 2 — _keyword_match: triggers that must NOT match (return _eval_generic)
# ═════════════════════════════════════════════════════════════════════════════

def test_keyword_match_no_match():
    section("Section 2 — _keyword_match: triggers that should return _eval_generic")

    no_match_cases = [
        # French / alternate language — semantically correct but no EN keyword
        ("montant dépasse 8000 eur",               "FR: large amount — no EN keyword"),
        ("transfert nocturne après minuit",         "FR: night tx — no EN keyword"),
        ("ip provenant d'un pays étranger",         "FR: foreign IP (no 'ip country/geofenc')"),
        ("virement vers nouveau bénéficiaire",      "FR: new beneficiary — no EN keyword"),
        ("compte dormant réactivé",                 "FR: dormant — 'dormant' absent in EN form"),
        # Note: 'dormant' IS an English keyword — French uses same spelling, so it WOULD match.
        # The test below uses a French-only phrasing without any matching EN token.
        ("compte inactif depuis 90 jours",          "FR: dormant — 'inactif' not 'inactive'"),
        ("structuration financière",                "FR: structuring — no 'structur' match"),

        # Partial / misleading keywords that should NOT match
        ("iban transfer completed",                 "IBAN without blacklist/suspicious/ofac"),
        ("balance of account is positive",          "balance without %"),
        ("alert sent to customer",                  "alert without 'repeated/alerted/2 or more'"),
        ("merchant category review",                "merchant — maps to MCC (intentional, tested separately)"),

        # Completely unrelated
        ("",                                        "empty trigger"),
        ("hello world",                             "garbage trigger"),
        ("review this transaction",                 "vague text"),
    ]

    # 'merchant category review' actually DOES match (merchant → MCC).
    # Remove it from the "must not match" list — it's a valid match.
    true_no_match = [
        (t, lbl) for t, lbl in no_match_cases
        if "merchant" not in t  # merchant IS a keyword
        and "dormant" not in t  # 'dormant' IS an English keyword
    ]

    for trigger, label in true_no_match:
        result = _keyword_match(trigger.lower())
        assert_is(result, _eval_generic, label)

    # Explicit: "merchant" IS captured (not a no-match)
    assert_true(
        _keyword_match("merchant category review") is not _eval_generic,
        "'merchant' keyword is recognized (maps to _eval_high_risk_mcc)",
    )

    # "balance of account" — no % sign → no match
    assert_is(_keyword_match("balance of account is positive"), _eval_generic,
              "balance without % → generic")

    # "iban transfer" — no blacklist/suspicious/ofac/counterpart → no match
    assert_is(_keyword_match("iban transfer completed"), _eval_generic,
              "iban without qualifier → generic")


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 3 — _pick_evaluator: LLM fallback resolves unrecognized triggers
# ═════════════════════════════════════════════════════════════════════════════

def test_pick_evaluator_llm_fallback():
    section("Section 3 — _pick_evaluator: LLM fallback for unrecognized triggers")

    cases = [
        # (trigger, domain, llm_key_returned, expected_evaluator, label)
        ("montant dépasse 8000 eur",         "LIMIT",      "large_amount",       _eval_large_amount,          "FR large amount"),
        ("transfert nocturne après minuit",  "BEHAVIORAL", "night_transactions", _eval_night_transactions,    "FR night tx"),
        ("ip pays étranger",                 "GEOGRAPHIC", "foreign_ip",         _eval_foreign_ip,            "FR foreign IP"),
        ("virement nouveau bénéficiaire",    "BEHAVIORAL", "new_beneficiary",    _eval_new_beneficiary,       "FR new beneficiary"),
        ("compte inactif 90 jours",          "BEHAVIORAL", "dormant_account",    _eval_dormant_account,       "FR dormant account"),
        ("structuration de paiements",       "AML",        "structuring",        _eval_structuring,           "FR structuring"),
        ("fréquence élevée de transactions", "VELOCITY",   "velocity",           _eval_velocity,              "FR velocity"),
        ("pays multiples en 48h",            "GEOGRAPHIC", "cross_border",       _eval_cross_border,          "FR cross-border"),
        ("iban liste noire",                 "GEOGRAPHIC", "suspicious_iban",    _eval_suspicious_iban,       "FR suspicious IBAN"),
        ("vidange du solde",                 "BEHAVIORAL", "balance_ratio",      _eval_balance_ratio,         "FR balance drain"),
        ("alertes répétées",                 "BEHAVIORAL", "repeated_alerts",    _eval_repeated_alerts,       "FR repeated alerts"),
        ("montant proche seuil 10000 eur",   "AML",        "near_threshold",     _eval_near_threshold_amount, "FR near threshold"),
        ("code MCC risqué",                  "BEHAVIORAL", "high_risk_mcc",      _eval_high_risk_mcc,         "FR high-risk MCC"),
    ]

    for trigger, domain, llm_key, expected, label in cases:
        rule = make_rule(trigger=trigger, domain=domain)
        mock_response = MagicMock()
        mock_response.content = llm_key

        with patch("fraud.llm_rule_classifier._get_llm") as mock_get_llm:
            mock_llm = MagicMock()
            mock_llm.invoke.return_value = mock_response
            mock_get_llm.return_value = mock_llm

            # Clear the classifier cache to force a fresh LLM call
            import fraud.llm_rule_classifier as clf
            clf._intent_cache.clear()

            result = _pick_evaluator(rule)
            assert_is(result, expected, f"LLM fallback: {label}")


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 4 — _pick_evaluator: LLM error / invalid-key handling
# ═════════════════════════════════════════════════════════════════════════════

def test_pick_evaluator_llm_errors():
    section("Section 4 — _pick_evaluator: LLM error and invalid key handling")

    import fraud.llm_rule_classifier as clf

    # 4a — LLM returns an unknown key → domain fallback
    rule = make_rule(trigger="completely unknown trigger xyz", domain="LIMIT")
    clf._intent_cache.clear()
    mock_response = MagicMock()
    mock_response.content = "nonexistent_evaluator_key"

    with patch("fraud.llm_rule_classifier._get_llm") as mock_get_llm:
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = mock_response
        mock_get_llm.return_value = mock_llm
        clf._intent_cache.clear()

        result = _pick_evaluator(rule)
        # LIMIT domain → domain fallback → _eval_large_amount
        assert_is(result, _eval_large_amount,
                  "Unknown LLM key + LIMIT domain → domain fallback (_eval_large_amount)")

    # 4b — LLM returns empty string → domain fallback
    rule2 = make_rule(trigger="unknown trigger", domain="VELOCITY")
    clf._intent_cache.clear()
    mock_response2 = MagicMock()
    mock_response2.content = ""

    with patch("fraud.llm_rule_classifier._get_llm") as mock_get_llm:
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = mock_response2
        mock_get_llm.return_value = mock_llm
        clf._intent_cache.clear()

        result2 = _pick_evaluator(rule2)
        assert_is(result2, _eval_velocity,
                  "Empty LLM key + VELOCITY domain → domain fallback (_eval_velocity)")

    # 4c — LLM raises an exception → domain fallback
    rule3 = make_rule(trigger="unknown trigger", domain="AML")
    clf._intent_cache.clear()

    with patch("fraud.llm_rule_classifier._get_llm") as mock_get_llm:
        mock_get_llm.side_effect = RuntimeError("Groq API timeout")
        clf._intent_cache.clear()

        result3 = _pick_evaluator(rule3)
        assert_is(result3, _eval_structuring,
                  "LLM exception + AML domain → domain fallback (_eval_structuring)")

    # 4d — Unknown domain AND LLM fails → _eval_generic
    rule4 = make_rule(trigger="unknown trigger", domain="UNKNOWN_DOMAIN")
    clf._intent_cache.clear()

    with patch("fraud.llm_rule_classifier._get_llm") as mock_get_llm:
        mock_get_llm.side_effect = RuntimeError("unavailable")
        clf._intent_cache.clear()

        result4 = _pick_evaluator(rule4)
        assert_is(result4, _eval_generic,
                  "LLM exception + unknown domain → _eval_generic")


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 5 — _pick_evaluator: domain fallback order
# ═════════════════════════════════════════════════════════════════════════════

def test_pick_evaluator_domain_fallback():
    section("Section 5 — _pick_evaluator: domain fallback (LLM disabled)")

    import fraud.llm_rule_classifier as clf

    domain_cases = [
        ("LIMIT",      _eval_large_amount,  "LIMIT → large_amount"),
        ("AML",        _eval_structuring,   "AML → structuring"),
        ("VELOCITY",   _eval_velocity,      "VELOCITY → velocity"),
        ("GEOGRAPHIC", _eval_foreign_ip,    "GEOGRAPHIC → foreign_ip"),
        ("BEHAVIORAL", _eval_balance_ratio, "BEHAVIORAL → balance_ratio"),
    ]

    for domain, expected, label in domain_cases:
        rule = make_rule(trigger="zz no keyword match zz", domain=domain)
        clf._intent_cache.clear()

        with patch("fraud.llm_rule_classifier._get_llm") as mock_get_llm:
            mock_get_llm.side_effect = RuntimeError("LLM disabled")

            result = _pick_evaluator(rule)
            assert_is(result, expected, f"Domain fallback: {label}")

    # Unknown domain → _eval_generic
    rule_unknown = make_rule(trigger="zz no keyword match zz", domain="CUSTOM")
    clf._intent_cache.clear()
    with patch("fraud.llm_rule_classifier._get_llm") as mock_get_llm:
        mock_get_llm.side_effect = RuntimeError("LLM disabled")
        result = _pick_evaluator(rule_unknown)
        assert_is(result, _eval_generic, "Unknown domain + LLM fail → _eval_generic")


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 6 — classify_rule_intent: in-memory cache behaviour
# ═════════════════════════════════════════════════════════════════════════════

def test_classifier_cache():
    section("Section 6 — classify_rule_intent: cache hit, miss, invalidation")

    from fraud.llm_rule_classifier import classify_rule_intent, invalidate_rule_cache
    import fraud.llm_rule_classifier as clf

    clf._intent_cache.clear()

    import datetime
    rule = make_rule(
        trigger="vague trigger that needs llm",
        domain="BEHAVIORAL",
        rule_id="RL-CACHE-001",
        updated_at=datetime.datetime(2025, 6, 1, 12, 0, 0),
    )

    mock_response = MagicMock()
    mock_response.content = "balance_ratio"

    with patch("fraud.llm_rule_classifier._get_llm") as mock_get_llm:
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = mock_response
        mock_get_llm.return_value = mock_llm

        # First call — LLM should be invoked
        result1 = classify_rule_intent(rule)
        assert_eq(result1, "balance_ratio", "Cache miss: correct key returned")
        assert_eq(mock_llm.invoke.call_count, 1, "Cache miss: LLM invoked exactly once")

        # Second call, same rule — must use cache (LLM NOT called again)
        result2 = classify_rule_intent(rule)
        assert_eq(result2, "balance_ratio", "Cache hit: same key returned")
        assert_eq(mock_llm.invoke.call_count, 1,
                  "Cache hit: LLM NOT invoked a second time")

        # Invalidate cache
        invalidate_rule_cache(rule.id)
        cache_key = (rule.id, str(rule.updated_at))
        assert_true(cache_key not in clf._intent_cache,
                    "Cache entry removed after invalidate_rule_cache()")

        # Third call after invalidation — LLM must be called again
        result3 = classify_rule_intent(rule)
        assert_eq(result3, "balance_ratio", "Post-invalidation: correct key returned")
        assert_eq(mock_llm.invoke.call_count, 2,
                  "Post-invalidation: LLM invoked again")


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 7 — classify_rule_intent: key validation and response stripping
# ═════════════════════════════════════════════════════════════════════════════

def test_classifier_key_validation():
    section("Section 7 — classify_rule_intent: key validation and response format")

    from fraud.llm_rule_classifier import classify_rule_intent
    import fraud.llm_rule_classifier as clf

    mock_rule = make_rule(trigger="some trigger", rule_id="RL-VAL-001")

    cases = [
        # (raw LLM response,    expected result,     label)
        ("large_amount",        "large_amount",       "exact valid key"),
        ("large_amount\n",      "large_amount",       "key with trailing newline stripped"),
        ("large_amount.",       "large_amount",       "key with trailing period stripped"),
        ("LARGE_AMOUNT",        "large_amount",       "uppercase key normalised"),
        ("Large_Amount",        "large_amount",       "mixed-case key normalised"),
        ("large_amount is the answer", "large_amount","extra words after key ignored"),
        ("unknown_key",         None,                 "unknown key → None"),
        ("",                    None,                 "empty response → None"),
        ("   ",                 None,                 "whitespace-only → None"),
    ]

    for raw_response, expected, label in cases:
        import datetime
        mock_rule.updated_at = datetime.datetime(2025, 6, 1, 12, 0, 0)
        mock_rule.id = f"RL-VAL-{raw_response[:8].replace(' ', '_')}"
        clf._intent_cache.clear()

        mock_resp = MagicMock()
        mock_resp.content = raw_response

        with patch("fraud.llm_rule_classifier._get_llm") as mock_get_llm:
            mock_llm = MagicMock()
            mock_llm.invoke.return_value = mock_resp
            mock_get_llm.return_value = mock_llm

            result = classify_rule_intent(mock_rule)
            assert_eq(result, expected, label)

    # LLM raises exception → None (not a crash)
    clf._intent_cache.clear()
    with patch("fraud.llm_rule_classifier._get_llm") as mock_get_llm:
        mock_get_llm.side_effect = ConnectionError("network error")
        result = classify_rule_intent(mock_rule)
        assert_none(result, "LLM exception → None (graceful)")


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def print_summary():
    bar = "═" * 62
    total = PASSED + FAILED
    print(f"\n{BOLD}{bar}{RESET}")
    if FAILED == 0:
        print(f"{BOLD}{GREEN}  ALL {PASSED}/{total} TESTS PASSED ✔{RESET}")
    else:
        print(f"{BOLD}{RED}  {FAILED}/{total} TESTS FAILED ✗   ({PASSED} passed){RESET}")
    print(f"{BOLD}{bar}{RESET}\n")


if __name__ == "__main__":
    if not _IMPORTS_OK:
        print(f"{RED}Cannot run tests — import failed (see above).{RESET}")
        sys.exit(1)

    try:
        test_keyword_match_known()
        test_keyword_match_no_match()
        test_pick_evaluator_llm_fallback()
        test_pick_evaluator_llm_errors()
        test_pick_evaluator_domain_fallback()
        test_classifier_cache()
        test_classifier_key_validation()
    except Exception as exc:
        fail(f"Unexpected exception: {exc}")
        traceback.print_exc()

    print_summary()
    sys.exit(0 if FAILED == 0 else 1)
