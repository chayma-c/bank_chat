# 🧪 Plan de Tests — BankChat Microservices

> **Prérequis** : Stack démarrée (`docker-compose up -d`).
> Récupérer un token JWT Keycloak pour les rôles `bank_agent` et `client` :
> ```bash
> # Token bank_agent (remplacer les valeurs)
> TOKEN=$(curl -s -X POST http://localhost/auth/realms/myrealm/protocol/openid-connect/token \
>   -d "client_id=bank_chat&grant_type=password&username=AGENT_USER&password=AGENT_PASS" \
>   | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
>
> # Token client (rôle insuffisant — pour tester les refus)
> CLIENT_TOKEN=$(curl -s -X POST http://localhost/auth/realms/myrealm/protocol/openid-connect/token \
>   -d "client_id=bank_chat&grant_type=password&username=CLIENT_USER&password=CLIENT_PASS" \
>   | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
> ```

---

## 🔴 1. SERVICE FRAUDE (`fraud-service:8001`)

### 1.1 — Health Check (public)
**But** : Vérifier que le service est démarré.
```bash
curl http://localhost/fraud/health
```
✅ **Attendu** : `{"status": "ok", "service": "fraud-service", "version": "2.0.0"}`

---

### 1.2 — Analyse fraude nominale (IBAN valide avec transactions)
**But** : Pipeline complet : IBAN → transactions → scoring → rapport.
```bash
curl -X POST http://localhost/fraud/analyze \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "Analyse cet IBAN TN5914207207100798950584", "user_id": "agent1", "session_id": "sess-001"}'
```
✅ **Attendu** :
- `score_final` entre 0 et 100
- `risk_level` dans `[APPROVED, REVIEW, HOLD, BLOCK]`
- `llm_summary` non vide (rapport professionnel en français)
- `fraud_results` : liste des règles évaluées

---

### 1.3 — Analyse fraude : IBAN absent dans le message
**But** : Le service doit détecter l'absence d'IBAN et retourner une erreur claire.
```bash
curl -X POST http://localhost/fraud/analyze \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "Analyse les transactions suspectes", "user_id": "agent1"}'
```
✅ **Attendu** : `{"error": "L'IBAN n'a pas pu être identifié. Veuillez préciser le compte à analyser."}`

---

### 1.4 — Analyse fraude : IBAN inexistant (pas de transactions)
**But** : Comportement avec un IBAN valide syntaxiquement mais sans données.
```bash
curl -X POST http://localhost/fraud/analyze \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "Analyse IBAN_INEXISTANT999", "user_id": "agent1"}'
```
✅ **Attendu** : Réponse structurée avec `score_final: 0`, `transactions_count: 0`, `risk_level: "APPROVED"` ou erreur explicite.

---

### 1.5 — Sécurité : Accès sans token JWT
**But** : Toute requête sans Bearer token doit être rejetée.
```bash
curl -X POST http://localhost/fraud/analyze \
  -H "Content-Type: application/json" \
  -d '{"message": "test", "user_id": "hacker"}'
```
✅ **Attendu** : `401 Unauthorized` avec `{"detail": "Authorization header is missing."}`

---

### 1.6 — Sécurité : Accès avec token CLIENT (rôle insuffisant)
**But** : Un client ne peut pas déclencher une analyse de fraude.
```bash
curl -X POST http://localhost/fraud/analyze \
  -H "Authorization: Bearer $CLIENT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "Analyse IBAN_TN123", "user_id": "client1"}'
```
✅ **Attendu** : `403 Forbidden` avec `{"detail": "Access denied: 'bank_agent' or 'admin' realm role required."}`

---

### 1.7 — Export transactions (action = export_transactions)
**But** : Générer un rapport Excel pour un IBAN.
```bash
curl -X POST http://localhost/fraud/analyze \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "Exporte les transactions de IBAN_TN001", "action": "export_transactions", "user_id": "agent1"}'
```
✅ **Attendu** : `download_url` non vide, format `.xlsx` accessible.

---

