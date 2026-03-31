"""
╔══════════════════════════════════════════════════════════════════════════════╗
║         FRAUD SERVICE — STANDALONE TEST SUITE                               ║
║                                                                              ║
║  Two test layers:                                                            ║
║    1. MODULE tests  — directly import loader / rules / scoring               ║
║       (works offline, no Docker required)                                    ║
║    2. API tests     — hit http://localhost:8001 with real HTTP requests      ║
║       (requires the fraud-service container to be running)                  ║
║                                                                              ║
║  Run:                                                                        ║
║    python test_fraud_service.py              # both layers                  ║
║    python test_fraud_service.py --api-only   # API only                     ║
║    python test_fraud_service.py --unit-only  # module only                  ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import sys
import json
import time
import argparse
import traceback
from pathlib import Path

# ── colour helpers ────────────────────────────────────────────────────────────
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
    bar = "─" * 60
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

def assert_eq(actual, expected, label):
    assert_true(actual == expected, label,
                f"expected {expected!r}, got {actual!r}")

def assert_in(value, collection, label):
    assert_true(value in collection, label,
                f"{value!r} not in {collection!r}")

def assert_gt(actual, threshold, label):
    assert_true(actual > threshold, label,
                f"{actual} not > {threshold}")

def assert_ge(actual, threshold, label):
    assert_true(actual >= threshold, label,
                f"{actual} not >= {threshold}")


# ─────────────────────────────────────────────────────────────────────────────
# KNOWN IBANs from the CSV (with interesting patterns)
# ─────────────────────────────────────────────────────────────────────────────
# DE22965687203229836394  — sent 23 transactions on 2024-01-16 alone
#                           → should trigger STRUCTURING_SMURFING + LAYERING
# DE98525832950016740395  — multiple WIRE_TRANSFERs >30k€
#                           → should trigger HIGH_VALUE
# DE85863193008687593586  — appears 50+ times across 3 months, many countries
#                           → should trigger NIGHT_TRANSACTIONS
# DE80602276804025016525  — very active account, many small txs
#                           → baseline / normal-looking account
IBAN_SMURFING   = "DE22965687203229836394"   # burst of 20+ txs in one day
IBAN_HIGH_VALUE = "DE98525832950016740395"   # 30k–46k€ wire transfers
IBAN_ACTIVE     = "DE85863193008687593586"   # active normal-ish account
IBAN_BASELINE   = "DE80602276804025016525"   # frequent low-value txs
IBAN_UNKNOWN    = "DE00000000000000000000"   # not in dataset — expect 0 txs


# ═════════════════════════════════════════════════════════════════════════════
#  LAYER 1 — MODULE / UNIT TESTS
# ═════════════════════════════════════════════════════════════════════════════

def run_unit_tests():
    section("LAYER 1 — Module / Unit Tests")

    # ── import path setup ────────────────────────────────────────────────────
    here = Path(__file__).resolve().parent
    sys.path.insert(0, str(here))

    try:
        from fraud.loader import (
            load_transactions, filter_by_iban,
            get_account_summary, validate_iban,
        )
        from fraud.rules  import run_all_rules
        from fraud.scoring import (
            compute_behavioral_score,
            compute_aml_score,
            compute_final_score,
        )
        ok("Imports successful (loader / rules / scoring)")
    except Exception as exc:
        fail(f"Import failed: {exc}")
        traceback.print_exc()
        return

    # ─── 1. validate_iban ────────────────────────────────────────────────────
    print("\n── validate_iban ──")
    assert_true(validate_iban("DE22965687203229836394"), "valid DE IBAN accepted")
    assert_true(validate_iban("FR7630006000011234567890189"), "valid FR IBAN accepted")
    assert_true(not validate_iban(""), "empty string rejected")
    assert_true(not validate_iban("NOTANIBAN"), "garbage string rejected")
    assert_true(not validate_iban("DE123"),  "too-short DE rejected")

    # ─── 2. load_transactions ────────────────────────────────────────────────
    print("\n── load_transactions ──")
    try:
        df = load_transactions()
        assert_gt(len(df), 0, f"CSV loaded ({len(df)} rows)")
        cols = set(df.columns)
        for col in ("transaction_amount", "timestamp", "geo_location",
                    "ip_address", "merchant_mcc", "account_currentbalance",
                    "client_iban", "counterparty_iban", "transaction_type"):
            assert_in(col, cols, f"column '{col}' present")
        assert_in("country", cols, "derived 'country' column present")

        # amounts should be numeric
        import pandas as pd
        assert_true(
            pd.api.types.is_float_dtype(df["transaction_amount"]),
            "transaction_amount is float (comma→dot parsed correctly)"
        )
        assert_true(
            pd.api.types.is_float_dtype(df["account_currentbalance"]),
            "account_currentbalance is float"
        )
        assert_true(
            pd.api.types.is_datetime64_any_dtype(df["timestamp"]),
            "timestamp parsed as datetime"
        )
    except FileNotFoundError as exc:
        warn(f"CSV not found — skipping load tests: {exc}")
        return
    except Exception as exc:
        fail(f"load_transactions raised: {exc}")
        traceback.print_exc()
        return

    # ─── 3. filter_by_iban ───────────────────────────────────────────────────
    print("\n── filter_by_iban ──")
    for iban, label, expect_min in [
        (IBAN_SMURFING,   "smurfing IBAN",    20),
        (IBAN_HIGH_VALUE, "high-value IBAN",  10),
        (IBAN_ACTIVE,     "active IBAN",       5),
        (IBAN_BASELINE,   "baseline IBAN",     5),
        (IBAN_UNKNOWN,    "unknown IBAN",       0),
    ]:
        try:
            sub = filter_by_iban(df, iban)
            if iban == IBAN_UNKNOWN:
                assert_eq(len(sub), 0, f"{label}: 0 rows returned")
            else:
                assert_ge(len(sub), expect_min,
                          f"{label}: ≥{expect_min} transactions found ({len(sub)})")
        except Exception as exc:
            fail(f"filter_by_iban({iban[:10]}…) raised: {exc}")

    # ─── 4. get_account_summary ──────────────────────────────────────────────
    print("\n── get_account_summary ──")
    sub = filter_by_iban(df, IBAN_SMURFING)
    summary = get_account_summary(sub)
    assert_gt(summary["total_transactions"], 0, "total_transactions > 0")
    assert_gt(summary["total_amount"],       0, "total_amount > 0")
    assert_gt(summary["avg_amount"],         0, "avg_amount > 0")
    assert_true(summary["date_range"] != "N/A", "date_range computed")
    assert_true(len(summary["transaction_types"]) > 0, "transaction_types populated")
    info(f"  summary: {summary['total_transactions']} txs | "
         f"total €{summary['total_amount']:,.2f} | range {summary['date_range']}")

    # empty DataFrame summary
    import pandas as pd
    empty_summary = get_account_summary(pd.DataFrame())
    assert_eq(empty_summary["total_transactions"], 0, "empty DataFrame → 0 txs")

    # ─── 5. run_all_rules ────────────────────────────────────────────────────
    print("\n── run_all_rules (smurfing IBAN) ──")
    sub_smurf = filter_by_iban(df, IBAN_SMURFING)
    if "timestamp" in sub_smurf.columns:
        import pandas as pd
        sub_smurf = sub_smurf.copy()
        sub_smurf["timestamp"] = pd.to_datetime(sub_smurf["timestamp"], errors="coerce")

    results = run_all_rules(sub_smurf)
    assert_gt(len(results), 0, f"{len(results)} rules evaluated")
    triggered = [r for r in results if r.get("triggered")]
    info(f"  {len(triggered)}/{len(results)} rules triggered for {IBAN_SMURFING[:10]}…")
    for r in triggered:
        info(f"    [{r['severity']}] {r['rule']}: {r['details'][:80]}")

    # STRUCTURING_SMURFING should fire for the burst account
    smurf_rule = next((r for r in results if r["rule"] == "STRUCTURING_SMURFING"), None)
    assert_true(smurf_rule is not None, "STRUCTURING_SMURFING rule present in results")
    if smurf_rule:
        assert_true(smurf_rule["triggered"],
                    "STRUCTURING_SMURFING triggered for burst IBAN")

    # HIGH_VALUE should fire for the 30k+ wire account
    print("\n── run_all_rules (high-value IBAN) ──")
    sub_hv = filter_by_iban(df, IBAN_HIGH_VALUE)
    if "timestamp" in sub_hv.columns:
        sub_hv = sub_hv.copy()
        sub_hv["timestamp"] = pd.to_datetime(sub_hv["timestamp"], errors="coerce")
    hv_results = run_all_rules(sub_hv)
    hv_rule = next((r for r in hv_results if r["rule"] == "HIGH_VALUE"), None)
    assert_true(hv_rule is not None, "HIGH_VALUE rule present")
    if hv_rule:
        assert_true(hv_rule["triggered"], "HIGH_VALUE triggered for large-wire IBAN")
        info(f"    {hv_rule['details'][:80]}")

    # LAYERING_CASCADE for smurfing IBAN
    layer_rule = next((r for r in results if r["rule"] == "LAYERING_CASCADE"), None)
    assert_true(layer_rule is not None, "LAYERING_CASCADE rule present")

    # ─── 6. scoring pipeline ─────────────────────────────────────────────────
    print("\n── scoring pipeline ──")
    beh_score, beh_signals = compute_behavioral_score(sub_smurf)
    assert_ge(beh_score, 0,   f"behavioral score ≥ 0 (got {beh_score})")
    info(f"  behavioral score = {beh_score}  |  signals: {len(beh_signals)}")
    for s in beh_signals:
        info(f"    +{s['points']} pts  {s['signal']}  — {s['detail']}")

    aml_score = compute_aml_score(results)
    assert_ge(aml_score, 0,   f"AML score ≥ 0 (got {aml_score})")
    info(f"  AML score = {aml_score}")

    final_score, risk_level = compute_final_score(beh_score, aml_score)
    assert_ge(final_score, 0, f"final score ≥ 0 (got {final_score})")
    assert_ge(100, final_score, f"final score ≤ 100 (got {final_score})")
    assert_in(risk_level, ["APPROVED", "REVIEW", "HOLD", "BLOCK"],
              f"risk_level valid (got '{risk_level}')")
    info(f"  final score = {final_score}/100  |  risk level = {risk_level}")

    # scoring for baseline (should be lower or equal)
    sub_base = filter_by_iban(df, IBAN_BASELINE)
    if "timestamp" in sub_base.columns:
        sub_base = sub_base.copy()
        sub_base["timestamp"] = pd.to_datetime(sub_base["timestamp"], errors="coerce")
    base_results                   = run_all_rules(sub_base)
    base_beh, _                    = compute_behavioral_score(sub_base)
    base_aml                       = compute_aml_score(base_results)
    base_final, base_risk          = compute_final_score(base_beh, base_aml)
    info(f"  baseline IBAN → score {base_final}/100 ({base_risk})")

    # ─── 7. edge cases ───────────────────────────────────────────────────────
    print("\n── edge cases ──")
    import pandas as pd
    # completely empty DataFrame
    empty_df = pd.DataFrame(columns=df.columns)
    empty_results = run_all_rules(empty_df)
    assert_gt(len(empty_results), 0, "run_all_rules returns results for empty DF")
    triggered_on_empty = [r for r in empty_results if r.get("triggered")]
    assert_eq(len(triggered_on_empty), 0, "no rules triggered for empty DataFrame")

    # unknown IBAN → empty filtered DF
    sub_unk = filter_by_iban(df, IBAN_UNKNOWN)
    unk_results = run_all_rules(sub_unk)
    triggered_unk = [r for r in unk_results if r.get("triggered")]
    assert_eq(len(triggered_unk), 0, "no rules triggered for unknown IBAN")


# ═════════════════════════════════════════════════════════════════════════════
#  LAYER 2 — HTTP API TESTS
# ═════════════════════════════════════════════════════════════════════════════

BASE_URL = "http://localhost:8001"

def _post(path, payload, timeout=60):
    import urllib.request, urllib.error
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(
        BASE_URL + path, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())
    except Exception as exc:
        return None, {"_error": str(exc)}

def _get(path, timeout=10):
    import urllib.request, urllib.error
    try:
        with urllib.request.urlopen(BASE_URL + path, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except Exception as exc:
        return None, {"_error": str(exc)}


def run_api_tests():
    section("LAYER 2 — HTTP API Tests")
    info(f"Target: {BASE_URL}")

    # ── health check ─────────────────────────────────────────────────────────
    print("\n── GET /health ──")
    status, body = _get("/health")
    if status is None:
        fail(f"Service unreachable: {body.get('_error')}")
        warn("Start the fraud-service container and re-run with --api-only")
        return
    assert_eq(status, 200, "HTTP 200 from /health")
    assert_eq(body.get("status"), "ok", "status == 'ok'")
    info(f"  version: {body.get('version')}")

    # ── fraud_check — well-known smurfing IBAN ─────────────────────────────
    print(f"\n── POST /analyze  (smurfing IBAN {IBAN_SMURFING[:10]}…) ──")
    t0 = time.time()
    status, body = _post("/analyze", {
        "iban": IBAN_SMURFING,
        "action": "fraud_check",
        "user_id": "test-runner",
        "session_id": "test-session-001",
    })
    elapsed = time.time() - t0
    assert_eq(status, 200, "HTTP 200")
    assert_true(body.get("error") is None, f"no error field (got {body.get('error')!r})")
    assert_gt(body.get("transactions_count", 0), 0,
              f"transactions_count > 0 (got {body.get('transactions_count')})")
    assert_ge(body.get("score_final", -1), 0,  "score_final ≥ 0")
    assert_ge(100, body.get("score_final", 101), "score_final ≤ 100")
    assert_in(body.get("risk_level"), ["APPROVED","REVIEW","HOLD","BLOCK"],
              f"risk_level valid (got {body.get('risk_level')!r})")
    assert_true(isinstance(body.get("fraud_results"), list),
                "fraud_results is a list")
    assert_gt(len(body.get("fraud_results", [])), 0, "fraud_results non-empty")
    assert_true(bool(body.get("llm_summary")), "llm_summary present")
    info(f"  score={body['score_final']}/100  risk={body['risk_level']}"
         f"  txs={body['transactions_count']}  elapsed={elapsed:.1f}s")
    triggered = [r for r in body["fraud_results"] if r.get("triggered")]
    info(f"  {len(triggered)} rules triggered:")
    for r in triggered:
        info(f"    [{r['severity']}] {r['rule']}")

    # ── fraud_check via message (IBAN extracted from text) ─────────────────
    print(f"\n── POST /analyze  (IBAN in message text, high-value) ──")
    status, body = _post("/analyze", {
        "message": f"Vérifie les fraudes pour le compte {IBAN_HIGH_VALUE}",
        "user_id": "test-runner",
    })
    assert_eq(status, 200, "HTTP 200")
    assert_eq(body.get("iban"), IBAN_HIGH_VALUE,
              f"IBAN extracted from message (got {body.get('iban')!r})")
    assert_gt(body.get("transactions_count", 0), 0, "transactions found via message IBAN")
    info(f"  extracted IBAN: {body.get('iban')}  txs: {body.get('transactions_count')}")

    # ── fraud_check — active account ───────────────────────────────────────
    print(f"\n── POST /analyze  (active account {IBAN_ACTIVE[:10]}…) ──")
    status, body = _post("/analyze", {"iban": IBAN_ACTIVE, "action": "fraud_check"})
    assert_eq(status, 200, "HTTP 200")
    assert_true(body.get("error") is None, "no error")
    assert_gt(body.get("transactions_count", 0), 0, "transactions_count > 0")
    info(f"  score={body.get('score_final')}/100  risk={body.get('risk_level')}")

    # ── export_transactions ────────────────────────────────────────────────
    print(f"\n── POST /analyze  (export_transactions, baseline IBAN) ──")
    status, body = _post("/analyze", {
        "iban": IBAN_BASELINE,
        "action": "export_transactions",
        "message": "export toutes les transactions",
    })
    assert_eq(status, 200, "HTTP 200")
    assert_true(body.get("error") is None, "no error")
    report_path = body.get("report_path", "")
    assert_true(bool(report_path), f"report_path non-empty (got {report_path!r})")
    info(f"  report: {report_path}")

    # ── unknown IBAN ────────────────────────────────────────────────────────
    print(f"\n── POST /analyze  (unknown IBAN) ──")
    status, body = _post("/analyze", {"iban": IBAN_UNKNOWN})
    assert_eq(status, 200, "HTTP 200")
    assert_eq(body.get("transactions_count", 0), 0, "transactions_count == 0")
    assert_true(bool(body.get("error")), "error field non-empty for unknown IBAN")
    info(f"  error: {body.get('error')}")

    # ── missing IBAN (no iban, no message) ─────────────────────────────────
    print(f"\n── POST /analyze  (no IBAN provided) ──")
    status, body = _post("/analyze", {"user_id": "test"})
    assert_eq(status, 200, "HTTP 200")
    assert_true(bool(body.get("error")), "error returned when IBAN missing")
    info(f"  error: {body.get('error')}")

    # ── response schema completeness ────────────────────────────────────────
    print(f"\n── Response schema validation ──")
    status, body = _post("/analyze", {"iban": IBAN_SMURFING})
    expected_keys = [
        "iban", "action", "transactions_count", "account_summary",
        "score_behavioral", "score_aml", "score_final",
        "risk_level", "tracfin_required", "fraud_results",
        "report_path", "llm_summary", "error",
    ]
    for key in expected_keys:
        assert_in(key, body, f"response contains '{key}'")

    # account_summary sub-keys
    if body.get("account_summary"):
        summary = body["account_summary"]
        for sk in ("total_transactions", "total_amount", "avg_amount",
                   "max_amount", "min_amount", "date_range", "transaction_types"):
            assert_in(sk, summary, f"account_summary.{sk} present")


# ═════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═════════════════════════════════════════════════════════════════════════════

def print_summary():
    bar = "═" * 60
    total = PASSED + FAILED
    print(f"\n{BOLD}{bar}{RESET}")
    if FAILED == 0:
        print(f"{BOLD}{GREEN}  ALL {PASSED}/{total} TESTS PASSED ✔{RESET}")
    else:
        print(f"{BOLD}{RED}  {FAILED}/{total} TESTS FAILED ✗   ({PASSED} passed){RESET}")
    print(f"{BOLD}{bar}{RESET}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fraud service test suite")
    grp = parser.add_mutually_exclusive_group()
    grp.add_argument("--api-only",  action="store_true", help="Run only HTTP API tests")
    grp.add_argument("--unit-only", action="store_true", help="Run only module unit tests")
    args = parser.parse_args()

    if not args.api_only:
        run_unit_tests()

    if not args.unit_only:
        run_api_tests()

    print_summary()
    sys.exit(0 if FAILED == 0 else 1)
