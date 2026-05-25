# Fraud Rule Lifecycle Management System

## 🎯 Objectif

Système complet de validation, test, approbation et audit pour les règles de détection de fraude, empêchant la création de règles dangereuses ou incohérentes.

---

## 🚀 Fonctionnalités

### ✅ Priorité 1 — IMPLÉMENTÉ

#### 1. **Statuts de règles** (Rule Status)
```
DRAFT      → En cours de création/modification (non évaluée)
TESTING    → En mode sandbox (évaluée mais pas comptée)
ACTIVE     → Live et appliquée
PENDING    → En attente d'approbation
DISABLED   → Temporairement désactivée
ARCHIVED   → Dépréciée (audit only)
```

#### 2. **Validation métier complète**
- ✅ Validation syntaxique du trigger
- ✅ Validation sémantique (cohérence domain/trigger)
- ✅ Validation points/severity alignment
- ✅ Détection de règles trop larges (qui flagueraient tout)
- ✅ Détection de conflits avec règles existantes
- ✅ Vérification paramètres numériques

#### 3. **Endpoint `/test-rule`** (Sandbox)
- ✅ Test en mode sandbox sans affecter le scoring live
- ✅ Métriques : triggered, points, match_rate
- ✅ Warnings et recommandations
- ✅ Historique des tests

#### 4. **Logs d'audit** (Audit Trail)
- ✅ Trace de tous les changements (qui, quand, quoi, pourquoi)
- ✅ Table `fraud_rule_audit_logs`
- ✅ Actions : CREATE, UPDATE, DELETE, ACTIVATE, DEACTIVATE, TEST, APPROVE, REJECT

### 🔄 Priorité 2 — IMPLÉMENTÉ

#### 5. **Versioning**
- ✅ Snapshots automatiques sur changements significatifs
- ✅ Rollback vers versions précédentes
- ✅ Comparaison entre versions
- ✅ Table `fraud_rule_versions`

#### 6. **Sandbox Mode** (TESTING status)
- ✅ Règles évaluées mais points non comptés
- ✅ Métriques collectées pour calibration

#### 7. **Approval Workflow**
- ✅ Soumission pour approbation (DRAFT → PENDING)
- ✅ Approbation/rejet par admin
- ✅ Table `fraud_rule_approvals`
- ✅ Historique des décisions

### 🔮 Priorité 3 — ROADMAP

#### 8. **Calibration automatique**
- 🔲 Suggestion de points basée sur taux de faux positifs
- 🔲 A/B testing entre versions
- 🔲 Auto-tuning du threshold

#### 9. **ML-assisted scoring**
- 🔲 Prédiction de l'impact d'une nouvelle règle
- 🔲 Clustering pour détecter redondances
- 🔲 Anomaly detection sur règles

#### 10. **False Positive Analytics**
- 🔲 Dashboard avec FP rate par règle
- 🔲 Feedback loop (marquer résultats comme FP/TP)
- 🔲 Auto-disable règles avec FP > 30%

---

## 📦 Installation

### 1. Migration DB

```bash
# Appliquer la migration
python /path/to/migration_add_rule_lifecycle.py

# (Optionnel) Rollback
python /path/to/migration_add_rule_lifecycle.py --rollback
```

### 2. Mise à jour `models.py`

Ajouter les champs suivants à `FraudRuleModel` :

```python
from fraud.models import FraudRuleModel

# Ajouter ces colonnes :
status        = Column(String(32),  nullable=False, default="DRAFT")
version       = Column(Integer,     nullable=False, default=1)
created_by    = Column(String(128), nullable=True)
updated_by    = Column(String(128), nullable=True)

triggered_count      = Column(Integer, nullable=False, default=0)
total_evaluations    = Column(Integer, nullable=False, default=0)
false_positive_count = Column(Integer, nullable=False, default=0)

last_triggered = Column(DateTime, nullable=True)
last_tested    = Column(DateTime, nullable=True)
```

### 3. Intégration dans `main.py`

```python
# fraud/main.py

from fraud.rule_router_extended import router as rule_router_extended

app.include_router(rule_router_extended)
```

---

## 🔧 Utilisation

### Créer une règle avec validation