### 1.8 — Decision Logs : Liste paginée
**But** : Récupérer l'historique des analyses.
```bash
curl "http://localhost/fraud/decision-logs?limit=10&offset=0" \
  -H "Authorization: Bearer $TOKEN"
```
✅ **Attendu** : `{"total": N, "logs": [...]}` avec les champs `id`, `iban`, `score_final`, `risk_level`.

---

### 1.9 — Decision Logs : Filtrage par risk_level
**But** : Filtrer les analyses à haut risque.
```bash
curl "http://localhost/fraud/decision-logs?risk_level=BLOCK" \
  -H "Authorization: Bearer $TOKEN"
```
✅ **Attendu** : Tous les logs retournés ont `risk_level == "BLOCK"`.

---

### 1.10 — Decision Logs Stats
**But** : Vérifier les métriques du dashboard.
```bash
curl http://localhost/fraud/decision-logs/stats \
  -H "Authorization: Bearer $TOKEN"
```
✅ **Attendu** : `{"total_analyses": N, "block_count": N, "tracfin_count": N, "mailed_count": N, "avg_score": X.X}`

---

## 📋 2. RÈGLES DE FRAUDE (`/fraud/rules`)

### 2.1 — Liste toutes les règles actives
```bash
curl http://localhost/fraud/rules \
  -H "Authorization: Bearer $TOKEN"
```
✅ **Attendu** : Liste JSON avec les règles par défaut (domaines : VELOCITY, LIMIT, GEOGRAPHIC, AML, BEHAVIORAL).

---

### 2.2 — Créer une nouvelle règle
**But** : Ajouter une règle VELOCITY personnalisée.
```bash
curl -X POST http://localhost/fraud/rules \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Test Règle Virement Nocturne",
    "domain": "VELOCITY",
    "trigger": "Plus de 3 virements entre 00h et 05h",
    "points": 25,
    "severity": "HIGH",
    "active": true,
    "description": "Règle de test pour les virements nocturnes"
  }'
```
✅ **Attendu** : `{"id": "...", "name": "Test Règle Virement Nocturne", "active": true}` avec status 201.

---

### 2.3 — Modifier une règle (désactivation)
**But** : Désactiver une règle existante.
```bash
# Remplacer RULE_ID par l'ID récupéré lors du GET /rules
curl -X PUT http://localhost/fraud/rules/RULE_ID \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"active": false}'
```
✅ **Attendu** : Règle retournée avec `"active": false`.

---

### 2.4 — Supprimer une règle
```bash
curl -X DELETE http://localhost/fraud/rules/RULE_ID \
  -H "Authorization: Bearer $TOKEN"
```
✅ **Attendu** : `204 No Content` ou `{"message": "deleted"}`.

---

### 2.5 — Créer une règle avec domain invalide
**But** : Tester la validation des données.
```bash
curl -X POST http://localhost/fraud/rules \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "Test", "domain": "INEXISTANT", "trigger": "...", "points": 10}'
```
✅ **Attendu** : `422 Unprocessable Entity` — validation Pydantic/DB échoue.

---

### 2.6 — Créer une règle avec points hors bornes (0-100)
```bash
curl -X POST http://localhost/fraud/rules \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "Test", "domain": "AML", "trigger": "...", "points": 150}'
```
✅ **Attendu** : `422` ou erreur DB — points doit être entre 0 et 100.

---

## 📧 3. SERVICE MAILING (`mail-service:8002`)

### 3.1 — Health Check
```bash
curl http://localhost/mail/health
```
✅ **Attendu** : `{"status": "ok", "service": "mail-service"}`

---

### 3.2 — Envoi d'alerte fraude (fraud_alert template)
**But** : Envoyer un email d'alerte pour score ≥ 50.
```bash
curl -X POST http://localhost/mail/send \
  -H "Content-Type: application/json" \
  -d '{
    "to": "compliance@yourbank.com",
    "subject": "[TEST] Alerte fraude score 65/100",
    "template": "fraud_alert",
    "context": {
      "iban": "TN5914207207100798950584",
      "score_final": 65,
      "risk_level": "HOLD",
      "tracfin_required": false,
      "llm_summary": "Test de l alerte fraude."
    },
    "iban": "TN5914207207100798950584",
    "score_final": 65,
    "risk_level": "HOLD",
    "tracfin": false,
    "session_id": "test-001",
    "user_id": "agent1"
  }'
```
✅ **Attendu** : `{"status": "sent", "id": "..."}` — email reçu sur la boîte SMTP.

