"""
output_reports.py — Fraud report output manager.

Stratégie simplifiée (compatible compte Gmail personnel) :
  1. LOCAL  — génère le fichier Excel dans /app/data/reports/
              (monté via docker-compose : ./backend/data:/app/data)
  2. Le fraud-service expose un endpoint GET /reports/{filename}
              pour télécharger le fichier depuis le navigateur.

All monetary thresholds expressed in EUR (international standard).

Variables d'environnement :
  REPORTS_DIR   chemin absolu du dossier de rapports (défaut : /app/data/reports)
  FRAUD_SERVICE_PUBLIC_URL   URL publique du service (défaut : http://localhost:8001)
"""

import os
import io
from pathlib import Path
from datetime import datetime

import pandas as pd
from .auth import generate_report_signature
import time


# ── Config ────────────────────────────────────────────────────────────────────

def _reports_dir() -> Path:
    env = os.getenv("REPORTS_DIR", "").strip()
    if env:
        d = Path(env)
    else:
        candidates = [
            Path("/app/data/reports"),
            Path("/data/reports"),
        ]
        for ancestor in Path(__file__).resolve().parents:
            candidates.append(ancestor / "backend" / "data" / "reports")
            candidates.append(ancestor / "data" / "reports")
        d = candidates[0]

    d.mkdir(parents=True, exist_ok=True)
    return d


def _public_url() -> str:
    return os.getenv("FRAUD_SERVICE_PUBLIC_URL", "http://localhost:8001").rstrip("/")


# ═══════════════════════════════════════════════════════════════════════════════
# GÉNÉRATION EXCEL
# ═══════════════════════════════════════════════════════════════════════════════

# ── Suspicious transaction filter thresholds (EUR) ────────────────────────────
# Updated from TND to EUR.  These are the same bands used by the rule engine:
#   - R1  HIGH_AMOUNT        : > 5 000 EUR  (PSD2)
#   - R4  NIGHT_TX           : 00:00–05:00
#   - R7  BALANCE_DRAIN      : > 80% balance
#   - R10 NEAR_THRESHOLD     : 9 500–9 999 EUR  (AML structuring band)
_FLAGGED_AMOUNT_THRESHOLD = 5_000        # EUR  (was 3 000 TND)
_NEAR_THRESHOLD_LO        = 9_500        # EUR  AML smurfing band lower
_NEAR_THRESHOLD_HI        = 9_999        # EUR  AML smurfing band upper


