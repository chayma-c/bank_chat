"""
output_reports.py — Fraud report output manager.

Stratégie simplifiée (compatible compte Gmail personnel) :
  1. LOCAL  — génère le fichier Excel dans /app/data/reports/
              (monté via docker-compose : ./backend/data:/app/data)
  2. Le fraud-service expose un endpoint GET /reports/{filename}
              pour télécharger le fichier depuis le navigateur.

Plus besoin de Google Drive / Shared Drive / quota SA.

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
    """
    Dossier où les rapports Excel sont sauvegardés.
    Dans Docker : /app/data/reports  (monté → ./backend/data/reports sur l'hôte)
    En local    : ./data/reports (relatif au repo)
    """
    env = os.getenv("REPORTS_DIR", "").strip()
    if env:
        d = Path(env)
    else:
        # Chercher /app/data/reports (Docker) ou backend/data/reports (local)
        candidates = [
            Path("/app/data/reports"),
            Path("/data/reports"),
        ]
        # Remonter les dossiers parents pour trouver backend/data/reports
        for ancestor in Path(__file__).resolve().parents:
            candidates.append(ancestor / "backend" / "data" / "reports")
            candidates.append(ancestor / "data" / "reports")

        # Prendre /app/data/reports en priorité (Docker)
        d = candidates[0]

    d.mkdir(parents=True, exist_ok=True)
    return d


def _public_url() -> str:
    """URL de base du fraud-service (pour construire le lien de téléchargement)."""
    return os.getenv("FRAUD_SERVICE_PUBLIC_URL", "http://localhost:8001").rstrip("/")


# ═══════════════════════════════════════════════════════════════════════════════
# GÉNÉRATION EXCEL
# ═══════════════════════════════════════════════════════════════════════════════

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
                "Seuils",
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
                "<30 APPROVED · 30-59 REVIEW · ≥60 BLOCK",
            ],
        }).to_excel(writer, sheet_name="Score Résumé", index=False)

        # ── Onglet 2 : Règles de Détection ────────────────────────────────────
        rules_df = pd.DataFrame(rule_results) if rule_results else pd.DataFrame(
            {"Règle": ["Aucune règle"], "triggered": [False], "points": [0],
             "details": [""], "severity": [""]}
        )
        if not rules_df.empty:
            rules_df = rules_df.rename(columns={
                "rule": "Règle", "triggered": "Déclenchée",
                "points": "Points", "details": "Détails", "severity": "Sévérité",
            })
        rules_df.to_excel(writer, sheet_name="Règles de Détection", index=False)

        # ── Onglet 3 : Signaux Comportementaux ───────────────────────────────
        if behavioral_signals:
            sig_df = pd.DataFrame(behavioral_signals).rename(columns={
                "signal": "Signal", "points": "Points", "detail": "Détail",
            })
        else:
            sig_df = pd.DataFrame({"Signal": ["Aucun signal détecté"], "Points": [0]})
        sig_df.to_excel(writer, sheet_name="Signaux Comportementaux", index=False)

        # ── Onglet 4 : Transactions (toutes) ─────────────────────────────────
        df.to_excel(writer, sheet_name="Transactions", index=False)

        # ── Onglet 5 : Transactions Suspectes ────────────────────────────────
        flagged_idx: set = set()
        if amount_col in df.columns:
            amounts = pd.to_numeric(df[amount_col], errors="coerce")
            flagged_idx.update(df[amounts > 3_000].index.tolist())
        if "timestamp" in df.columns:
            hours = pd.to_datetime(df["timestamp"], errors="coerce").dt.hour
            flagged_idx.update(df[hours.between(0, 4)].index.tolist())
        if "account_currentbalance" in df.columns and amount_col in df.columns:
            amounts  = pd.to_numeric(df[amount_col], errors="coerce")
            balances = pd.to_numeric(df["account_currentbalance"], errors="coerce")
            flagged_idx.update(
                df[(balances > 0) & (amounts > 0.8 * balances)].index.tolist()
            )
        flagged_df = (
            df.loc[sorted(flagged_idx)] if flagged_idx
            else pd.DataFrame(columns=df.columns)
        )
        flagged_df.to_excel(writer, sheet_name="Transactions Suspectes", index=False)

        # ── Onglet 6 : Résumé Compte ─────────────────────────────────────────
        amounts_num = pd.to_numeric(df.get(amount_col, pd.Series(dtype=float)),
                                    errors="coerce")
        date_range = "N/A"
        if "timestamp" in df.columns and df["timestamp"].notna().any():
            ts = pd.to_datetime(df["timestamp"], errors="coerce")
            date_range = f"{ts.min().strftime('%Y-%m-%d')} → {ts.max().strftime('%Y-%m-%d')}"

        pd.DataFrame({
            "Métrique": [
                "IBAN", "Nb transactions", "Montant total",
                "Montant moyen", "Montant max", "Montant min", "Période",
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
        pd.DataFrame({
            "Réglementation": [
                "5AMLD (UE)", "Perceval (France)", "PSD2 SCA",
                "Vélocité Carte", "Vélocité Virement",
                "Dépôt Espèces AML", "Carte Haute Valeur",
                "Virement SEPA", "Chèque",
            ],
            "Seuil": [
                "Espèces >10 000 €/jour", "Toute fraude CB internet",
                "100% paiements >30 €", ">5 txs/1 h", ">10 txs/10 min",
                ">20× <10 k€/24 h", ">5 000 €", ">15 000 €", ">10 000 €",
            ],
            "Action": [
                "Déclaration TRACFIN <30j", "Signalement Perceval <13 mois",
                "Authentification forte", "Challenge SMS", "BLOCK auto",
                "Review AML", "3DS + géoloc", "Review manuel", "Vérif signature",
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
            "local_path":   str,   # chemin absolu dans le container
            "download_url": str,   # URL cliquable  http://host:8001/reports/xxx.xlsx
            "sheet_url":    None,  # non utilisé (pas de Google Sheets)
            "drive_url":    None,  # non utilisé (pas de Google Drive)
            "primary_url":  str,   # = download_url
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