"""
Excel report generator for fraud analysis results.
Generates two types of reports:
  1. Full transaction export (all transactions for an IBAN)
  2. Fraud analysis report (transactions + scoring + alerts)
"""

import os
from pathlib import Path
from datetime import datetime

import pandas as pd

# ── Output directory ──────────────────────────────────────────────────────────
REPORTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "reports"


def ensure_reports_dir():
    """Create reports directory if it doesn't exist."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)


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
    filename = f"transactions_{iban_safe}_{timestamp}.xlsx"
    filepath = REPORTS_DIR / filename

    with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
        # ── Sheet 1: Transactions ──
        df.to_excel(writer, sheet_name="Transactions", index=False)

        # ── Sheet 2: Summary ──
        summary_data = {
            "Métrique": [
                "IBAN", "Nombre total de transactions",
                "Montant total", "Montant moyen",
                "Montant max", "Montant min",
                "Période", "Date d'export"
            ],
            "Valeur": [
                iban, len(df),
                round(pd.to_numeric(df.get("transaction_amount", pd.Series(dtype=float)), errors="coerce").sum(), 2),
                round(pd.to_numeric(df.get("transaction_amount", pd.Series(dtype=float)), errors="coerce").mean(), 2),
                round(pd.to_numeric(df.get("transaction_amount", pd.Series(dtype=float)), errors="coerce").max(), 2),
                round(pd.to_numeric(df.get("transaction_amount", pd.Series(dtype=float)), errors="coerce").min(), 2),
                f"{df['timestamp'].min()} → {df['timestamp'].max()}" if "timestamp" in df.columns else "N/A",
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ]
        }
        pd.DataFrame(summary_data).to_excel(writer, sheet_name="Résumé", index=False)

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
    """
    ensure_reports_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    iban_safe = iban.replace(" ", "_").replace("/", "_")
    filename = f"fraud_report_{iban_safe}_{timestamp}.xlsx"
    filepath = REPORTS_DIR / filename

    with pd.ExcelWriter(filepath, engine="openpyxl") as writer:

        # ── Sheet 1: Score Summary ──
        risk_emoji = {"APPROVED": "🟢", "REVIEW": "🟡", "HOLD": "🟠", "BLOCK": "🔴"}.get(risk_level, "⚪")
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
                "Seuils: <30=APPROVED | 30-59=REVIEW | ≥60=BLOCK",
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
                "",
            ]
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
            signals_df = pd.DataFrame(behavioral_signals)
            signals_df = signals_df.rename(columns={
                "signal": "Signal",
                "points": "Points",
                "detail": "Détail",
            })
        else:
            signals_df = pd.DataFrame({"Signal": ["Aucun signal détecté"], "Points": [0]})
        signals_df.to_excel(writer, sheet_name="Signaux Comportementaux", index=False)

        # ── Sheet 4: All Transactions ──
        df.to_excel(writer, sheet_name="Transactions", index=False)

        # ── Sheet 5: Flagged Transactions ──
        # Flagged = transactions that triggered the high-score rules
        flagged_indices = set()
        amount_col = "transaction_amount" if "transaction_amount" in df.columns else "amount"

        if amount_col in df.columns:
            amounts = pd.to_numeric(df[amount_col], errors="coerce")
            flagged_indices.update(df[amounts > 3_000].index.tolist())

        if "timestamp" in df.columns:
            hours = df["timestamp"].dt.hour
            flagged_indices.update(df[hours.between(0, 4)].index.tolist())

        if "account_currentbalance" in df.columns and amount_col in df.columns:
            amounts      = pd.to_numeric(df[amount_col], errors="coerce")
            balances     = pd.to_numeric(df["account_currentbalance"], errors="coerce")
            drain_mask   = (balances > 0) & (amounts > 0.8 * balances)
            flagged_indices.update(df[drain_mask].index.tolist())

        if flagged_indices:
            flagged_df = df.loc[sorted(flagged_indices)]
        else:
            flagged_df = pd.DataFrame(columns=df.columns)

        flagged_df.to_excel(writer, sheet_name="Transactions Suspectes", index=False)

        # ── Sheet 6: Regulatory Thresholds Reference ──
        reg_data = {
            "Réglementation": [
                "5AMLD (UE)", "Perceval (France)", "PSD2 SCA",
                "Vélocité Carte", "Vélocité Virement", "Dépôt Espèces AML",
                "Carte Haute Valeur", "Virement SEPA", "Chèque",
            ],
            "Seuil": [
                "Espèces >10,000€/jour", "Toute fraude CB internet",
                "100% paiements >30€", ">5 txs/1h", ">10 txs/10min",
                ">20× <10k€/24h", ">5,000€", ">15,000€", ">10,000€",
            ],
            "Action": [
                "Déclaration TRACFIN <30j", "Signalement Perceval <13 mois",
                "Authentification forte", "Challenge SMS", "BLOCK auto",
                "Review AML", "3DS + géoloc", "Review manuel", "Vérif signature",
            ]
        }
        pd.DataFrame(reg_data).to_excel(writer, sheet_name="Seuils Réglementaires", index=False)

    return str(filepath)