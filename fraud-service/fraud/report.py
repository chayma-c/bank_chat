"""
report.py — Excel report generator for fraud analysis results.
Generates two types of reports:
  1. Full transaction export (all transactions for an IBAN)
  2. Fraud analysis report (transactions + scoring + alerts)

All monetary thresholds expressed in EUR (international standard).
Reference: 5AMLD (EU 2018/843), PSD2 SCA, FATF GAFI 2023.
"""

import os
from pathlib import Path
from datetime import datetime

import pandas as pd

# ── Output directory ──────────────────────────────────────────────────────────
REPORTS_DIR = Path(os.getenv("REPORTS_DIR", "/app/data/reports"))
if not REPORTS_DIR.is_absolute():
    REPORTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "reports"

# ── EUR thresholds (aligned with output_reports.py and rule_engine.py) ────────
# R1  HIGH_AMOUNT        : > 5 000 EUR   (PSD2, was 3 000 TND)
# R10 NEAR_THRESHOLD_LO : 9 500 EUR      (AML 5AMLD structuring band)
# R10 NEAR_THRESHOLD_HI : 9 999 EUR
# R7  BALANCE_DRAIN_RATIO: 80%            (unchanged)
_HIGH_AMOUNT_THRESHOLD = 5_000   # EUR
_NEAR_THRESHOLD_LO     = 9_500   # EUR
_NEAR_THRESHOLD_HI     = 9_999   # EUR
_BALANCE_DRAIN_RATIO   = 0.80


def ensure_reports_dir():
    """Create reports directory if it doesn't exist."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def _strip_tz(df: pd.DataFrame) -> pd.DataFrame:
    """Excel doesn't support TZ-aware datetimes. Strip them."""
    for col in df.select_dtypes(include=["datetimetz", "datetime", "object"]).columns:
        try:
            if pd.api.types.is_datetime64tz_dtype(df[col]):
                df[col] = df[col].dt.tz_localize(None)
            elif pd.api.types.is_object_dtype(df[col]):
                temp = pd.to_datetime(df[col], errors="ignore")
                if pd.api.types.is_datetime64tz_dtype(temp):
                    df[col] = temp.dt.tz_localize(None)
        except Exception:
            pass
    return df


def generate_transaction_export(
    df: pd.DataFrame,
    iban: str,
) -> str:
    """
    Export all transactions for an IBAN to an Excel file.
    Returns the file path.
    """
    ensure_reports_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    iban_safe = iban.replace(" ", "_").replace("/", "_")
    filename  = f"transactions_{iban_safe}_{timestamp}.xlsx"
    filepath  = REPORTS_DIR / filename

    amount_col = "transaction_amount" if "transaction_amount" in df.columns else "amount"
    amounts_num = pd.to_numeric(df.get(amount_col, pd.Series(dtype=float)), errors="coerce")

    with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
        # ── Sheet 1: Transactions ──
        export_df = _strip_tz(df.copy())
        export_df.to_excel(writer, sheet_name="Transactions", index=False)

        # ── Sheet 2: Summary (EUR) ──
        summary_data = {
            "Métrique": [
                "IBAN",
                "Nombre total de transactions",
                "Montant total (EUR)",
                "Montant moyen (EUR)",
                "Montant max (EUR)",
                "Montant min (EUR)",
                "Période",
                "Date d'export",
                "Devise référence",
            ],
            "Valeur": [
                iban,
                len(df),
                round(float(amounts_num.sum()),  2),
                round(float(amounts_num.mean()), 2),
                round(float(amounts_num.max()),  2),
                round(float(amounts_num.min()),  2),
                (f"{df['timestamp'].min()} → {df['timestamp'].max()}"
                 if "timestamp" in df.columns else "N/A"),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "EUR (norme internationale)",
            ],
        }
        pd.DataFrame(summary_data).to_excel(writer, sheet_name="Résumé", index=False)

    return str(filepath)


