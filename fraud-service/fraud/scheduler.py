import asyncio
import os
import pandas as pd
from datetime import datetime, time
from .db import get_settings, update_last_run, save_report_blob
from .loader import load_transactions, filter_by_iban, list_all_ibans
from .rules import run_all_rules
from .scoring import (
    compute_behavioral_score,
    compute_aml_score,
    compute_final_score,
    check_tracfin_required,
)
from .report import generate_master_fraud_report

# ── Batch Analysis Core ─────────────────────────────────────────────────────

async def run_global_analysis_task():
    """
    Perform a complete scan of the transaction dataset.
    Generates a master report and saves it to the database.
    """
    print("🚀 [scheduler] Starting Global Fraud Audit...")
    try:
        # 1. Get all unique IBANs
        ibans = list_all_ibans()
        if not ibans:
            print("⚠️ [scheduler] No IBANs found in dataset. Aborting.")
            return

        print(f"🧐 [scheduler] Analyzing {len(ibans)} accounts...")
        all_results = []
        
        # We load full transactions once to speed up
        full_df = load_transactions()
        
        for iban in ibans:
            try:
                # Filter and analyze
                df = filter_by_iban(full_df, iban)
                if df.empty:
                    continue
                
                # Rules & Scoring (Logic mirrored from nodes.py analyze_fraud)
                if "timestamp" in df.columns:
                    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
                
                rule_results = run_all_rules(df)
                score_behavioral, behavioral_signals = compute_behavioral_score(df)
                score_aml = compute_aml_score(rule_results)
                score_final, risk_level = compute_final_score(score_behavioral, score_aml)
                tracfin = check_tracfin_required(rule_results, df)
                
                all_results.append({
                    "iban": iban,
                    "score_behavioral": score_behavioral,
                    "score_aml": score_aml,
                    "score_final": score_final,
                    "risk_level": risk_level,
                    "tracfin_required": tracfin,
                    "transactions_count": len(df),
                    "transactions_raw": df.to_dict("records") if score_final > 50 else []
                })
            except Exception as e:
                print(f"❌ [scheduler] Error analyzing IBAN {iban}: {e}")

        # 2. Generate Master Report
        if not all_results:
            print("⚠️ [scheduler] No results collected.")
            return

        report_path = generate_master_fraud_report(all_results)
        print(f"✅ [scheduler] Master report generated at: {report_path}")

        # 3. Save to Database (BLOB)
        with open(report_path, "rb") as f:
            binary_data = f.read()
        
        filename = os.path.basename(report_path)
        alert_count = sum(1 for r in all_results if r["score_final"] > 50)
        
        report_id = save_report_blob(filename, binary_data, alert_count)
        print(f"🗄️ [scheduler] Report #{report_id} saved to database.")

        # 4. Update Last Run
        update_last_run()
        print("🏁 [scheduler] Global Audit complete.")

    except Exception as e:
        print(f"💥 [scheduler] Critical error in global analysis: {e}")

# ── Background Loop Logic ──────────────────────────────────────────────────

async def scheduler_loop():
    """
    Background loop that checks once per minute if a scheduled run is due.
    """
    print("⏰ [scheduler] Background loop started.")
    while True:
        try:
            settings = get_settings()
            if not settings or settings.get("frequency") == "manual":
                await asyncio.sleep(60)
                continue

            now = datetime.now()
            current_time_str = now.strftime("%H:%M")
            day_of_week = now.weekday() # 0 = Monday

            # Check if current time matches scheduled time
            if current_time_str == settings["scheduled_time"]:
                # Check frequency specific constraints
                should_run = False
                if settings["frequency"] == "daily":
                    should_run = True
                elif settings["frequency"] == "weekly":
                    # Mapping: UI (Sun=0, Mon=1) -> Python (Mon=0, Sun=6)
                    # Formula: python_day = (ui_day + 6) % 7
                    target_python_day = (settings["day_of_week"] + 6) % 7
                    if day_of_week == target_python_day:
                        should_run = True
                
                # Check if it already ran today (simple avoidance of double triggers in the same minute)
                last_run = settings.get("last_run")
                if last_run and last_run.date() == now.date():
                    should_run = False

                if should_run:
                    await run_global_analysis_task()
            
        except Exception as e:
            print(f"⚠️ [scheduler] Loop error: {e}")
            
        await asyncio.sleep(60) # Wait 1 minute
