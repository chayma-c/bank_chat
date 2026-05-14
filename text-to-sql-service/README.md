# 🧠 Text-to-SQL Agent — Service de Requêtage en Langage Naturel

## 📋 Vue d'Ensemble

Le **Text-to-SQL Agent** est un microservice intelligent qui transforme des questions en langage naturel en requêtes SQL sécurisées, les exécute sur la base de données bancaire, et retourne des résultats formatés avec des explications métier.

```
┌─────────────────────────────────────────────────────────────────────┐
│                        ARCHITECTURE GLOBALE                          │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  [Utilisateur]  →  "Combien de transactions sont bloquées ?"        │
│        ↓                                                              │
│  [Orchestrator Django] → Détecte l'intention "text_to_sql"          │
│        ↓                                                              │
│  [Text-to-SQL Service:8003] ← JWT Keycloak (bank_agent/admin)       │
│        ↓                                                              │
│   1. 🧠 LLM: Génère SQL SELECT                                       │
│   2. 🛡️ Validator: Vérifie sécurité                                  │
│   3. 🗄️ PostgreSQL: Exécute requête (banking_data)                  │
│   4. 📊 LLM: Génère explication métier                               │
│        ↓                                                              │
│  [Résultat] → "45 transactions bloquées (statut='blocked')"         │
│                                                                       │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 🎯 Fonctionnalités Principales

### 1. 🗣️ **Compréhension du Langage Naturel**

L'agent comprend des questions en français et en anglais grâce à un modèle LLM (Groq/Llama) :

**Exemples de questions supportées :**
```
✅ "Combien de transactions sont frauduleuses ?"
✅ "Trouve les transactions qui ont été bloquées"
✅ "Quelles sont les règles AML actives ?"
✅ "Quel est le montant total des virements ce mois-ci ?"
✅ "Liste les 10 IBANs avec le score de risque le plus élevé"
```

**Pipeline de compréhension :**
```python
Question NL → Extraction contexte → Génération SQL → Validation → Exécution
```

---

### 2. 🛡️ **Génération SQL Sécurisée**

Le système génère uniquement des requêtes **SELECT** en lecture seule avec validation stricte.

#### ✅ **Requêtes Autorisées**

```sql
-- ✅ Simple SELECT
SELECT * FROM transactions WHERE status = 'blocked' LIMIT 100

-- ✅ Agrégation
SELECT COUNT(*) as total FROM transactions WHERE is_fraudulent = TRUE

-- ✅ Jointures (tables autorisées uniquement)
SELECT t.*, f.name as rule_name 
FROM transactions t 
JOIN fraud_rules f ON t.fraud_score > 0.7

-- ✅ GROUP BY et ORDER BY
SELECT merchant_category, COUNT(*) as count, SUM(amount) as total
FROM transactions 
WHERE status = 'blocked'
GROUP BY merchant_category
ORDER BY count DESC
LIMIT 10
```

#### ❌ **Requêtes Bloquées**

```sql
-- ❌ Modification de données
DELETE FROM transactions WHERE id = 123
UPDATE transactions SET status = 'approved' WHERE ...
INSERT INTO transactions VALUES (...)
DROP TABLE transactions

-- ❌ Tables système
SELECT * FROM pg_user
SELECT * FROM information_schema.tables

-- ❌ Fonctions dangereuses
SELECT pg_read_file('/etc/passwd')
SELECT pg_sleep(10000)

-- ❌ Requêtes multiples (stacked queries)
SELECT * FROM transactions; DROP TABLE fraud_rules;

-- ❌ CROSS JOIN sans condition (produits cartésiens)
SELECT * FROM transactions CROSS JOIN fraud_rules
```

---

### 3. 🔒 **Validation de Sécurité SQL**

Chaque requête passe par un **validateur multi-niveaux** avant exécution :

```python
# validator.py - Pipeline de validation

