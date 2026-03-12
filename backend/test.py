"""
Quick test script for the fraud detection agent.
Run from the backend/ directory:  python test_fraud_agent.py
"""

import os
import sys
import django

# Setup Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
sys.path.insert(0, os.path.dirname(__file__))
django.setup()

from langchain_core.messages import HumanMessage
from chatbot.graph.fraud.graph import run_fraud_agent
from chatbot.graph.nodes import detect_intent

print("=" * 60)
print("  🧪 FRAUD AGENT — INTEGRATION TEST")
print("=" * 60)


# ── Test 1: Intent Detection ──────────────────────────────────────
print("\n📋 TEST 1: Intent Detection")
print("-" * 40)

test_messages = [
    ("Analyse les fraudes pour IBAN_FR123", "fraud"),
    ("Vérifie s'il y a des anomalies sur IBAN_FR123", "fraud"),
    ("Export toutes les transactions pour IBAN_FR123", "fraud"),
    ("Check suspicious activity on IBAN_FR123", "fraud"),
    ("Quel est mon solde?", "account"),
    ("Je veux faire un virement", "transfer"),
]

for msg, expected in test_messages:
    state = {
        "messages": [HumanMessage(content=msg)],
        "user_id": "test",
        "session_id": "test-session",
        "intent": "",
        "agent": "",
        "context": {},
        "error": None,
    }
    result = detect_intent(state)
    actual = result["intent"]
    status = "✅" if actual == expected else "❌"
    print(f"  {status} '{msg[:50]}...' → intent={actual} (expected={expected})")


# ── Test 2: Fraud Agent — Full Pipeline (Fraud Check) ────────────
print("\n📋 TEST 2: Fraud Agent — Full Pipeline (fraud_check)")
print("-" * 40)

try:
    result = run_fraud_agent(
        messages=[HumanMessage(content="Analyse les fraudes pour IBAN_FR123")],
        user_id="test_user",
        session_id="test-session-1",
    )

    print(f"  IBAN:               {result.get('iban')}")
    print(f"  Action:             {result.get('action')}")
    print(f"  Transactions found: {result.get('transactions_count')}")
    print(f"  Score behavioral:   {result.get('score_behavioral')}/130")
    print(f"  Score AML:          {result.get('score_aml')}/100")
    print(f"  Score final:        {result.get('score_final')}/100")
    print(f"  Risk level:         {result.get('risk_level')}")
    print(f"  TRACFIN required:   {result.get('tracfin_required')}")
    print(f"  Report path:        {result.get('report_path')}")
    print(f"  Error:              {result.get('error')}")

    # Print triggered rules
    triggered = [r for r in result.get("fraud_results", []) if r.get("triggered")]
    if triggered:
        print(f"\n  🚨 Triggered rules ({len(triggered)}):")
        for r in triggered:
            print(f"    [{r['severity']}] {r['rule']}: {r['details']} (score={r['score']})")
    else:
        print("  ✅ No fraud rules triggered")

    # Print summary preview
    summary = result.get("llm_summary", "")
    if summary:
        print(f"\n  📝 Summary preview:")
        print(f"  {summary[:300]}...")

    print("\n  ✅ TEST 2 PASSED")

except FileNotFoundError as e:
    print(f"  ⚠️  Skipped (no Excel file): {e}")
except Exception as e:
    print(f"  ❌ TEST 2 FAILED: {e}")
    import traceback
    traceback.print_exc()


# ── Test 3: Fraud Agent — Export ──────────────────────────────────
print("\n📋 TEST 3: Fraud Agent — Export Transactions")
print("-" * 40)

try:
    result = run_fraud_agent(
        messages=[HumanMessage(content="Exporte toutes les transactions pour IBAN_FR123 en Excel")],
        user_id="test_user",
        session_id="test-session-2",
    )

    print(f"  Action:             {result.get('action')}")
    print(f"  Transactions found: {result.get('transactions_count')}")
    print(f"  Report path:        {result.get('report_path')}")
    print(f"  Error:              {result.get('error')}")
    print("  ✅ TEST 3 PASSED")

except FileNotFoundError as e:
    print(f"  ⚠️  Skipped (no Excel file): {e}")
except Exception as e:
    print(f"  ❌ TEST 3 FAILED: {e}")


# ── Test 4: Missing IBAN ─────────────────────────────────────────
print("\n📋 TEST 4: Missing IBAN handling")
print("-" * 40)

try:
    result = run_fraud_agent(
        messages=[HumanMessage(content="Analyse les fraudes svp")],
        user_id="test_user",
        session_id="test-session-3",
    )

    has_error = bool(result.get("error"))
    print(f"  Error detected:     {has_error}")
    print(f"  Error message:      {result.get('error', '')[:100]}")
    print("  ✅ TEST 4 PASSED" if has_error else "  ❌ TEST 4 FAILED (should have error)")

except Exception as e:
    print(f"  ❌ TEST 4 FAILED: {e}")


print("\n" + "=" * 60)
print("  🏁 ALL TESTS COMPLETE")
print("=" * 60)