```python
import requests

# POST /rules
response = requests.post(
    "http://localhost:8001/fraud/rules/",
    json={
        "name": "Large crypto transactions",
        "domain": "LIMIT",
        "trigger": "Amount > 5000 TND",
        "triggerDetail": "Cryptocurrency merchants (MCC 6051)",
        "points": 30,
        "severity": "HIGH",
        "description": "Flags large crypto purchases",
        "status": "DRAFT",  # Commence en DRAFT
        "active": False
    }
)

# Réponse inclut warnings si problèmes détectés
{
    "id": "RL-LCT-A3B2C1",
    "name": "Large crypto transactions",
    ...
    "_warnings": [
        "Trigger may not be recognized by rule engine. Test before activating."
    ],
    "_recommendations": []
}
```

### Valider avant de sauvegarder

```python
# POST /rules/validate (sans créer)
response = requests.post(
    "http://localhost:8001/fraud/rules/validate",
    json={
        "name": "Test rule",
        "domain": "LIMIT",
        "trigger": "amount > 1",  # ⚠️ Trop large !
        "points": 100,  # ⚠️ Auto-block !
        "severity": "LOW"  # ⚠️ Incohérent avec points=100
    }
)

# Réponse
{
    "valid": false,
    "errors": [
        "Points cannot be 100 (auto-block). Maximum allowed is 99.",
        "Points 100 inconsistent with severity LOW. Expected range: 1-10.",
        "Trigger too broad: 'amount > 1'. This would flag most/all transactions."
    ],
    "warnings": [],
    "recommendations": []
}
```

### Tester une règle en sandbox

```python
# POST /rules/test
response = requests.post(
    "http://localhost:8001/fraud/rules/test",
    json={
        "rule_id": "RL-LCT-A3B2C1",  # OU rule_data pour tester avant création
        "iban": "TN5914207207100707129648",
        "excel_path": "/app/data/transactions.csv"  # Optionnel
    }
)

# Réponse
{
    "rule_id": "RL-LCT-A3B2C1",
    "rule_name": "Large crypto transactions",
    "triggered": true,
    "points": 30,
    "details": "2 transaction(s) > 5,000 (max: 7,200.00)",
    "transactions_evaluated": 156,
    "match_count": 1,
    "match_rate": 0.006,  # 0.6% des transactions
    "iban": "TN59...",
    "date_range": "2024-01-01 → 2024-04-23",
    "warnings": [],
    "recommendations": [
        "Rule triggered on 0.6% of transactions. Acceptable sensitivity."
    ]
}
```

### Workflow d'approbation

```python
# 1. Soumettre pour approbation
requests.post(
    f"http://localhost:8001/fraud/rules/{rule_id}/submit-for-approval",
    params={"comment": "Ready for production"}
)
# Status: DRAFT → PENDING

# 2. Approuver (admin only)
requests.post(
    f"http://localhost:8001/fraud/rules/{rule_id}/approve",
    json={
        "approved": True,
        "approved_by": "admin_user_123",
        "comment": "Tested successfully, approved for production"
    }
)
# Status: PENDING → ACTIVE, active=True

# 3. Ou rejeter
requests.post(
    f"http://localhost:8001/fraud/rules/{rule_id}/approve",
    json={
        "approved": False,
        "approved_by": "admin_user_123",
        "comment": "FP rate too high, needs tuning"
    }
)
# Status: PENDING → DRAFT, active=False
```

### Versioning et rollback

```python
# Lister les versions
response = requests.get(f"http://localhost:8001/fraud/rules/{rule_id}/versions")
# {
#   "rule_id": "RL-LCT-A3B2C1",
#   "total_versions": 3,
#   "versions": [
#     {"version": 3, "points": 35, "created_at": "2024-05-15T10:30:00", ...},
#     {"version": 2, "points": 30, "created_at": "2024-05-10T14:20:00", ...},
#     {"version": 1, "points": 25, "created_at": "2024-05-01T09:00:00", ...}
#   ]
# }

# Comparer deux versions
response = requests.get(
    f"http://localhost:8001/fraud/rules/{rule_id}/versions/compare",
    params={"v1": 2, "v2": 3}
)
# {
#   "differences": {
#     "points": {"v2": 30, "v3": 35},
#     "severity": {"v2": "HIGH", "v3": "CRITICAL"}
#   }
# }

# Rollback vers version 2
response = requests.post(
    f"http://localhost:8001/fraud/rules/{rule_id}/rollback/2",
    params={"comment": "Reverting due to high FP rate"}
)
# Règle restaurée à l'état de la version 2
```