---

### 3.3 — Envoi d'alerte critique (critical_alert template)
**But** : Score ≥ 80 ou TRACFIN requis.
```bash
curl -X POST http://localhost/mail/send \
  -H "Content-Type: application/json" \
  -d '{
    "to": "compliance@yourbank.com",
    "subject": "[TEST] Alerte CRITIQUE score 92/100 - TRACFIN REQUIS",
    "template": "critical_alert",
    "context": {
      "iban": "TN_CRITICAL_TEST",
      "score_final": 92,
      "risk_level": "BLOCK",
      "tracfin_required": true,
      "llm_summary": "Compte suspect - déclaration TRACFIN nécessaire."
    },
    "tracfin": true,
    "score_final": 92
  }'
```
✅ **Attendu** : `{"status": "sent"}` — email reçu avec le bon template critique.

---

### 3.4 — Vérifier le pipeline fraud → mail automatique
**But** : Déclencher une analyse dont le score dépasse 50 et vérifier que le mail est envoyé automatiquement par `mail_agent`.

1. Lancer une analyse avec un IBAN à haut risque connu.
2. Vérifier dans les logs Django : `[mail_agent] ✅ Email sent → compliance@yourbank.com`
3. Vérifier dans la BD `mail_db` (table `sent_emails`) qu'un enregistrement existe.

```bash
docker logs bank_chat_orchestrateur 2>&1 | grep "mail_agent"
```

---

### 3.5 — Envoi vers adresse invalide
```bash
curl -X POST http://localhost/mail/send \
  -H "Content-Type: application/json" \
  -d '{"to": "pas-une-adresse", "subject": "Test", "template": "fraud_alert", "context": {}}'
```
✅ **Attendu** : `{"status": "failed", "error_detail": "..."}` — erreur SMTP capturée.

---

## 🧠 4. TEXT-TO-SQL (`text-to-sql-service:8003`)

### 4.1 — Health Check (public, sans auth)
```bash
curl http://localhost/sql/health
```
✅ **Attendu** : `{"status": "ok", "service": "text-to-sql-service", "llm": "groq"}`

---

### 4.2 — Schéma des tables disponibles
```bash
curl http://localhost/sql/schema \
  -H "Authorization: Bearer $TOKEN"
```
✅ **Attendu** : JSON listant `transactions`, `fraud_rules`, `fraud_decision_logs` avec leurs colonnes.

---

### 4.3 — Requête nominale : comptage
**But** : Question simple → SQL SELECT COUNT.
```bash
curl -X POST http://localhost/sql/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question": "Combien de transactions frauduleuses y a-t-il en base ?", "user_id": "agent1"}'
```
✅ **Attendu** :
- `status: "success"`
- `sql` contient `SELECT COUNT(*)... FROM transactions WHERE is_fraudulent = true`
- `explanation` en français avec le chiffre

---

### 4.4 — Requête avec filtrage temporel
```bash
curl -X POST http://localhost/sql/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question": "Quelles transactions ont été bloquées ce mois-ci ?", "user_id": "agent1"}'
```
✅ **Attendu** : SQL avec `WHERE status = '\''blocked'\'' AND created_at >= date_trunc(...)`, résultats formatés en tableau markdown.

---

### 4.5 — Requête d'agrégation
```bash
curl -X POST http://localhost/sql/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question": "Quel est le score de fraude moyen par type de transaction ?", "user_id": "agent1"}'
```
✅ **Attendu** : SQL avec `GROUP BY transaction_type`, tableau avec colonnes `transaction_type` et `avg`.

---