def _build_fraud_excel(
    df: pd.DataFrame,
    iban: str,
    rule_results: list,
    behavioral_signals: list,
    score_behavioral: int,
    score_aml: int,
    score_final: int,
    risk_level: str,
    tracfin_required: bool,
    target_path: Path,
) -> Path:
    """
    Génère le rapport Excel complet et l'écrit dans target_path.
    Retourne le Path du fichier créé.
    All displayed thresholds are in EUR.
    """
    risk_emoji = {
        "APPROVED": "🟢", "REVIEW": "🟡", "HOLD": "🟠", "BLOCK": "🔴"
    }.get(risk_level, "⚪")

    amount_col = "transaction_amount" if "transaction_amount" in df.columns else "amount"

    with pd.ExcelWriter(target_path, engine="openpyxl") as writer:

        # ── Onglet 1 : Score Résumé ───────────────────────────────────────────
        pd.DataFrame({
            "Métrique": [
                "IBAN analysé",
                "Score comportemental (0-100)",
                "Score AML / règles (0-100)",
                "Score final (0-100)",
                f"Niveau de risque {risk_emoji}",
                "Déclaration TRACFIN requise",
                "Date d'analyse",
                "Transactions analysées",
                "Devise référence",
                "Seuils de décision",
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
                "< 30 APPROVED · 30–59 REVIEW · ≥ 60 BLOCK",
            ],
        }).to_excel(writer, sheet_name="Score Résumé", index=False)

        # ── Onglet 2 : Règles de Détection ────────────────────────────────────
        rules_df = pd.DataFrame(rule_results) if rule_results else pd.DataFrame(
            {"Règle": ["Aucune règle"], "triggered": [False], "points": [0],
             "details": [""], "severity": [""]}
        )
        if not rules_df.empty:
            rules_df = rules_df.rename(columns={
                "rule":      "Règle",
                "triggered": "Déclenchée",
                "points":    "Points",
                "details":   "Détails",
                "severity":  "Sévérité",
            })
        rules_df.to_excel(writer, sheet_name="Règles de Détection", index=False)

        # ── Onglet 3 : Signaux Comportementaux ───────────────────────────────
        if behavioral_signals:
            sig_df = pd.DataFrame(behavioral_signals).rename(columns={
                "signal": "Signal",
                "points": "Points",
                "detail": "Détail",
            })
        else:
            sig_df = pd.DataFrame({"Signal": ["Aucun signal détecté"], "Points": [0]})
        sig_df.to_excel(writer, sheet_name="Signaux Comportementaux", index=False)

        # ── Strip Timezones ──────────────────────────────────────────────────
        export_df = df.copy()
        for col in export_df.select_dtypes(include=["datetimetz", "datetime", "object"]).columns:
            try:
                if pd.api.types.is_datetime64tz_dtype(export_df[col]):
                    export_df[col] = export_df[col].dt.tz_localize(None)
                elif pd.api.types.is_object_dtype(export_df[col]):
                    temp = pd.to_datetime(export_df[col], errors="ignore")
                    if pd.api.types.is_datetime64tz_dtype(temp):
                        export_df[col] = temp.dt.tz_localize(None)
            except Exception:
                pass

        # ── Onglet 4 : Transactions (toutes) ─────────────────────────────────
        export_df.to_excel(writer, sheet_name="Transactions", index=False)

        # ── Onglet 5 : Transactions Suspectes ────────────────────────────────
        # Flagging criteria updated to EUR thresholds:
        #   - Amount > 5 000 EUR         (R1 HIGH_AMOUNT, PSD2)
        #   - Night transaction 00:00–05:00  (R4, unchanged)
        #   - > 80% balance drain        (R7, unchanged)
        #   - Amount in 9 500–9 999 EUR band (R10 AML near-threshold)
        flagged_idx: set = set()

        if amount_col in df.columns:
            amounts = pd.to_numeric(df[amount_col], errors="coerce")
            # R1: > 5 000 EUR
            flagged_idx.update(df[amounts > _FLAGGED_AMOUNT_THRESHOLD].index.tolist())
            # R10: AML near-threshold band
            flagged_idx.update(
                df[amounts.between(_NEAR_THRESHOLD_LO, _NEAR_THRESHOLD_HI)].index.tolist()
            )

        if "timestamp" in df.columns:
            hours = pd.to_datetime(df["timestamp"], errors="coerce").dt.hour
            flagged_idx.update(df[hours.between(0, 4)].index.tolist())

        bal_col = next(
            (c for c in ("account_currentbalance", "account_current_balance") if c in df.columns),
            None,
        )
        if bal_col and amount_col in df.columns:
            amounts  = pd.to_numeric(df[amount_col], errors="coerce")
            balances = pd.to_numeric(df[bal_col], errors="coerce")
            flagged_idx.update(
                df[(balances > 0) & (amounts > 0.8 * balances)].index.tolist()
            )

        flagged_df = (
            df.loc[sorted(flagged_idx)] if flagged_idx
            else pd.DataFrame(columns=df.columns)
        )

        export_flagged_df = flagged_df.copy()
        for col in export_flagged_df.select_dtypes(include=["datetimetz", "datetime", "object"]).columns:
            try:
                if pd.api.types.is_datetime64tz_dtype(export_flagged_df[col]):
                    export_flagged_df[col] = export_flagged_df[col].dt.tz_localize(None)
                elif pd.api.types.is_object_dtype(export_flagged_df[col]):
                    temp = pd.to_datetime(export_flagged_df[col], errors="ignore")
                    if pd.api.types.is_datetime64tz_dtype(temp):
                        export_flagged_df[col] = temp.dt.tz_localize(None)
            except Exception:
                pass

        export_flagged_df.to_excel(writer, sheet_name="Transactions Suspectes", index=False)

        # ── Onglet 6 : Résumé Compte ─────────────────────────────────────────
        amounts_num = pd.to_numeric(df.get(amount_col, pd.Series(dtype=float)), errors="coerce")
        date_range = "N/A"
        if "timestamp" in df.columns and df["timestamp"].notna().any():
            ts = pd.to_datetime(df["timestamp"], errors="coerce")
            date_range = f"{ts.min().strftime('%Y-%m-%d')} → {ts.max().strftime('%Y-%m-%d')}"

        pd.DataFrame({
            "Métrique": [
                "IBAN", "Nb transactions", "Montant total (EUR)",
                "Montant moyen (EUR)", "Montant max (EUR)",
                "Montant min (EUR)", "Période",
            ],
            "Valeur": [
                iban, len(df),
                round(float(amounts_num.sum()), 2),
                round(float(amounts_num.mean()), 2),
                round(float(amounts_num.max()), 2),
                round(float(amounts_num.min()), 2),
                date_range,
            ],
        }).to_excel(writer, sheet_name="Résumé Compte", index=False)

        # ── Onglet 7 : Seuils Réglementaires ─────────────────────────────────
        # Updated to EUR with correct regulatory references
        pd.DataFrame({
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
                "Virement SEPA (5AMLD)",
                "Chèque (5AMLD)",
                "Pays OFAC (auto-BLOCK)",
            ],
            "Seuil (EUR)": [
                "> 5 000 €  (was 3 000 TND)",
                "Pattern regex + pays OFAC",
                "> 20 dépôts < 10 000 € / 7 jours  (was 3 txns / 24h)",
                "00:00–05:00  (inchangé)",
                "Distance > 1 000 km OU pays différent  (was IP fixe 185.230.x.x)",
                "MCC 5541/5999/5311 + > 1 500 €  (inchangé)",
                "> 80 % du solde  (inchangé)",
                "≥ 2 alertes / 7 jours  (was ≥ 3)",
                "Carte > 5 / 1h · Virement > 10 / 10 min  (was > 10 / 1h)",
                "9 500 – 9 999 € (primary) · 4 500–4 999 €  (was > 500 €)",
                "Pays différents < 48h  (inchangé)",
                "Nouveau bénéficiaire + transfert immédiat > 3 000 €  (was > 2 000 €)",
                "Inactif > 90 jours  (inchangé)",
                "———",
                "Espèces > 10 000 € / jour",
                "Toute fraude CB internet",
                "Espèces > 10 000 € sans justificatif",
                "> 15 000 €",
                "> 10 000 €",
                "RU, IR, KP, SY, VE",
            ],
            "Action": [
                "3DS + géoloc obligatoire",
                "BLOCK automatique si OFAC",
                "Déclaration TRACFIN < 30 j",
                "Revue manuelle",
                "Challenge + vérification",
                "Revue manuelle",
                "Blocage temporaire",
                "Escalade compliance",
                "Challenge SMS (carte) · BLOCK auto (virement)",
                "Review AML + TRACFIN si persistant",
                "Analyse layering",
                "Authentification renforcée",
                "KYC renforcé",
                "———",
                "Déclaration TRACFIN < 30 j",
                "Signalement Perceval < 13 mois",
                "REFUS + TRACFIN",
                "Review manuel",
                "Vérif signature",
                "BLOCK automatique",
            ],
            "Score (+pts)": [
                "+30", "+20", "+35", "+10", "+20", "+10",
                "+10", "+25", "+20", "+15", "+12", "+20",
                "+18", "—",
                "—", "—", "—", "—", "—", "Score=100",
            ],
        }).to_excel(writer, sheet_name="Seuils Réglementaires", index=False)

    return target_path