### Audit trail

```python
# Historique d'une règle spécifique
response = requests.get(f"http://localhost:8001/fraud/rules/{rule_id}/audit")
# {
#   "rule_id": "RL-LCT-A3B2C1",
#   "total": 12,
#   "logs": [
#     {
#       "id": 45,
#       "action": "APPROVE",
#       "changed_by": "admin_123",
#       "changed_at": "2024-05-16T15:30:00",
#       "changes": {"status": {"old": "PENDING", "new": "ACTIVE"}},
#       "comment": "Approved for production"
#     },
#     ...
#   ]
# }

# Changements récents (toutes règles)
response = requests.get("http://localhost:8001/fraud/rules/audit/recent")
```

### Détection de conflits

```python
# Analyser une règle pour conflits/redondances
response = requests.get(f"http://localhost:8001/fraud/rules/{rule_id}/conflicts")
# {
#   "rule_id": "RL-LCT-A3B2C1",
#   "rule_name": "Large crypto transactions",
#   "conflicts": [],
#   "redundancies": [
#     {
#       "other_rule_id": "RL-LAR-B1C2D3",
#       "other_rule_name": "Large amount alert",
#       "similarity": 0.75,
#       "description": "Triggers share 3 keywords: amount, large, transaction"
#     }
#   ],
#   "recommendations": [
#     "Found 1 potentially redundant rule(s). Consider merging or adjusting points."
#   ]
# }
```

---

## 🛡️ Sécurité

### Validations critiques

1. **Points = 100 interdit** → Auto-block, trop dangereux
2. **Triggers trop larges bloqués** → `amount > 1` rejeté
3. **Cohérence points/severity** → `points=50, severity=LOW` rejeté
4. **Forbidden keywords** → `drop`, `delete`, `exec`, `eval` interdits
5. **Nom descriptif requis** → `test`, `new rule` rejetés

### Workflow d'approbation

- **DRAFT/TESTING** → Créées par n'importe qui
- **PENDING** → En attente d'approbation
- **ACTIVE** → Approuvées par admin uniquement

### Audit complet

Chaque action est logged avec :
- `changed_by` (user ID)
- `changed_at` (timestamp)
- `changes` (before/after)
- `ip_address` (optionnel)
- `comment` (optionnel)

---

## 📊 Analytics

### Couverture par domaine

```python
GET /rules/analytics/coverage
# {
#   "total_rules": 15,
#   "active_rules": 12,
#   "by_domain": {
#     "LIMIT": 4,
#     "AML": 3,
#     "VELOCITY": 2,
#     "GEOGRAPHIC": 2,
#     "BEHAVIORAL": 1
#   },
#   "by_severity": {
#     "CRITICAL": 2,
#     "HIGH": 5,
#     "MEDIUM": 4,
#     "LOW": 1
#   },
#   "gaps": []
# }
```

### Performance d'une règle

```python
GET /rules/{rule_id}/performance
# {
#   "rule_id": "RL-LCT-A3B2C1",
#   "rule_name": "Large crypto transactions",
#   "total_evaluations": 1250,
#   "triggered_count": 45,
#   "trigger_rate": 0.036,  # 3.6%
#   "avg_points_per_trigger": 30.0,
#   "total_points_contributed": 1350,
#   "false_positives": 5,
#   "false_positive_rate": 0.11,  # 11% des triggers sont FP
#   "last_triggered": "2024-05-16T14:22:00"
# }
```

---

## 🔗 Architecture

