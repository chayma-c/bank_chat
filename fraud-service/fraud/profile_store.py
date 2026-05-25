"""
profile_store.py — AccountRiskProfile upsert logic.

Shared by nodes.py (per-IBAN interactive analysis) and scheduler.py (batch run).
Keeps the rolling-memory table in sync after every fraud analysis run.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .models import AccountRiskProfile

logger = logging.getLogger(__name__)

# Minimum score to be counted as an "alert" for Rule 8 (REPEATED_ALERTS)
ALERT_THRESHOLD = 30  # REVIEW or higher


def upsert_account_risk_profile(
    db: Session,
    iban: str,
    score_final: int,
    risk_level: str,
    tracfin: bool,
    df: pd.DataFrame,
    window_days: int = 7,
) -> None:
    """
    Insert or update the rolling-memory row for `iban`.

    Called after every analysis run (interactive or scheduled).
    Uses PostgreSQL's ON CONFLICT DO UPDATE for atomicity.

    Args:
        db:           Open SQLAlchemy session (caller owns commit/close).
        iban:         Client IBAN that was just analyzed.
        score_final:  Fraud score from this run (0-100).
        risk_level:   Risk label: APPROVED | REVIEW | HOLD | BLOCK.
        tracfin:      Whether a TRACFIN declaration is required.
        df:           DataFrame of transactions used in this run.
        window_days:  Rolling window size used in this run.
    """
    now = datetime.now(timezone.utc)

    # ── Compute rolling aggregates from the current analysis window ──────────
    amount_col = next(
        (c for c in ("transaction_amount", "amount") if c in df.columns),
        None,
    )
    if amount_col and not df.empty:
        amounts = pd.to_numeric(df[amount_col], errors="coerce").dropna()
        tx_count     = int(len(amounts))
        total_amount = float(amounts.sum())
        avg_amount   = float(amounts.mean()) if not amounts.empty else 0.0
        max_amount   = float(amounts.max()) if not amounts.empty else 0.0
    else:
        tx_count = total_amount = avg_amount = max_amount = 0

    # ── alert_count_7d: count prior alerts from profile, then increment ──────
    # Read existing row to preserve the running counter
    existing = db.query(AccountRiskProfile).filter(
        AccountRiskProfile.client_iban == iban
    ).first()

    if existing:
        prior_alert_count = existing.alert_count_7d or 0
        # Decay: if last analysis was >7 days ago, reset the counter
        if existing.last_analyzed_at:
            last_ts = existing.last_analyzed_at
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            if (now - last_ts) > timedelta(days=7):
                prior_alert_count = 0
    else:
        prior_alert_count = 0

    new_alert_count = prior_alert_count + (1 if score_final >= ALERT_THRESHOLD else 0)

    # ── Upsert via raw psycopg2-style ON CONFLICT ────────────────────────────
    stmt = pg_insert(AccountRiskProfile).values(
        client_iban          = iban,
        last_analyzed_at     = now,
        last_score_final     = score_final,
        last_risk_level      = risk_level,
        last_tracfin         = tracfin,
        rolling_tx_count     = tx_count,
        rolling_total_amount = round(total_amount, 2),
        rolling_avg_amount   = round(avg_amount, 2),
        rolling_max_amount   = round(max_amount, 2),
        rolling_window_days  = window_days,
        alert_count_7d       = new_alert_count,
        created_at           = now,
        updated_at           = now,
    ).on_conflict_do_update(
        index_elements=["client_iban"],
        set_={
            "last_analyzed_at":     now,
            "last_score_final":     score_final,
            "last_risk_level":      risk_level,
            "last_tracfin":         tracfin,
            "rolling_tx_count":     tx_count,
            "rolling_total_amount": round(total_amount, 2),
            "rolling_avg_amount":   round(avg_amount, 2),
            "rolling_max_amount":   round(max_amount, 2),
            "rolling_window_days":  window_days,
            "alert_count_7d":       new_alert_count,
            "updated_at":           now,
        }
    )

    try:
        db.execute(stmt)
        db.commit()
        logger.info(
            f"[profile_store] Upserted profile for {iban}: "
            f"score={score_final} level={risk_level} "
            f"alerts_7d={new_alert_count} window={window_days}d"
        )
    except Exception as exc:
        db.rollback()
        logger.warning(f"[profile_store] Upsert failed for {iban}: {exc}")