# ═══════════════════════════════════════════════════════════════════════════════
# API PUBLIQUE — appelée depuis nodes.py
# ═══════════════════════════════════════════════════════════════════════════════

def route_fraud_output(
    df: pd.DataFrame,
    iban: str,
    rule_results: list,
    behavioral_signals: list,
    score_behavioral: int,
    score_aml: int,
    score_final: int,
    risk_level: str,
    tracfin_required: bool,
) -> dict:
    """
    Génère le rapport Excel dans /app/data/reports/ et retourne les URLs.

    Retourne:
        {
            "local_path":   str,
            "download_url": str,
            "sheet_url":    None,
            "drive_url":    None,
            "primary_url":  str,
            "errors":       list,
        }
    """
    result = {
        "local_path":   None,
        "download_url": None,
        "sheet_url":    None,
        "drive_url":    None,
        "primary_url":  "",
        "errors":       [],
    }

    try:
        reports_dir = _reports_dir()
        timestamp   = datetime.now().strftime("%Y%m%d_%H%M%S")
        iban_safe   = iban.replace(" ", "_").replace("/", "_")
        filename    = f"fraud_report_{iban_safe}_{timestamp}.xlsx"
        filepath    = reports_dir / filename

        _build_fraud_excel(
            df=df,
            iban=iban,
            rule_results=rule_results,
            behavioral_signals=behavioral_signals,
            score_behavioral=score_behavioral,
            score_aml=score_aml,
            score_final=score_final,
            risk_level=risk_level,
            tracfin_required=tracfin_required,
            target_path=filepath,
        )

        expires = int(time.time()) + 1800 # Valid for 30 minutes
        signature = generate_report_signature(filename, expires)
        download_url = f"{_public_url()}/reports/{filename}?expires={expires}&signature={signature}"
        
        result["local_path"]   = str(filepath)
        result["download_url"] = download_url
        result["primary_url"]  = download_url

        print(f"[fraud] Rapport généré : {filepath}")
        print(f"[fraud] Téléchargement : {download_url}")

    except Exception as e:
        result["errors"].append(f"Local Excel: {e}")
        print(f"[fraud][WARN] Génération rapport échouée: {e}")

    return result