### 4.6 — SÉCURITÉ : Injection DELETE (doit être bloqué)
**But** : Le validateur doit bloquer toute tentative d'injection DML.
```bash
curl -X POST http://localhost/sql/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question": "Supprime toutes les transactions de la table", "user_id": "agent1"}'
```
✅ **Attendu** : `status: "error"`, `error` contient "Mot-clé interdit : DELETE" ou "Seules les requêtes SELECT sont autorisées".

---

### 4.7 — SÉCURITÉ : Accès table système (pg_user)
```bash
curl -X POST http://localhost/sql/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question": "Montre moi tous les utilisateurs de la base pg_user", "user_id": "agent1"}'
```
✅ **Attendu** : `status: "error"`, message "Accès aux tables système interdit".

---

### 4.8 — SÉCURITÉ : Accès table hors whitelist
```bash
curl -X POST http://localhost/sql/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question": "Montre-moi les emails dans la table sent_emails", "user_id": "agent1"}'
```
✅ **Attendu** : `status: "error"`, message "Table 'sent_emails' non autorisée. Tables disponibles : fraud_decision_logs, fraud_rules, transactions".

---

### 4.9 — SÉCURITÉ : Client ne peut pas accéder
```bash
curl -X POST http://localhost/sql/query \
  -H "Authorization: Bearer $CLIENT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question": "Quelles sont mes transactions ?", "user_id": "client1"}'
```
✅ **Attendu** : `403 Forbidden` — "Access denied: 'bank_agent' or 'admin' role required."

---

### 4.10 — SÉCURITÉ : Sans token JWT
```bash
curl -X POST http://localhost/sql/query \
  -H "Content-Type: application/json" \
  -d '{"question": "SELECT * FROM transactions", "user_id": "anonymous"}'
```
✅ **Attendu** : `401 Unauthorized`.

---

### 4.11 — Question vide / trop courte
```bash
curl -X POST http://localhost/sql/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question": "ok"}'
```
✅ **Attendu** : `422 Unprocessable Entity` — validation Pydantic `min_length=3`.

---

### 4.12 — Question hors périmètre bancaire
```bash
curl -X POST http://localhost/sql/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question": "Quelle est la météo à Paris aujourd hui ?", "user_id": "agent1"}'
```
✅ **Attendu** : SQL généré du type `SELECT '\''Question non applicable...'\'' AS message;` — pas d'erreur, mais résultat explicite.

---

### 4.13 — Exemples de questions
```bash
curl http://localhost/sql/examples \
  -H "Authorization: Bearer $TOKEN"
```
✅ **Attendu** : JSON avec 6 catégories (Fraude, Anomalies, Support, Reporting, Audit, Analyse) et questions exemples.

---

## 🌐 5. MCP / SEARCH AGENT (via Chatbot)

> Ces tests s'effectuent via l'interface du chatbot (UI Angular) ou via l'API chatbot Django.

### 5.1 — Détection d'intention "search"
**But** : Le chatbot route correctement vers `search_agent`.
- **Message** : `"Quel est le cours actuel de l'euro par rapport au dollar ?"`
- **Via API** :
```bash
curl -X POST http://localhost/api/v1/chatbot/chat/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "Quel est le cours actuel de l euro par rapport au dollar ?", "session_id": "test-search-001"}'
```
✅ **Attendu** : Réponse avec `agent: "search_agent"`, contenu = résumé des résultats web avec sources citées.

---

### 5.2 — Recherche avec optimisation de requête
**But** : Le LLM optimise les mots-clés avant la recherche MCP.
- **Message** : `"Donne moi les dernières actualités sur les faillites bancaires"`
✅ **Attendu** : Log Django : `[search_agent] Optimized query: bank failures latest news 2025`

---

### 5.3 — Fallback si MCP échoue
**But** : En cas d'erreur du serveur MCP, la recherche legacy est utilisée.
```bash
# Observer les logs
docker logs bank_chat_orchestrateur 2>&1 | grep "search_agent"
```
✅ **Attendu si MCP KO** : `[search_agent] MCP call failed: ... Falling back to legacy tool.` — réponse quand même retournée.

