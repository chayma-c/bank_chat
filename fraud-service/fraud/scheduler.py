"""
scheduler.py — Background fraud analysis loop.

Upgraded in Phase 2:
  - Reads transactions from the DB (not the CSV file)
  - Uses the configurable rolling window from fraud_metadata
  - Uses rule_engine.run_rules_from_db() (dynamic, DB-backed rules)
  - Upserts AccountRiskProfile after each IBAN analysis
"""

import asyncio
import logging
import os
import pandas as pd
from datetime import datetime, time

from .database import SessionLocal
from .db import get_settings, update_last_run, update_last_auto_run, save_report_blob, get_rolling_window_days
from .loader import load_transactions, list_all_ibans
from .rule_engine import run_rules_from_db
from .scoring import (
    compute_behavioral_score,
    compute_aml_score,
    compute_final_score,
    check_tracfin_required,
)
from .profile_store import upsert_account_risk_profile
from .report import generate_master_fraud_report

logger = logging.getLogger(__name__)

# ── Batch Analysis Core ──────────────────────────────────────────────────────


async def run_global_analysis_task():
    """
    Perform a complete scan of all IBANs in the transactions table.

    For each IBAN:
      1. Load transactions from DB within the rolling window
      2. Run DB-backed fraud rules (run_rules_from_db)
      3. Compute scores
      4. Upsert AccountRiskProfile
      5. Collect results for the master report

    At the end: generate the master Excel report and save to DB.
    """
    logger.info("🚀 [scheduler] Starting Global Fraud Audit...")
    db = SessionLocal()
    try:
        # ── 0. Read rolling window ───────────────────────────────────────────
        window_days = get_rolling_window_days()
        logger.info(f"[scheduler] Rolling window: {window_days} days")

        # ── 1. Discover all unique IBANs ─────────────────────────────────────
        ibans = list_all_ibans(db=db)
        if not ibans:
            logger.warning("⚠️ [scheduler] No IBANs found in DB. Aborting.")
            return

        logger.info(f"🧐 [scheduler] Analyzing {len(ibans)} accounts...")
        all_results = []

        for iban in ibans:
            try:
                # ── 2. Load transactions for this IBAN within the window ─────
                df = load_transactions(db=db, iban=iban, window_days=window_days)

                if df.empty:
                    logger.debug(f"[scheduler] No rows in window for {iban} — skipping.")
                    continue

                # Ensure timestamp is datetime
                if "timestamp" in df.columns:
                    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)

                # ── 3. Evaluate rules (DB-backed) ────────────────────────────
                rule_results = run_rules_from_db(df, db)
                score_behavioral, _ = compute_behavioral_score(df, db=db)
                score_aml           = compute_aml_score(rule_results)
                score_final, risk_level = compute_final_score(score_behavioral, score_aml)
                tracfin             = check_tracfin_required(rule_results, df)

                # ── 4. Upsert rolling profile ────────────────────────────────
                upsert_account_risk_profile(
                    db=db,
                    iban=iban,
                    score_final=score_final,
                    risk_level=risk_level,
                    tracfin=tracfin,
                    df=df,
                    window_days=window_days,
                )

                all_results.append({
                    "iban":               iban,
                    "score_behavioral":   score_behavioral,
                    "score_aml":          score_aml,
                    "score_final":        score_final,
                    "risk_level":         risk_level,
                    "tracfin_required":   tracfin,
                    "transactions_count": len(df),
                    # Only include raw rows in the report if high risk
                    "transactions_raw":   df.to_dict("records") if score_final > 50 else [],
                })

                logger.info(
                    f"[scheduler] {iban}: score={score_final} "
                    f"level={risk_level} tracfin={tracfin} "
                    f"txs={len(df)}"
                )

            except Exception as exc:
                logger.error(f"❌ [scheduler] Error analyzing {iban}: {exc}")

        # ── 5. Generate master report ────────────────────────────────────────
        if not all_results:
            logger.warning("⚠️ [scheduler] No results collected.")
            return

        report_path = generate_master_fraud_report(all_results)
        logger.info(f"✅ [scheduler] Master report: {report_path}")

        # ── 6. Save report blob to DB ────────────────────────────────────────
        with open(report_path, "rb") as f:
            binary_data = f.read()

        filename    = os.path.basename(report_path)
        alert_count = sum(1 for r in all_results if r["score_final"] > 50)
        report_id   = save_report_blob(filename, binary_data, alert_count)
        logger.info(f"🗄️ [scheduler] Report #{report_id} saved to DB.")

        # ── 7. Update last run timestamp ─────────────────────────────────────
        update_last_run()
        logger.info("🏁 [scheduler] Global Audit complete.")

    except Exception as exc:
        logger.error(f"💥 [scheduler] Critical error: {exc}")
    finally:
        db.close()


# ── Background Loop Logic ────────────────────────────────────────────────────

async def scheduler_loop():
    """
    Background loop that checks for scheduled runs.
    Runs every 30 seconds to ensure timing precision.
    """
    logger.info("⏰ [scheduler] Background loop started (precision: 30s).")
    while True:
        try:
            settings = get_settings()
            if not settings:
                await asyncio.sleep(30)
                continue

            freq = settings.get("frequency", "manual")
            if freq == "manual":
                await asyncio.sleep(30)
                continue

            now              = datetime.now()
            current_time_str = now.strftime("%H:%M")
            day_of_week      = now.weekday()  # 0 = Monday

            if now.second < 30:
                logger.debug(
                    f"[scheduler] Check ({now.strftime('%H:%M:%S')}): "
                    f"current={current_time_str} target={settings['scheduled_time']} "
                    f"freq={freq}"
                )

            if current_time_str == settings["scheduled_time"]:
                should_run = False

                if freq == "daily":
                    should_run = True
                elif freq == "weekly":
                    # UI: Sun=0, Mon=1 → Python: Mon=0, Sun=6
                    target_python_day = (settings["day_of_week"] + 6) % 7
                    if day_of_week == target_python_day:
                        should_run = True

                # Avoid running twice on same day (automated runs only)
                last_auto = settings.get("last_auto_run")
                if last_auto and last_auto.date() == now.date():
                    should_run = False

                if should_run:
                    logger.info(f"🚀 [scheduler] Triggering {freq} analysis...")
                    await run_global_analysis_task()
                    update_last_auto_run()

        except Exception as exc:
            logger.warning(f"⚠️ [scheduler] Loop error: {exc}")

        await asyncio.sleep(30)