def generate_master_fraud_report(results: list) -> str:
    """
    Generate a master Excel report aggregating results from multiple IBANs.
    Only includes detailed sheets for accounts with a final score > 50.
    Returns the file path.
    """
    ensure_reports_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename  = f"master_fraud_report_{timestamp}.xlsx"
    filepath  = REPORTS_DIR / filename

    with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
        # ── Sheet 1: Ranking Dashboard ──
        summary_rows = []
        for res in results:
            summary_rows.append({
                "IBAN":              res["iban"],
                "Score final":       res["score_final"],
                "Niveau de risque":  res["risk_level"],
                "TRACFIN requis":    "OUI" if res["tracfin_required"] else "NON",
                "Transactions":      res["transactions_count"],
                "Score AML":         res["score_aml"],
                "Score comportemental": res["score_behavioral"],
                "Devise référence":  "EUR",
            })

        summary_df = pd.DataFrame(summary_rows)
        if not summary_df.empty:
            summary_df = summary_df.sort_values(by="Score final", ascending=False)
        summary_df.to_excel(writer, sheet_name="Ranking Dashboard", index=False)

        # ── Following sheets: detailed analysis for high-risk accounts (> 50) ──
        for res in results:
            if res["score_final"] > 50:
                iban_short  = res["iban"][-10:].replace(" ", "_")
                sheet_prefix = f"Audit_{iban_short}"

                score_summary = pd.DataFrame({
                    "Métrique": ["IBAN", "Score final", "Niveau de risque", "TRACFIN", "Devise"],
                    "Valeur":   [res["iban"], res["score_final"], res["risk_level"],
                                 res["tracfin_required"], "EUR"],
                })
                score_summary.to_excel(writer, sheet_name=f"{sheet_prefix}_Summary", index=False)

                if "transactions_raw" in res and res["transactions_raw"]:
                    df = pd.DataFrame(res["transactions_raw"])
                    df = _strip_tz(df)

                    # Flag high-risk transactions using EUR threshold (> 1 000 EUR)
                    # Note: master report uses a lower cut-off to surface more rows
                    high_risk_tx = df.copy()
                    if "transaction_amount" in high_risk_tx.columns:
                        high_risk_tx = high_risk_tx[
                            pd.to_numeric(high_risk_tx["transaction_amount"], errors="coerce") > 1_000
                        ]

                    if not high_risk_tx.empty:
                        high_risk_tx.to_excel(
                            writer, sheet_name=f"{sheet_prefix}_Tx", index=False
                        )

    return str(filepath)