def validate_sql(sql: str) -> ValidationResult:
    ✅ 1. Normalisation (trim, suppression ';')
    ✅ 2. Détection requêtes multiples (stacked queries)
    ✅ 3. Vérification SELECT uniquement
    ✅ 4. Blocage mots-clés DML/DDL (DELETE, UPDATE, DROP...)
    ✅ 5. Blocage tables système (pg_*, information_schema)
    ✅ 6. Blocage fonctions dangereuses (pg_read_file, pg_sleep...)
    ✅ 7. Vérification whitelist tables
    ✅ 8. Réécriture SELECT * → colonnes explicites
    ✅ 9. Détection colonnes hallucinées (inexistantes)
    ✅ 10. Ajout LIMIT automatique (protection DoS)
```

**Exemple de validation :**

```python
# ❌ Requête rejetée
INPUT:  "DROP TABLE transactions; SELECT * FROM users"
OUTPUT: ValidationResult(
    is_valid=False,
    error="Requêtes multiples (;) non autorisées."
)

# ✅ Requête nettoyée
INPUT:  "SELECT * FROM transactions WHERE amount > 5000"
OUTPUT: ValidationResult(
    is_valid=True,
    cleaned_sql="SELECT id, transaction_id, user_id, amount, status, created_at FROM transactions WHERE amount > 5000 LIMIT 100",
    warnings=["ℹ️ SELECT * remplacé par colonnes explicites", "ℹ️ LIMIT 100 ajouté automatiquement"]
)
```

---

### 4. 🗄️ **Exécution Sécurisée des Requêtes**

Les requêtes sont exécutées avec plusieurs protections :

```python
# Connexion READ-ONLY
conn.set_session(readonly=True)

# Timeout protection (30 secondes max)
cursor.execute(f"SET statement_timeout = {SQL_TIMEOUT_MS}")

# Row limit enforcement (100 lignes max par défaut)
results = cursor.fetchmany(SQL_MAX_ROWS + 1)
if len(results) > SQL_MAX_ROWS:
    truncated = True
    results = results[:SQL_MAX_ROWS]
```

**Mécanismes de protection :**
- ✅ **READ-ONLY** : Impossible de modifier les données
- ✅ **TIMEOUT** : 30s max par requête (configurable via `SQL_TIMEOUT_MS`)
- ✅ **ROW LIMIT** : 100 lignes max (configurable via `SQL_MAX_ROWS`)
- ✅ **CONNECTION POOL** : Gestion optimisée des connexions PostgreSQL
- ✅ **ROLLBACK** : Toujours appelé après exécution (même en READ-ONLY)

---

### 5. 📊 **Génération de Réponses Métier**

Le système génère automatiquement une explication professionnelle des résultats en français :

```python
# Exemple de réponse générée

Question: "Combien de transactions ont été bloquées ce mois-ci ?"

SQL Généré:
SELECT COUNT(*) as total_blocked 
FROM transactions 
WHERE status = 'blocked' 
  AND created_at >= DATE_TRUNC('month', CURRENT_DATE)
LIMIT 1

Résultat:
| total_blocked |
|---------------|
| 127           |

Explication Métier (générée par LLM):
"📊 **Analyse des Transactions Bloquées**

Au cours du mois actuel, **127 transactions** ont été automatiquement bloquées 
par le système de détection de fraude. Ces blocages sont déclenchés lorsqu'une 
transaction présente des caractéristiques suspectes détectées par les règles 
de sécurité actives.