```
Frontend (Angular)
    ↓
FastAPI (/fraud/rules/...)
    ↓
┌─────────────────────────────────────────┐
│ RuleValidationService                   │
│  ├─ RuleValidator                       │
│  ├─ RuleTester (sandbox)                │
│  └─ ConflictAnalyzer                    │
├─────────────────────────────────────────┤
│ RuleAuditService                        │
│ RuleVersioningService                   │
│ RuleApprovalService                     │
│ RuleTestTrackingService                 │
└─────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────┐
│ fraud_rules                             │
│ fraud_rule_audit_logs                   │
│ fraud_rule_versions                     │
│ fraud_rule_approvals                    │
│ fraud_rule_test_runs                    │
└─────────────────────────────────────────┘
```

---

## 📁 Fichiers créés

```
/home/claude/
├── schemas.py                      # Pydantic schemas avec validation
├── validation_service.py           # Service de validation métier
├── models_extended.py              # Nouveaux modèles DB
├── audit_service.py                # Audit, versioning, approval
├── rule_router_extended.py         # Endpoints REST étendus
├── migration_add_rule_lifecycle.py # Migration DB
└── README_RULE_LIFECYCLE.md        # Ce fichier
```

---

## 🚦 Exemples de scénarios

### ❌ Scénario 1 : Règle dangereuse bloquée

```python
# Tentative de création
POST /rules/
{
  "name": "Block everything",
  "domain": "LIMIT",
  "trigger": "amount > 1",
  "points": 100,
  "severity": "CRITICAL"
}

# ❌ REJETÉ
{
  "detail": {
    "message": "Validation failed",
    "errors": [
      "Points cannot be 100 (auto-block). Maximum allowed is 99.",
      "Trigger too broad: 'amount > 1'. This would flag most/all transactions."
    ]
  }
}
```

### ✅ Scénario 2 : Règle avec warnings acceptée

```python
# Création
POST /rules/
{
  "name": "Night transfers to foreign IPs",
  "domain": "GEOGRAPHIC",
  "trigger": "IP starts with 185.230 AND time between 00:00-05:00",
  "points": 25,
  "severity": "HIGH",
  "status": "TESTING"  # Mode sandbox
}

# ✅ CRÉÉE avec warnings
{
  "id": "RL-NTF-X1Y2Z3",
  "_warnings": [
    "Trigger 'IP starts with 185.230 AND time between 00:00-05:00' may not be recognized. Test first."
  ],
  "_recommendations": [
    "Use TESTING status to validate before activating."
  ]
}
```

### 🧪 Scénario 3 : Test → Calibration → Activation

```python
# 1. Test
POST /rules/test
{"rule_id": "RL-NTF-X1Y2Z3", "iban": "TN59..."}

# Résultat : 15% match rate (trop sensible)

# 2. Ajustement
PUT /rules/RL-NTF-X1Y2Z3
{"trigger": "IP starts with 185.230 AND time between 00:00-05:00 AND amount > 2000", ...}

# 3. Re-test
POST /rules/test
# Résultat : 2% match rate (OK)

# 4. Soumission
POST /rules/RL-NTF-X1Y2Z3/submit-for-approval

# 5. Approbation
POST /rules/RL-NTF-X1Y2Z3/approve
{"approved": true, "approved_by": "admin"}

# ✅ ACTIVE
```

---

## 📝 TODO / Roadmap

### Court terme
- [ ] Frontend UI pour workflow d'approbation
- [ ] Dashboard analytics temps réel
- [ ] Notifications Slack/Email sur approvals
- [ ] Export CSV des audit logs

### Moyen terme
- [ ] ML-based trigger suggestions
- [ ] Auto-calibration basée sur FP rate
- [ ] A/B testing entre versions de règles
- [ ] Sandbox mode avec traffic replay

### Long terme
- [ ] Visual rule builder (no-code)
- [ ] Rule marketplace (templates)
- [ ] Multi-tenant support
- [ ] GraphQL API

---

## 🤝 Contribution

Pour proposer une amélioration :
1. Créer une règle en DRAFT
2. La tester avec `/test`
3. Soumettre pour approbation
4. Documenter dans les comments

---

## 📞 Support

- Bugs : Créer un ticket avec logs d'audit
- Questions : Consulter `/rules/analytics/coverage`
- Urgent : Rollback via `/rules/{id}/rollback/{version}`

---

**Auteur** : Fraud Detection Team  
**Version** : 1.0.0  
**Dernière mise à jour** : 2024-05-16