---

### 5.4 — Question avec mot-clé "heure" / "date"
- **Message** : `"Quelle est la date d aujourd hui ?"`
✅ **Attendu** : Intent détecté = `search`, résumé avec la date actuelle.

---

## 🤝 6. AGENT SUPPORT (via Chatbot)

### 6.1 — Question support générique
```bash
curl -X POST http://localhost/api/v1/chatbot/chat/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "Ma carte bancaire est bloquée, que faire ?", "session_id": "test-support-001"}'
```
✅ **Attendu** : `agent: "support_agent"`, réponse avec les étapes à suivre, ton professionnel.

---

### 6.2 — Réinitialisation de mot de passe
- **Message** : `"J ai oublié mon mot de passe, comment le réinitialiser ?"`
✅ **Attendu** : Instructions claires sur le processus de réinitialisation.

---

### 6.3 — Question hors périmètre (fallback)
- **Message** : `"Bonjour"` ou `"Raconte-moi une blague"`
✅ **Attendu** : `agent: "fallback"`, réponse polie indiquant les domaines disponibles.

---

### 6.4 — Question compte (routing account_agent)
- **Message** : `"Quel est mon solde actuel ?"`
✅ **Attendu** : `agent: "account_agent"`, réponse centrée sur les informations de compte.

---

### 6.5 — Question virement (routing transfer_agent)
- **Message** : `"Je veux effectuer un virement de 500€ vers mon ami"`
✅ **Attendu** : `agent: "transfer_agent"`, instructions sur le processus de virement.

---

## ✅ Checklist de Vérification Globale

```bash
# 1. Vérifier que tous les containers tournent
docker-compose ps

# 2. Vérifier les logs de chaque service
docker logs bank_chat_fraud       2>&1 | tail -20
docker logs bank_chat_text2sql    2>&1 | tail -20
docker logs bank_chat_mail        2>&1 | tail -20
docker logs bank_chat_orchestrateur 2>&1 | tail -30

# 3. Vérifier la connectivité réseau entre services
docker exec bank_chat_orchestrateur python -c "import httpx; print(httpx.get('http://fraud-service:8001/health').json())"
docker exec bank_chat_orchestrateur python -c "import httpx; print(httpx.get('http://text-to-sql-service:8003/health').json())"
docker exec bank_chat_orchestrateur python -c "import httpx; print(httpx.get('http://mail-service:8002/health').json())"

# 4. Vérifier la BD banking_data
docker exec bank_chat_db psql -U sql_user -d banking_data -c "\dt"
docker exec bank_chat_db psql -U sql_user -d banking_data -c "SELECT COUNT(*) FROM transactions;"
docker exec bank_chat_db psql -U sql_user -d banking_data -c "SELECT COUNT(*) FROM fraud_rules WHERE active=true;"
```

---

## 📊 Tableau Récapitulatif

| # | Service | Cas | Résultat attendu |
|---|---------|-----|-----------------|
| 1.1 | Fraud | Health check | 200 OK |
| 1.2 | Fraud | Analyse IBAN valide | score + summary |
| 1.3 | Fraud | IBAN absent | Erreur claire |
| 1.5 | Fraud | Sans JWT | 401 |
| 1.6 | Fraud | Token CLIENT | 403 |
| 2.2 | Rules | Créer règle | 201 + id |
| 2.5 | Rules | Domain invalide | 422 |
| 3.2 | Mail | fraud_alert | sent |
| 3.5 | Mail | Adresse invalide | failed |
| 4.3 | SQL | Comptage NL | SELECT COUNT |
| 4.6 | SQL | Injection DELETE | Bloqué |
| 4.7 | SQL | pg_user | Bloqué |
| 4.8 | SQL | Table hors whitelist | Bloqué |
| 4.9 | SQL | Token CLIENT | 403 |
| 5.1 | MCP | Cours bourse | search_agent |
| 6.1 | Support | Carte bloquée | support_agent |
| 6.3 | Support | Hors périmètre | fallback |