**Actions recommandées :**
- Consulter les détails via le service de fraude pour identifier les motifs
- Vérifier si des faux positifs nécessitent un déblocage manuel
- Analyser les tendances pour ajuster les règles si nécessaire"
```

---

### 6. 🎯 **Base de Données du Text-to-SQL Agent**

Le service accède à la base `banking_data` avec **3 tables whitelistées** :

#### 📋 **Table: transactions**

Transactions bancaires avec scoring de fraude.

```sql
CREATE TABLE transactions (
    id                 SERIAL PRIMARY KEY,
    transaction_id     VARCHAR(255) UNIQUE NOT NULL,
    user_id            VARCHAR(255) NOT NULL,
    amount             DECIMAL(15, 2) NOT NULL,
    currency           VARCHAR(3) DEFAULT 'EUR',
    transaction_type   VARCHAR(50) NOT NULL,  -- transfer, payment, withdrawal, deposit
    status             VARCHAR(20) DEFAULT 'pending',  -- pending, completed, failed, blocked
    fraud_score        DECIMAL(5, 4),  -- Score ML entre 0 et 1
    is_fraudulent      BOOLEAN DEFAULT FALSE,
    merchant_name      VARCHAR(255),
    merchant_category  VARCHAR(100),  -- gambling, luxury, crypto, etc.
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Index pour optimiser les requêtes fréquentes
CREATE INDEX idx_transactions_user_id ON transactions(user_id);
CREATE INDEX idx_transactions_status ON transactions(status);
CREATE INDEX idx_transactions_fraud_score ON transactions(fraud_score);
CREATE INDEX idx_transactions_created_at ON transactions(created_at);
```

#### 🚨 **Table: fraud_rules**

Règles de détection de fraude configurables.

```sql
CREATE TABLE fraud_rules (
    id             VARCHAR(64) PRIMARY KEY,
    name           VARCHAR(255) NOT NULL,
    domain         VARCHAR(64) NOT NULL,  -- VELOCITY, LIMIT, GEOGRAPHIC, AML, BEHAVIORAL
    trigger        VARCHAR(512) NOT NULL,
    trigger_detail VARCHAR(512) DEFAULT '',
    points         INTEGER NOT NULL DEFAULT 10 CHECK (points BETWEEN 0 AND 100),
    severity       VARCHAR(32) NOT NULL DEFAULT 'MEDIUM',  -- LOW, MEDIUM, HIGH, CRITICAL
    active         BOOLEAN NOT NULL DEFAULT TRUE,
    description    TEXT DEFAULT '',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_fraud_rules_domain ON fraud_rules(domain);
CREATE INDEX idx_fraud_rules_active ON fraud_rules(active);
```

#### 📜 **Table: fraud_decision_logs**

Historique des analyses de fraude par IBAN.

```sql
CREATE TABLE fraud_decision_logs (
    id                     VARCHAR(64) PRIMARY KEY,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_id                VARCHAR(128),
    session_id             VARCHAR(128),
    iban                   VARCHAR(64) NOT NULL,
    transactions_count     INTEGER DEFAULT 0,
    date_range             VARCHAR(64),
    score_behavioral       INTEGER DEFAULT 0,
    score_aml              INTEGER DEFAULT 0,
    score_final            INTEGER DEFAULT 0,
    risk_level             VARCHAR(32),  -- APPROVED, REVIEW, HOLD, BLOCK
    tracfin_required       BOOLEAN NOT NULL DEFAULT FALSE,
    rules_triggered        INTEGER DEFAULT 0,
    rules_evaluated        INTEGER DEFAULT 0,
    triggered_rules_detail JSONB,
    report_path            VARCHAR(512),
    download_url           VARCHAR(512),
    mail_sent              BOOLEAN NOT NULL DEFAULT FALSE,
    mail_recipient         VARCHAR(255),
    mail_template          VARCHAR(64),
    mail_status            VARCHAR(16),
    mail_id                VARCHAR(64),
    llm_summary            TEXT,
    error                  TEXT
);

CREATE INDEX idx_decision_logs_created_at ON fraud_decision_logs(created_at DESC);
CREATE INDEX idx_decision_logs_iban ON fraud_decision_logs(iban);
CREATE INDEX idx_decision_logs_risk_level ON fraud_decision_logs(risk_level);
```

---

## 🎪 Cas d'Usage Réels

### 1. 🚨 **Détection de Fraude**

```
Q: "Trouve les transactions suspectes avec un score > 0.8"
→ SQL: SELECT transaction_id, user_id, amount, fraud_score, merchant_name 
       FROM transactions 
       WHERE fraud_score > 0.8 
       ORDER BY fraud_score DESC 
       LIMIT 100

Q: "Quelles sont les transactions bloquées ce mois-ci ?"
→ Détecte automatiquement le statut 'blocked' et la période courante
```

### 2. 👤 **Support Client**

```
Q: "Combien de transactions ont échoué aujourd'hui ?"
→ Filtre status='failed' + date du jour

Q: "Historique des paiements de l'utilisateur USER789"
→ Filtre user_id='USER789' + ORDER BY created_at DESC
```

### 3. 📊 **Reporting**

```
Q: "Quel est le montant total des virements par mois ?"
→ GROUP BY DATE_TRUNC('month', created_at), SUM(amount)

Q: "Top 5 des marchands par volume de transactions"
→ GROUP BY merchant_name, COUNT(*), ORDER BY count DESC LIMIT 5
```

### 4. 📋 **Audit & Compliance**

```
Q: "Combien de signalements TRACFIN ont été générés ?"
→ SELECT COUNT(*) FROM fraud_decision_logs WHERE tracfin_required = TRUE

Q: "Quelles règles AML sont actuellement actives ?"
→ SELECT * FROM fraud_rules WHERE domain = 'AML' AND active = TRUE
```

### 5. 📈 **Analyse Métier**

```
Q: "Tendance des scores de fraude sur les 30 derniers jours"
→ GROUP BY DATE(created_at), AVG(fraud_score), DATE >= NOW() - INTERVAL '30 days'

Q: "Répartition des niveaux de risque (APPROVED/REVIEW/HOLD/BLOCK)"
→ SELECT risk_level, COUNT(*) FROM fraud_decision_logs GROUP BY risk_level
```

---

## 🔐 Authentification & Autorisations

### **JWT Keycloak Requis**

Le service est **réservé aux agents bancaires et administrateurs** uniquement.

```python
# auth.py - Vérification des rôles

ALLOWED_ROLES = frozenset({"bank_agent", "admin"})

async def require_bank_agent(creds: HTTPAuthorizationCredentials):
    """
    Valide le JWT Keycloak et vérifie que l'utilisateur a le rôle
    'bank_agent' ou 'admin' dans realm_access.roles
    
    Raises:
        401 — Token manquant, expiré ou invalide
        403 — Token valide mais rôle insuffisant (ex: role 'client')
    """
    # 1. Vérifier présence du token
    # 2. Décoder et valider signature RS256
    # 3. Vérifier expiration
    # 4. Extraire realm_access.roles
    # 5. Bloquer si pas bank_agent ou admin
```

**Flux d'authentification :**

```
┌──────────────┐
│   Frontend   │
│  (Angular)   │
└──────┬───────┘
       │ 1. Login → Keycloak
       ↓
┌──────────────┐
│   Keycloak   │  Génère JWT avec realm_access.roles: ["bank_agent"]
└──────┬───────┘
       │ 2. JWT token
       ↓
┌──────────────┐
│ Orchestrator │  Reçoit JWT, le forward à text-to-sql-service
│   (Django)   │
└──────┬───────┘
       │ 3. POST /sql/query + Authorization: Bearer <JWT>
       ↓
┌──────────────────┐
│ text-to-sql :8003│  Valide JWT, vérifie rôles, exécute requête
└──────────────────┘
```

**Niveaux d'accès :**

| Rôle        | Accès Text-to-SQL | Accès Fraud Service | Accès Mail Service |
|-------------|-------------------|---------------------|--------------------|
| `client`    | ❌ Refusé (403)   | ❌ Refusé (403)     | ❌ Refusé (403)    |
| `bank_agent`| ✅ Autorisé       | ✅ Autorisé         | ✅ Autorisé        |
| `admin`     | ✅ Autorisé       | ✅ Autorisé         | ✅ Autorisé        |

---

## 🚀 API Endpoints

### **POST /query** — Requête principale

Convertit une question NL en SQL, l'exécute et retourne les résultats formatés.

**Request :**
```json
{
  "question": "Combien de transactions sont frauduleuses ce mois-ci ?",
  "user_id": "agent_123",
  "limit": 100
}
```

**Response (Success) :**
```json
{
  "status": "success",
  "question": "Combien de transactions sont frauduleuses ce mois-ci ?",
  "sql": "SELECT COUNT(*) as total FROM transactions WHERE is_fraudulent = TRUE AND created_at >= DATE_TRUNC('month', CURRENT_DATE) LIMIT 1",
  "results": [{"total": 42}],
  "row_count": 1,
  "truncated": false,
  "explanation": "📊 Il y a actuellement **42 transactions** marquées comme frauduleuses...",
  "warnings": [],
  "duration_ms": 145.2
}
```

**Response (Error - Validation) :**
```json
{
  "status": "error",
  "sql": "DELETE FROM transactions WHERE id = 1",
  "error": "Mot-clé interdit détecté : DELETE. Seules les requêtes SELECT sont autorisées.",
  "warnings": []
}
```

**Response (Error - Auth) :**
```json
HTTP 403 Forbidden
{
  "detail": "Access denied: 'bank_agent' or 'admin' role required. This feature is reserved for bank staff only."
}
```

---

### **GET /schema** — Schéma de la base

Retourne la liste des tables accessibles et leurs colonnes.

**Response :**
```json
{
  "database": "banking_data",
  "allowed_tables": ["transactions", "fraud_rules", "fraud_decision_logs"],
  "tables": {
    "transactions": {
      "description": "Bank transactions with fraud scoring",
      "columns": [
        {"name": "id", "type": "SERIAL", "description": "Primary key"},
        {"name": "transaction_id", "type": "VARCHAR", "description": "Unique identifier"},
        ...
      ]
    }
  }
}
```

---

### **GET /examples** — Exemples de questions

Retourne des exemples de questions par catégorie.

**Response :**
```json
{
  "examples": [
    {
      "category": "🚨 Fraude",
      "questions": [
        "Quelles sont les transactions frauduleuses ce mois-ci ?",
        "Quelles transactions ont un score de fraude supérieur à 0.8 ?",
        ...
      ]
    },
    ...
  ]
}
```

---

### **GET /health** — Health Check

**Response :**
```json
{
  "status": "healthy",
  "service": "text-to-sql-service",
  "version": "1.0.0",
  "database": "connected",
  "llm": "groq-llama-3.3-70b-versatile"
}
```

---

## 📦 Installation & Déploiement

### **1. Via Docker Compose (Recommandé)**

Le service est déjà intégré dans `docker-compose.yml` :

```yaml
text-to-sql-service:
  build:
    context: ./text-to-sql-service
    dockerfile: Dockerfile
  container_name: bank_chat_text2sql
  environment:
    TEXT2SQL_PORT: "8003"
    DATABASE_URL: postgresql://sql_user:sql_password@db:5432/banking_data
    SQL_MAX_ROWS: "100"
    SQL_TIMEOUT_MS: "30000"
    KEYCLOAK_URL: "http://keycloak:8080/auth"
    KEYCLOAK_REALM: "myrealm"
    KEYCLOAK_CLIENT_ID: "bank_chat"
    KEYCLOAK_ISSUER: "http://localhost/auth"
    GROQ_API_KEY: "your_groq_api_key"
  ports:
    - "8003:8003"
  depends_on:
    - db
  networks:
    - bank_network
```

**Lancement :**
```bash
docker-compose up -d text-to-sql-service
```

---

### **2. Variables d'Environnement**

| Variable             | Description                                    | Défaut                                            |
|----------------------|------------------------------------------------|---------------------------------------------------|
| `TEXT2SQL_PORT`      | Port d'écoute du service                       | `8003`                                            |
| `DATABASE_URL`       | URL PostgreSQL (banking_data)                  | `postgresql://sql_user:sql_password@db:5432/...` |
| `SQL_MAX_ROWS`       | Nombre max de lignes retournées                | `100`                                             |
| `SQL_TIMEOUT_MS`     | Timeout par requête SQL (millisecondes)        | `30000` (30s)                                     |
| `GROQ_API_KEY`       | Clé API Groq pour le LLM                       | *(requis)*                                        |
| `GROQ_MODEL`         | Modèle LLM Groq                                | `llama-3.3-70b-versatile`                         |
| `KEYCLOAK_URL`       | URL interne Keycloak                           | `http://keycloak:8080/auth`                       |
| `KEYCLOAK_REALM`     | Realm Keycloak                                 | `myrealm`                                         |
| `KEYCLOAK_CLIENT_ID` | Client ID Keycloak                             | `bank_chat`                                       |
| `KEYCLOAK_ISSUER`    | URL publique Keycloak                          | `http://localhost/auth`                           |
| `CORS_ORIGINS`       | Origines autorisées (séparées par virgules)    | `http://localhost:4200,http://localhost`          |

---

## 🧪 Tests & Validation

### **Test Manuel via cURL**

```bash
# 1. Obtenir un JWT Keycloak (utilisateur bank_agent)
TOKEN=$(curl -X POST "http://localhost/auth/realms/myrealm/protocol/openid-connect/token" \
  -d "client_id=bank_chat" \
  -d "username=agent@bank.com" \
  -d "password=password123" \
  -d "grant_type=password" \
  | jq -r '.access_token')

# 2. Appeler le service text-to-sql
curl -X POST "http://localhost/sql/query" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Combien de transactions sont bloquées ?",
    "user_id": "agent_test"
  }' | jq
```

---

## 🛠️ Maintenance & Monitoring

### **Logs**

```bash
# Logs du service text-to-sql
docker logs -f bank_chat_text2sql

# Logs enrichis avec timestamps
docker logs bank_chat_text2sql | grep -E '\[.*\]'
```

### **Métriques Clés**

- **Requêtes par minute** : `docker exec bank_chat_text2sql tail -n 1000 /proc/1/fd/1 | grep "POST /query" | wc -l`
- **Temps de réponse moyen** : Observer `duration_ms` dans les réponses
- **Taux d'erreur** : `grep "401\|403\|500" logs | wc -l`
- **Pool de connexions DB** : Monitorer via PostgreSQL `pg_stat_activity`

---

## 🚨 Limitations & Contraintes

| Contrainte                | Valeur              | Configuration          |
|---------------------------|---------------------|------------------------|
| **Lignes max/requête**    | 100                 | `SQL_MAX_ROWS`         |
| **Timeout SQL**           | 30 secondes         | `SQL_TIMEOUT_MS`       |
| **Tables accessibles**    | 3 (whitelist)       | `schema.py`            |
| **Opérations autorisées** | SELECT uniquement   | `validator.py`         |
| **Rôles autorisés**       | bank_agent, admin   | `auth.py`              |
| **Connexions DB pool**    | 2-10 connections    | `main.py` (psycopg2)   |

---

## 🔮 Améliorations Futures

- [ ] **Cache Redis** : Mise en cache des requêtes fréquentes
- [ ] **Query History** : Stockage historique des requêtes exécutées
- [ ] **Query Optimization** : Analyse et suggestion d'optimisations SQL
- [ ] **Custom Limits** : Limites configurables par utilisateur/rôle
- [ ] **Export CSV/Excel** : Export des résultats en fichiers
- [ ] **Scheduled Queries** : Exécution planifiée de requêtes récurrentes
- [ ] **Advanced Permissions** : Row-level security par département

---

## 📚 Ressources & Documentation

- **FastAPI** : https://fastapi.tiangolo.com
- **PostgreSQL** : https://www.postgresql.org/docs
- **Keycloak** : https://www.keycloak.org/documentation
- **Groq API** : https://console.groq.com/docs
- **PyJWT** : https://pyjwt.readthedocs.io

---

## 👨‍💻 Support & Contact

Pour toute question ou problème :
- **Issues** : Ouvrir un ticket GitHub
- **Email** : devops@yourbank.com
- **Documentation** : `/docs` endpoint (Swagger UI)

---

**Version** : 1.0.0  
**Dernière mise à jour** : Mai 2026  
**Statut** : ✅ Production Ready