def generate_fraud_report(
    df: pd.DataFrame,
    iban: str,
    rule_results: list,
    behavioral_signals: list,
    score_behavioral: int,
    score_aml: int,
    score_final: int,
    risk_level: str,
    tracfin_required: bool,
) -> str:
    """
    Generate a comprehensive fraud analysis Excel report.
    Returns the file path.
    All monetary values in EUR.
    """
    ensure_reports_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    iban_safe = iban.replace(" ", "_").replace("/", "_")
    filename  = f"fraud_report_{iban_safe}_{timestamp}.xlsx"
    filepath  = REPORTS_DIR / filename

    amount_col = "transaction_amount" if "transaction_amount" in df.columns else "amount"

    with pd.ExcelWriter(filepath, engine="openpyxl") as writer:

        # ── Sheet 1: Score Summary ──
        risk_emoji = {"APPROVED": "🟢", "REVIEW": "🟡", "HOLD": "🟠", "BLOCK": "🔴"}.get(
            risk_level, "⚪"
        )
        score_data = {
            "Métrique": [
                "IBAN analysé",
                "Score comportemental (0-100)",
                "Score AML / règles (0-100)",
                "Score final (0-100)",
                f"Niveau de risque {risk_emoji}",
                "Déclaration TRACFIN requise",
                "Date d'analyse",
                "Nombre de transactions analysées",
                "Devise référence",
                "Seuils : < 30=APPROVED · 30–59=REVIEW · ≥ 60=BLOCK",
            ],
            "Valeur": [
                iban,
                score_behavioral,
                score_aml,
                score_final,
                risk_level,
                "OUI ⚠️" if tracfin_required else "NON",
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                len(df),
                "EUR (norme internationale)",
                "",
            ],
        }
        pd.DataFrame(score_data).to_excel(writer, sheet_name="Score Résumé", index=False)

        # ── Sheet 2: Rule Results ──
        rules_df = pd.DataFrame(rule_results)
        if not rules_df.empty:
            rules_df = rules_df.rename(columns={
                "rule":      "Règle",
                "triggered": "Déclenchée",
                "points":    "Points",
                "details":   "Détails",
                "severity":  "Sévérité",
            })
        rules_df.to_excel(writer, sheet_name="Règles de Détection", index=False)

        # ── Sheet 3: Behavioral Signals ──
        if behavioral_signals:
            signals_df = pd.DataFrame(behavioral_signals).rename(columns={
                "signal": "Signal", "points": "Points", "detail": "Détail",
            })
        else:
            signals_df = pd.DataFrame({"Signal": ["Aucun signal détecté"], "Points": [0]})
        signals_df.to_excel(writer, sheet_name="Signaux Comportementaux", index=False)

        # ── Sheet 4: All Transactions ──
        export_df = _strip_tz(df.copy())
        export_df.to_excel(writer, sheet_name="Transactions", index=False)

        # ── Sheet 5: Flagged Transactions (EUR thresholds) ────────────────────
        # R1  : amount > 5 000 EUR
        # R4  : night 00:00–05:00
        # R7  : > 80% balance drain
        # R10 : near-threshold 9 500–9 999 EUR
        flagged_indices: set = set()

        if amount_col in df.columns:
            amounts = pd.to_numeric(df[amount_col], errors="coerce")
            # R1 — high amount
            flagged_indices.update(df[amounts > _HIGH_AMOUNT_THRESHOLD].index.tolist())
            # R10 — AML near-threshold band
            flagged_indices.update(
                df[amounts.between(_NEAR_THRESHOLD_LO, _NEAR_THRESHOLD_HI)].index.tolist()
            )

        if "timestamp" in df.columns:
            hours = df["timestamp"].dt.hour
            flagged_indices.update(df[hours.between(0, 4)].index.tolist())

        bal_col = next(
            (c for c in ("account_currentbalance", "account_current_balance") if c in df.columns),
            None,
        )
        if bal_col and amount_col in df.columns:
            amounts    = pd.to_numeric(df[amount_col], errors="coerce")
            balances   = pd.to_numeric(df[bal_col], errors="coerce")
            drain_mask = (balances > 0) & (amounts > _BALANCE_DRAIN_RATIO * balances)
            flagged_indices.update(df[drain_mask].index.tolist())

        flagged_df = (
            df.loc[sorted(flagged_indices)].copy()
            if flagged_indices
            else pd.DataFrame(columns=df.columns)
        )
        flagged_df = _strip_tz(flagged_df)
        flagged_df.to_excel(writer, sheet_name="Transactions Suspectes", index=False)

        # ── Sheet 6: Regulatory Thresholds (updated to EUR) ──────────────────
        reg_data = {
            "Règle / Réglementation": [
                "R1 — HIGH_AMOUNT (PSD2 SCA)",
                "R2 — SUSPICIOUS_IBAN (OFAC)",
                "R3 — STRUCTURING / SMURFING (5AMLD Art.11)",
                "R4 — NIGHT_TRANSACTION",
                "R5 — FOREIGN_IP / Géofencing",
                "R6 — HIGH_RISK_MCC",
                "R7 — BALANCE_DRAIN",
                "R8 — REPEATED_ALERTS (FATF Rec. 20)",
                "R9 — VELOCITY_HIGH (5AMLD)",
                "R10 — ROUND_AMOUNT / Near-threshold (AML)",
                "R11 — CROSS_BORDER",
                "R12 — NEW_BENEFICIARY_HIGH",
                "R13 — DORMANT_ACCOUNT",
                "———",
                "TRACFIN — 5AMLD Art.33",
                "Perceval (France)",
                "Dépôt espèces (5AMLD)",
                "Virement SEPA",
                "Chèque",
                "Pays OFAC sanctionnés",
            ],
            "Seuil (EUR)": [
                "> 5 000 €  (PSD2, was 3 000 TND)",
                "Regex + pays OFAC (RU/IR/KP/SY/VE)",
                "> 20 dépôts < 10 000 € / 7 jours  (was 3 txns / 24h)",
                "00:00–05:00  (inchangé)",
                "Distance > 1 000 km OU pays différent  (was IP 185.230.x.x)",
                "MCC 5541/5999/5311 + > 1 500 €  (inchangé)",
                "> 80 % du solde  (inchangé)",
                "≥ 2 alertes / 7 jours  (was ≥ 3 — FATF Rec. 20)",
                "Carte > 5 / 1h · Virement > 10 / 10 min  (was > 10 / 1h)",
                "9 500–9 999 € (primary) · 4 500–4 999 €  (was > 500 €)",
                "Pays différents < 48h  (inchangé)",
                "Nouveau bénéf. + transfert immédiat > 3 000 €  (was > 2 000 €)",
                "Inactif > 90 jours  (inchangé)",
                "———",
                "Espèces > 10 000 € / jour",
                "Toute fraude CB internet",
                "Espèces > 10 000 € sans justificatif",
                "> 15 000 €",
                "> 10 000 €",
                "RU, IR, KP, SY, VE → BLOCK auto",
            ],
            "Action": [
                "3DS + géoloc obligatoire",
                "BLOCK auto si OFAC",
                "Déclaration TRACFIN < 30 j",
                "Revue manuelle",
                "Challenge + vérification",
                "Revue manuelle",
                "Blocage temporaire",
                "Escalade compliance",
                "Challenge SMS (carte) · BLOCK auto (virement)",
                "Review AML + TRACFIN si persistant",
                "Analyse layering",
                "Auth renforcée",
                "KYC renforcé",
                "———",
                "TRACFIN < 30 j",
                "Perceval < 13 mois",
                "REFUS + TRACFIN",
                "Review manuel",
                "Vérif signature",
                "BLOCK automatique",
            ],
            "Score (+pts)": [
                "+30", "+20", "+35", "+10", "+20", "+10",
                "+10", "+25", "+20", "+15", "+12", "+20",
                "+18", "—",
                "—", "—", "—", "—", "—", "Score = 100",
            ],
        }
        pd.DataFrame(reg_data).to_excel(writer, sheet_name="Seuils Réglementaires", index=False)

    return str(filepath)
